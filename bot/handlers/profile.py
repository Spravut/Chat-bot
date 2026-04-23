"""
Profile view, edit, and rating display.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import InputMediaPhoto, Message, ReplyKeyboardRemove
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.db.models import Photo, Rating, User, UserProfile
from bot.keyboards.reply import main_menu_keyboard
from bot.states.registration import RegistrationStates

router = Router()

GENDER_LABELS = {"male": "Мужской", "female": "Женский"}
SEEKING_LABELS = {"male": "Мужчину", "female": "Женщину", "any": "Не важно"}


def _format_profile(profile: UserProfile, rating: Rating | None = None) -> str:
    gender = GENDER_LABELS.get(profile.gender or "", "—")
    seeking = SEEKING_LABELS.get(profile.seeking_gender or "", "—")
    lines = [
        f"👤 <b>Имя:</b> {profile.name or '—'}",
        f"🎂 <b>Возраст:</b> {profile.age or '—'}",
        f"⚧ <b>Пол:</b> {gender}",
        f"❤️ <b>Ищу:</b> {seeking}",
        f"📍 <b>Город:</b> {profile.city or '—'}",
    ]
    if profile.bio:
        lines.append(f"📝 <b>О себе:</b> {profile.bio}")
    if profile.age_min or profile.age_max:
        age_range = f"{profile.age_min or '?'} – {profile.age_max or '?'}"
        lines.append(f"🔢 <b>Возраст партнёра:</b> {age_range}")
    if rating is not None:
        lines.append(
            f"\n⭐ <b>Рейтинг:</b> {float(rating.level3_score):.2f} "
            f"(анкета: {float(rating.level1_score):.1f} · активность: {float(rating.level2_score):.1f})"
        )
    return "\n".join(lines)


async def _get_user_with_profile(session: AsyncSession, telegram_id: int) -> User | None:
    result = await session.execute(
        select(User)
        .where(User.telegram_id == telegram_id)
        .options(selectinload(User.profile))
    )
    return result.scalar_one_or_none()


# ── Show profile ───────────────────────────────────────────────────────────────

@router.message(F.text == "👤 Моя анкета")
async def show_profile(message: Message, session: AsyncSession) -> None:
    user = await _get_user_with_profile(session, message.from_user.id)
    if user is None or user.profile is None:
        await message.answer("Анкета не найдена. Используй /start чтобы зарегистрироваться.")
        return

    rating = await session.get(Rating, user.id)
    photos = list(await session.scalars(
        select(Photo).where(Photo.user_id == user.id).order_by(Photo.sort_order)
    ))

    profile_text = f"Твоя анкета:\n\n{_format_profile(user.profile, rating)}"

    if not photos:
        await message.answer(profile_text, parse_mode="HTML", reply_markup=main_menu_keyboard())
    elif len(photos) == 1:
        await message.answer_photo(
            photos[0].photo_url,
            caption=profile_text,
            parse_mode="HTML",
            reply_markup=main_menu_keyboard(),
        )
    else:
        media = [
            InputMediaPhoto(
                media=p.photo_url,
                caption=profile_text if i == 0 else None,
                parse_mode="HTML" if i == 0 else None,
            )
            for i, p in enumerate(photos)
        ]
        await message.answer_media_group(media)
        await message.answer(
            f"☝️ Твои фото ({len(photos)} шт.)",
            reply_markup=main_menu_keyboard(),
        )


# ── Edit profile ───────────────────────────────────────────────────────────────

@router.message(F.text == "✏️ Редактировать анкету")
async def edit_profile(message: Message, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    user = await _get_user_with_profile(session, message.from_user.id)
    if user is None:
        await message.answer("Сначала зарегистрируйся через /start.")
        return

    # Store username so _save_and_finish can update it
    # Profile is NOT deleted here — it stays intact in case user cancels.
    # _save_and_finish will replace it on completion.
    await state.update_data(username=message.from_user.username)

    await message.answer(
        "Давай заполним анкету заново.\n\n"
        "Как тебя зовут?\n\n"
        "<i>Напиши /cancel чтобы отменить и вернуться в меню.</i>",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )
    await state.set_state(RegistrationStates.name)
