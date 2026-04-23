"""
Matches handler — shows the list of mutual likes and allows viewing each match's profile.
Also handles referral link generation.
"""
from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Match, Photo, User, UserProfile

router = Router()

GENDER_LABELS = {"male": "Мужской", "female": "Женский"}
SEEKING_LABELS = {"male": "Мужчину", "female": "Женщину", "any": "Не важно"}


async def _get_user(session: AsyncSession, telegram_id: int) -> User | None:
    return await session.scalar(select(User).where(User.telegram_id == telegram_id))


# ── Matches list ───────────────────────────────────────────────────────────────

@router.message(F.text == "💌 Мои мэтчи")
async def cmd_matches(message: Message, session: AsyncSession) -> None:
    user = await _get_user(session, message.from_user.id)
    if not user:
        await message.answer("Сначала зарегистрируйся через /start.")
        return

    result = await session.execute(
        select(Match).where(
            (Match.user_a_id == user.id) | (Match.user_b_id == user.id)
        ).order_by(Match.created_at.desc())
    )
    matches = result.scalars().all()

    if not matches:
        await message.answer("У тебя пока нет мэтчей. Продолжай листать анкеты! 😊")
        return

    rows = []
    for match in matches:
        other_id = match.user_b_id if match.user_a_id == user.id else match.user_a_id
        profile = await session.scalar(
            select(UserProfile).where(UserProfile.user_id == other_id)
        )
        if not profile:
            continue
        label = f"{profile.name}, {profile.age}"
        rows.append([InlineKeyboardButton(
            text=label,
            callback_data=f"match:view:{other_id}",
        )])

    if not rows:
        await message.answer("Анкеты твоих мэтчей удалены.")
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=rows)
    await message.answer(
        f"💌 <b>Твои мэтчи</b> ({len(rows)} чел.):",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


# ── View single match profile ──────────────────────────────────────────────────

@router.callback_query(F.data.startswith("match:view:"))
async def cb_view_match(callback: CallbackQuery, session: AsyncSession) -> None:
    other_user_id = int(callback.data.split(":")[2])
    await callback.answer()

    profile = await session.scalar(
        select(UserProfile).where(UserProfile.user_id == other_user_id)
    )
    if not profile:
        await callback.message.answer("Анкета удалена.")
        return

    other_user = await session.get(User, other_user_id)
    gender = GENDER_LABELS.get(profile.gender or "", "—")
    seeking = SEEKING_LABELS.get(profile.seeking_gender or "", "—")

    text_lines = [
        f"👤 <b>{profile.name}</b>, {profile.age} лет",
        f"⚧ {gender}  ·  ❤️ Ищет: {seeking}",
    ]
    if profile.city:
        text_lines.append(f"📍 {profile.city}")
    if profile.bio:
        text_lines.append(f"\n{profile.bio}")

    if other_user and other_user.username:
        text_lines.append(f"\n💬 Написать: @{other_user.username}")
    else:
        text_lines.append("\n⚠️ У пользователя нет юзернейма в Telegram")

    text = "\n".join(text_lines)

    photos = list(await session.scalars(
        select(Photo)
        .where(Photo.user_id == other_user_id)
        .order_by(Photo.sort_order)
    ))

    if not photos:
        await callback.message.answer(text, parse_mode="HTML")
    elif len(photos) == 1:
        await callback.message.answer_photo(photos[0].photo_url, caption=text, parse_mode="HTML")
    else:
        media = [
            InputMediaPhoto(
                media=p.photo_url,
                caption=text if i == 0 else None,
                parse_mode="HTML" if i == 0 else None,
            )
            for i, p in enumerate(photos)
        ]
        await callback.message.answer_media_group(media)


# ── Referral link ──────────────────────────────────────────────────────────────

@router.message(F.text == "🔗 Пригласить друга")
async def cmd_invite(message: Message, session: AsyncSession, bot: Bot) -> None:
    user = await _get_user(session, message.from_user.id)
    if not user:
        await message.answer("Сначала зарегистрируйся через /start.")
        return

    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start=ref_{message.from_user.id}"

    await message.answer(
        f"🔗 <b>Твоя реферальная ссылка:</b>\n{ref_link}\n\n"
        "За каждого приглашённого друга ты получаешь <b>+0.5</b> к рейтингу!",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
