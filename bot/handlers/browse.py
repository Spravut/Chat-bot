"""
Browse / swipe handler.

Flow:
  1. User taps "🔍 Смотреть анкеты".
  2. Bot pops the next candidate from the Redis feed (refilling if needed).
  3. A profile card is shown: photo (if any) + name/age/city/bio + ❤️/👎 buttons.
  4. On swipe the action is recorded, the rating of the target is recalculated,
     and the next card is displayed (old card deleted).
  5. Mutual like → Match created, both users notified.
"""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Like, Match, Photo, RatingEvent, User, UserProfile
from bot.services.cache import needs_refill, pop_next, push_profiles
from bot.services.rating import get_ranked_candidates, update_user_rating

logger = logging.getLogger(__name__)
router = Router()


# ── Keyboards ──────────────────────────────────────────────────────────────────

def _swipe_keyboard(target_user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❤️  Лайк",    callback_data=f"swipe:like:{target_user_id}"),
        InlineKeyboardButton(text="👎  Пропустить", callback_data=f"swipe:skip:{target_user_id}"),
    ]])


# ── Formatting ─────────────────────────────────────────────────────────────────

def _card_text(profile: UserProfile) -> str:
    header = f"<b>{profile.name}</b>, {profile.age} лет"
    if profile.city:
        header += f" · {profile.city}"
    parts = [header]
    if profile.bio:
        parts.append(f"\n{profile.bio}")
    return "\n".join(parts)


# ── DB helpers ─────────────────────────────────────────────────────────────────

async def _get_user(session: AsyncSession, telegram_id: int) -> User | None:
    return await session.scalar(
        select(User).where(User.telegram_id == telegram_id)
    )


# ── Core: show next profile card ───────────────────────────────────────────────

async def _show_next(
    bot: Bot,
    chat_id: int,
    viewer_db_id: int,
    session: AsyncSession,
    redis: Redis,
    old_message_id: int | None = None,
) -> None:
    # Refill the Redis queue when it runs low
    if await needs_refill(redis, viewer_db_id):
        candidates = await get_ranked_candidates(viewer_db_id, session)
        if candidates:
            await push_profiles(redis, viewer_db_id, candidates)

    # Try up to 5 pops — skip profiles whose accounts were deleted
    profile: UserProfile | None = None
    for _ in range(5):
        profile_id = await pop_next(redis, viewer_db_id)
        if profile_id is None:
            break
        profile = await session.scalar(
            select(UserProfile).where(UserProfile.user_id == profile_id)
        )
        if profile:
            break
        profile = None

    # Delete old card (best-effort)
    if old_message_id:
        try:
            await bot.delete_message(chat_id, old_message_id)
        except Exception:
            pass

    if profile is None:
        await bot.send_message(
            chat_id,
            "😔 Анкеты закончились. Загляни позже — скоро появятся новые!",
        )
        return

    first_photo = await session.scalar(
        select(Photo)
        .where(Photo.user_id == profile.user_id)
        .order_by(Photo.sort_order)
        .limit(1)
    )
    text = _card_text(profile)
    keyboard = _swipe_keyboard(profile.user_id)

    if first_photo:
        await bot.send_photo(
            chat_id, first_photo.photo_url,
            caption=text, reply_markup=keyboard, parse_mode="HTML",
        )
    else:
        await bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode="HTML")


# ── Entry point ────────────────────────────────────────────────────────────────

@router.message(F.text == "🔍 Смотреть анкеты")
async def cmd_browse(
    message: Message,
    session: AsyncSession,
    redis: Redis,
    state: FSMContext,
) -> None:
    await state.clear()
    user = await _get_user(session, message.from_user.id)
    if not user or not user.profile:
        await message.answer("Сначала создай анкету через /start.")
        return
    await _show_next(message.bot, message.chat.id, user.id, session, redis)


# ── Swipe callback ─────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("swipe:"))
async def process_swipe(
    callback: CallbackQuery,
    session: AsyncSession,
    redis: Redis,
) -> None:
    _, action, target_id_str = callback.data.split(":")
    target_user_id = int(target_id_str)

    await callback.answer()

    viewer = await _get_user(session, callback.from_user.id)
    if not viewer:
        return

    if action == "like":
        await _do_like(viewer.id, target_user_id, session, callback.bot)
    else:
        await _do_skip(viewer.id, target_user_id, session)

    await _show_next(
        callback.bot,
        callback.message.chat.id,
        viewer.id,
        session,
        redis,
        old_message_id=callback.message.message_id,
    )


# ── Like logic ─────────────────────────────────────────────────────────────────

async def _do_like(
    from_id: int,
    to_id: int,
    session: AsyncSession,
    bot: Bot,
) -> None:
    # Idempotent
    if await session.scalar(
        select(Like).where(Like.from_user_id == from_id, Like.to_user_id == to_id)
    ):
        return

    session.add(Like(from_user_id=from_id, to_user_id=to_id))
    session.add(RatingEvent(user_id=to_id, event_type="like_received", target_user_id=from_id))

    # Check for mutual like
    mutual = await session.scalar(
        select(Like).where(Like.from_user_id == to_id, Like.to_user_id == from_id)
    )
    if mutual:
        a_id, b_id = min(from_id, to_id), max(from_id, to_id)
        if not await session.scalar(
            select(Match).where(Match.user_a_id == a_id, Match.user_b_id == b_id)
        ):
            session.add(Match(user_a_id=a_id, user_b_id=b_id))
            await session.flush()
            await _notify_match(from_id, to_id, session, bot)

    await update_user_rating(to_id, session)
    await session.commit()


# ── Skip logic ─────────────────────────────────────────────────────────────────

async def _do_skip(from_id: int, to_id: int, session: AsyncSession) -> None:
    session.add_all([
        RatingEvent(user_id=from_id, event_type="skipped",       target_user_id=to_id),
        RatingEvent(user_id=to_id,   event_type="skip_received", target_user_id=from_id),
    ])
    await update_user_rating(to_id, session)
    await session.commit()


# ── Match notification ─────────────────────────────────────────────────────────

async def _notify_match(
    user_a_db: int,
    user_b_db: int,
    session: AsyncSession,
    bot: Bot,
) -> None:
    user_a = await session.get(User, user_a_db)
    user_b = await session.get(User, user_b_db)
    if not user_a or not user_b:
        return

    profile_a = await session.scalar(select(UserProfile).where(UserProfile.user_id == user_a_db))
    profile_b = await session.scalar(select(UserProfile).where(UserProfile.user_id == user_b_db))
    name_a = profile_a.name if profile_a else "Кто-то"
    name_b = profile_b.name if profile_b else "Кто-то"

    for tg_id, name in [(user_a.telegram_id, name_b), (user_b.telegram_id, name_a)]:
        try:
            await bot.send_message(
                tg_id,
                f"💕 <b>Мэтч!</b> <b>{name}</b> тоже лайкнул(а) тебя!\n"
                "Теперь можете написать друг другу.",
                parse_mode="HTML",
            )
        except Exception as e:
            logger.warning("Match notification failed for %s: %s", tg_id, e)
