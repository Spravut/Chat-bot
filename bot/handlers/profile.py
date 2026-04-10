from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.db.models import User, UserProfile
from bot.keyboards.reply import main_menu_keyboard
from bot.states.registration import RegistrationStates

router = Router()

GENDER_LABELS = {"male": "Мужской", "female": "Женский"}
SEEKING_LABELS = {"male": "Мужчину", "female": "Женщину", "any": "Не важно"}


def _format_profile(profile: UserProfile) -> str:
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
    return "\n".join(lines)


async def _get_user_with_profile(session: AsyncSession, telegram_id: int) -> User | None:
    result = await session.execute(
        select(User)
        .where(User.telegram_id == telegram_id)
        .options(selectinload(User.profile))
    )
    return result.scalar_one_or_none()


# ── Показать анкету ────────────────────────────────────────────────────────────

@router.message(F.text == "👤 Моя анкета")
async def show_profile(message: Message, session: AsyncSession) -> None:
    user = await _get_user_with_profile(session, message.from_user.id)

    if user is None or user.profile is None:
        await message.answer(
            "Анкета не найдена. Используй /start чтобы зарегистрироваться."
        )
        return

    await message.answer(
        f"Твоя анкета:\n\n{_format_profile(user.profile)}",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(),
    )


# ── Редактировать анкету ───────────────────────────────────────────────────────

@router.message(F.text == "✏️ Редактировать анкету")
async def edit_profile(message: Message, session: AsyncSession, state: FSMContext) -> None:
    user = await _get_user_with_profile(session, message.from_user.id)

    if user is None:
        await message.answer("Сначала зарегистрируйся через /start.")
        return

    # Удаляем старый профиль и запускаем регистрацию заново
    if user.profile:
        await session.delete(user)
        await session.commit()

    await message.answer(
        "Давай заполним анкету заново.\n\nКак тебя зовут?",
        reply_markup=__import__("aiogram").types.ReplyKeyboardRemove(),
    )
    await state.set_state(RegistrationStates.name)
