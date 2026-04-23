"""
Browse / swipe handler.

Flow:
  1. User taps "🔍 Смотреть анкеты".
  2. Bot pops next candidate from Redis feed (refilling when needed).
  3. Profile card shown: photo + name/age/city/bio + ❤️/👎 buttons.
  4. On ❤️ Like: card buttons replaced with "Написать сообщение?" choice.
  5. If "Написать" → user types one message → like + notification sent to target.
  6. If "Просто лайк" → like stored, notification sent without message.
  7. Target receives notification with profile card + optional message + accept/decline.
  8. If target accepts → mutual like → Match → both get Telegram username of the other.
  9. If target declines → nothing.
  10. On 👎 Skip: recorded, next card shown immediately.
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
from bot.states.browse import BrowseStates

logger = logging.getLogger(__name__)
router = Router()


# ── Keyboards ──────────────────────────────────────────────────────────────────

def _swipe_keyboard(target_user_id: int, photo_count: int = 1) -> InlineKeyboardMarkup:
    rows = [[
        InlineKeyboardButton(text="❤️  Лайк",         callback_data=f"swipe:like:{target_user_id}"),
        InlineKeyboardButton(text="👎  Пропустить",    callback_data=f"swipe:skip:{target_user_id}"),
    ]]
    if photo_count > 1:
        rows.append([
            InlineKeyboardButton(
                text=f"📸 Все фото ({photo_count})",
                callback_data=f"show_photos:{target_user_id}",
            )
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _ask_message_keyboard(target_user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✍️ Написать сообщение", callback_data=f"like_msg:yes:{target_user_id}")],
        [InlineKeyboardButton(text="👍 Просто лайк",        callback_data=f"like_msg:no:{target_user_id}")],
    ])


def _skip_msg_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="↩️ Без сообщения", callback_data="browse:skip_msg"),
    ]])


def _like_notify_keyboard(from_db_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❤️ Взаимный лайк", callback_data=f"like_accept:{from_db_id}"),
        InlineKeyboardButton(text="👎 Пропустить",    callback_data=f"like_skip:{from_db_id}"),
    ]])


# ── Formatting ─────────────────────────────────────────────────────────────────

def _card_text(profile: UserProfile, photo_count: int = 1) -> str:
    header = f"<b>{profile.name}</b>, {profile.age} лет"
    if profile.city:
        header += f" · {profile.city}"
    parts = [header]
    if profile.bio:
        parts.append(f"\n{profile.bio}")
    if photo_count > 1:
        parts.append(f"\n📸 {photo_count} фото")
    return "\n".join(parts)


# ── DB helpers ─────────────────────────────────────────────────────────────────

async def _get_user(session: AsyncSession, telegram_id: int) -> User | None:
    return await session.scalar(select(User).where(User.telegram_id == telegram_id))


# ── Core: show next profile card ───────────────────────────────────────────────

async def _show_next(
    bot: Bot,
    chat_id: int,
    viewer_db_id: int,
    session: AsyncSession,
    redis: Redis,
    old_message_id: int | None = None,
) -> None:
    if await needs_refill(redis, viewer_db_id):
        candidates = await get_ranked_candidates(viewer_db_id, session)
        if candidates:
            await push_profiles(redis, viewer_db_id, candidates)

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

    photos = list(await session.scalars(
        select(Photo)
        .where(Photo.user_id == profile.user_id)
        .order_by(Photo.sort_order)
    ))
    text = _card_text(profile, photo_count=len(photos))
    keyboard = _swipe_keyboard(profile.user_id, photo_count=len(photos))

    if photos:
        await bot.send_photo(
            chat_id, photos[0].photo_url,
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
    if not user:
        await message.answer("Сначала создай анкету через /start.")
        return
    has_profile = await session.scalar(
        select(UserProfile.user_id).where(UserProfile.user_id == user.id)
    )
    if not has_profile:
        await message.answer("Сначала создай анкету через /start.")
        return
    await _show_next(message.bot, message.chat.id, user.id, session, redis)


# ── Swipe: LIKE → ask about message ───────────────────────────────────────────

@router.callback_query(F.data.startswith("swipe:like:"))
async def process_like_btn(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    target_user_id = int(callback.data.split(":")[2])

    viewer = await _get_user(session, callback.from_user.id)
    if not viewer:
        await callback.answer()
        return

    target_profile = await session.scalar(
        select(UserProfile).where(UserProfile.user_id == target_user_id)
    )
    name = target_profile.name if target_profile else "этого человека"

    await state.update_data(
        target_user_id=target_user_id,
        viewer_db_id=viewer.id,
        card_message_id=callback.message.message_id,
    )

    prompt = f"\n\n💬 Хочешь написать сообщение <b>{name}</b>?"
    try:
        if callback.message.photo:
            existing = callback.message.caption or ""
            await callback.message.edit_caption(
                caption=existing + prompt,
                parse_mode="HTML",
                reply_markup=_ask_message_keyboard(target_user_id),
            )
        else:
            existing = callback.message.text or ""
            await callback.message.edit_text(
                existing + prompt,
                parse_mode="HTML",
                reply_markup=_ask_message_keyboard(target_user_id),
            )
    except Exception:
        pass

    await callback.answer()


# ── Swipe: SKIP ────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("swipe:skip:"))
async def process_skip_btn(
    callback: CallbackQuery,
    session: AsyncSession,
    redis: Redis,
    state: FSMContext,
) -> None:
    target_user_id = int(callback.data.split(":")[2])
    await callback.answer()

    viewer = await _get_user(session, callback.from_user.id)
    if not viewer:
        return

    await state.clear()
    await _do_skip(viewer.id, target_user_id, session)
    await _show_next(
        callback.bot,
        callback.message.chat.id,
        viewer.id,
        session,
        redis,
        old_message_id=callback.message.message_id,
    )


# ── "Написать сообщение" → YES ─────────────────────────────────────────────────

@router.callback_query(F.data.startswith("like_msg:yes:"))
async def cb_like_with_msg(callback: CallbackQuery, state: FSMContext) -> None:
    target_user_id = int(callback.data.split(":")[2])
    await callback.answer()

    await state.update_data(
        target_user_id=target_user_id,
        prompt_msg_id=callback.message.message_id,
    )
    await state.set_state(BrowseStates.typing_message)

    try:
        if callback.message.photo:
            await callback.message.edit_caption(
                caption="✍️ Напиши сообщение (до 500 символов):",
                reply_markup=_skip_msg_keyboard(),
            )
        else:
            await callback.message.edit_text(
                "✍️ Напиши сообщение (до 500 символов):",
                reply_markup=_skip_msg_keyboard(),
            )
    except Exception:
        pass


# ── "Просто лайк" → NO message ────────────────────────────────────────────────

@router.callback_query(F.data.startswith("like_msg:no:"))
async def cb_like_no_msg(
    callback: CallbackQuery,
    session: AsyncSession,
    redis: Redis,
    state: FSMContext,
) -> None:
    target_user_id = int(callback.data.split(":")[2])
    await callback.answer()

    data = await state.get_data()
    viewer_db_id: int | None = data.get("viewer_db_id")
    await state.clear()

    if not viewer_db_id:
        viewer = await _get_user(session, callback.from_user.id)
        if not viewer:
            return
        viewer_db_id = viewer.id

    await _do_like(viewer_db_id, target_user_id, session, callback.bot)
    await _show_next(
        callback.bot,
        callback.message.chat.id,
        viewer_db_id,
        session,
        redis,
        old_message_id=callback.message.message_id,
    )


# ── Receive typed message ──────────────────────────────────────────────────────

@router.message(BrowseStates.typing_message, F.text)
async def receive_like_message(
    message: Message,
    session: AsyncSession,
    redis: Redis,
    state: FSMContext,
) -> None:
    if len(message.text) > 500:
        await message.answer("Слишком длинно. Напиши не более 500 символов.")
        return

    data = await state.get_data()
    target_user_id: int = data["target_user_id"]
    viewer_db_id: int = data["viewer_db_id"]
    prompt_msg_id: int | None = data.get("prompt_msg_id")
    await state.clear()

    try:
        await message.bot.delete_message(message.chat.id, prompt_msg_id)
    except Exception:
        pass

    await _do_like(viewer_db_id, target_user_id, session, message.bot, user_message=message.text)
    await _show_next(message.bot, message.chat.id, viewer_db_id, session, redis)


# ── Non-text while waiting for message ────────────────────────────────────────

@router.message(BrowseStates.typing_message)
async def typing_not_text(message: Message) -> None:
    await message.answer("Пожалуйста, напиши текстовое сообщение, или нажми «↩️ Без сообщения».")


# ── Skip message → send just a like ───────────────────────────────────────────

@router.callback_query(BrowseStates.typing_message, F.data == "browse:skip_msg")
async def cb_skip_message(
    callback: CallbackQuery,
    session: AsyncSession,
    redis: Redis,
    state: FSMContext,
) -> None:
    await callback.answer()

    data = await state.get_data()
    target_user_id: int = data["target_user_id"]
    viewer_db_id: int = data["viewer_db_id"]
    await state.clear()

    await _do_like(viewer_db_id, target_user_id, session, callback.bot)
    await _show_next(
        callback.bot,
        callback.message.chat.id,
        viewer_db_id,
        session,
        redis,
        old_message_id=callback.message.message_id,
    )


# ── Target accepts the like notification ──────────────────────────────────────

@router.callback_query(F.data.startswith("like_accept:"))
async def cb_like_accept(callback: CallbackQuery, session: AsyncSession) -> None:
    from_db_id = int(callback.data.split(":")[1])

    viewer = await _get_user(session, callback.from_user.id)
    if not viewer:
        await callback.answer("Анкета не найдена.")
        return

    await callback.answer("❤️")
    await _do_like(viewer.id, from_db_id, session, callback.bot)

    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass


# ── Target skips the like notification ────────────────────────────────────────

@router.callback_query(F.data.startswith("like_skip:"))
async def cb_like_skip(callback: CallbackQuery) -> None:
    await callback.answer("Пропущено")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass


# ── Show all photos of a profile ──────────────────────────────────────────────

@router.callback_query(F.data.startswith("show_photos:"))
async def cb_show_photos(callback: CallbackQuery, session: AsyncSession) -> None:
    target_user_id = int(callback.data.split(":")[1])
    await callback.answer()

    from aiogram.types import InputMediaPhoto

    photos = list(await session.scalars(
        select(Photo)
        .where(Photo.user_id == target_user_id)
        .order_by(Photo.sort_order)
    ))

    if not photos:
        await callback.answer("Фото нет.", show_alert=True)
        return

    if len(photos) == 1:
        await callback.bot.send_photo(callback.message.chat.id, photos[0].photo_url)
        return

    media = [InputMediaPhoto(media=p.photo_url) for p in photos]
    await callback.bot.send_media_group(callback.message.chat.id, media)


# ── Like logic ─────────────────────────────────────────────────────────────────

async def _do_like(
    from_id: int,
    to_id: int,
    session: AsyncSession,
    bot: Bot,
    user_message: str | None = None,
) -> None:
    if await session.scalar(
        select(Like).where(Like.from_user_id == from_id, Like.to_user_id == to_id)
    ):
        return

    session.add(Like(from_user_id=from_id, to_user_id=to_id))
    session.add(RatingEvent(user_id=to_id, event_type="like_received", target_user_id=from_id))
    await session.flush()

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
        await update_user_rating(from_id, session)
    else:
        await _notify_like_received(from_id, to_id, session, bot, user_message)

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


# ── Notify target that someone liked them ──────────────────────────────────────

async def _notify_like_received(
    from_db_id: int,
    to_db_id: int,
    session: AsyncSession,
    bot: Bot,
    user_message: str | None = None,
) -> None:
    from_profile = await session.scalar(
        select(UserProfile).where(UserProfile.user_id == from_db_id)
    )
    to_user = await session.get(User, to_db_id)
    if not from_profile or not to_user:
        return

    first_photo = await session.scalar(
        select(Photo)
        .where(Photo.user_id == from_db_id)
        .order_by(Photo.sort_order)
        .limit(1)
    )

    text = "❤️ <b>Кто-то проявил к тебе интерес!</b>\n\n" + _card_text(from_profile)
    if user_message:
        text += f"\n\n💬 <i>Сообщение:</i>\n{user_message}"

    keyboard = _like_notify_keyboard(from_db_id)

    try:
        if first_photo:
            await bot.send_photo(
                to_user.telegram_id,
                first_photo.photo_url,
                caption=text,
                reply_markup=keyboard,
                parse_mode="HTML",
            )
        else:
            await bot.send_message(
                to_user.telegram_id,
                text,
                reply_markup=keyboard,
                parse_mode="HTML",
            )
    except Exception as e:
        logger.warning("Like notification failed for %s: %s", to_user.telegram_id, e)


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

    for tg_id, name, their_username in [
        (user_a.telegram_id, name_b, user_b.username),
        (user_b.telegram_id, name_a, user_a.username),
    ]:
        if their_username:
            contact = f"Напиши ему/ей: @{their_username}"
        else:
            contact = "⚠️ У этого человека нет юзернейма в Telegram. Попроси его/её написать тебе первым(ой)."

        try:
            await bot.send_message(
                tg_id,
                f"💕 <b>Мэтч!</b> <b>{name}</b> тоже оценил(а) тебя!\n\n{contact}",
                parse_mode="HTML",
            )
        except Exception as e:
            logger.warning("Match notification failed for %s: %s", tg_id, e)
