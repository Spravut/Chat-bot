from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import User, UserProfile
from bot.keyboards.inline import gender_keyboard, seeking_keyboard, skip_keyboard
from bot.keyboards.reply import main_menu_keyboard
from bot.states.registration import RegistrationStates

router = Router()

GENDER_LABELS = {"male": "Мужской", "female": "Женский"}
SEEKING_LABELS = {"male": "Мужчину", "female": "Женщину", "any": "Не важно"}


# ── Шаг 1: имя ────────────────────────────────────────────────────────────────

@router.message(RegistrationStates.name)
async def process_name(message: Message, state: FSMContext) -> None:
    name = message.text.strip() if message.text else ""
    if not (2 <= len(name) <= 50):
        await message.answer("Имя должно быть от 2 до 50 символов. Попробуй ещё раз.")
        return

    await state.update_data(name=name)
    await message.answer(f"Отлично, <b>{name}</b>! Сколько тебе лет?", parse_mode="HTML")
    await state.set_state(RegistrationStates.age)


# ── Шаг 2: возраст ────────────────────────────────────────────────────────────

@router.message(RegistrationStates.age)
async def process_age(message: Message, state: FSMContext) -> None:
    try:
        age = int(message.text.strip())
        if not (16 <= age <= 100):
            raise ValueError
    except (ValueError, AttributeError):
        await message.answer("Укажи возраст числом (от 16 до 100).")
        return

    await state.update_data(age=age)
    await message.answer("Укажи свой пол:", reply_markup=gender_keyboard())
    await state.set_state(RegistrationStates.gender)


# ── Шаг 3: пол ────────────────────────────────────────────────────────────────

@router.callback_query(RegistrationStates.gender, F.data.startswith("gender:"))
async def process_gender(callback: CallbackQuery, state: FSMContext) -> None:
    gender = callback.data.split(":")[1]
    await state.update_data(gender=gender)
    await callback.answer()
    await callback.message.edit_text(
        f"Пол: {GENDER_LABELS[gender]} ✓\n\nКого ищешь?",
        reply_markup=seeking_keyboard(),
    )
    await state.set_state(RegistrationStates.seeking_gender)


# ── Шаг 4: кого ищет ──────────────────────────────────────────────────────────

@router.callback_query(RegistrationStates.seeking_gender, F.data.startswith("seeking:"))
async def process_seeking(callback: CallbackQuery, state: FSMContext) -> None:
    seeking = callback.data.split(":")[1]
    await state.update_data(seeking_gender=seeking)
    await callback.answer()
    await callback.message.edit_text(
        f"Ищу: {SEEKING_LABELS[seeking]} ✓\n\nИз какого ты города?"
    )
    await state.set_state(RegistrationStates.city)


# ── Шаг 5: город ──────────────────────────────────────────────────────────────

@router.message(RegistrationStates.city)
async def process_city(message: Message, state: FSMContext) -> None:
    city = message.text.strip() if message.text else ""
    if not (2 <= len(city) <= 100):
        await message.answer("Укажи корректное название города.")
        return

    await state.update_data(city=city)
    await message.answer(
        "Расскажи немного о себе (или нажми «Пропустить»):",
        reply_markup=skip_keyboard(),
    )
    await state.set_state(RegistrationStates.bio)


# ── Шаг 6: bio (текст) ────────────────────────────────────────────────────────

@router.message(RegistrationStates.bio)
async def process_bio(message: Message, state: FSMContext, session: AsyncSession) -> None:
    bio = message.text.strip() if message.text else None
    if bio and len(bio) > 500:
        await message.answer("Слишком длинно. Напиши не более 500 символов.")
        return
    await _save_and_finish(message, state, session, bio, message.from_user.id)


# ── Шаг 6: bio (пропуск) ──────────────────────────────────────────────────────

@router.callback_query(RegistrationStates.bio, F.data == "skip")
async def skip_bio(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    await callback.answer()
    await callback.message.edit_text("О себе: пропущено ✓")
    await _save_and_finish(callback.message, state, session, None, callback.from_user.id)


# ── Сохранение в БД ───────────────────────────────────────────────────────────

async def _save_and_finish(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    bio: str | None,
    telegram_id: int,
) -> None:
    data = await state.get_data()
    await state.clear()

    user = User(telegram_id=telegram_id)
    session.add(user)
    await session.flush()  # получаем user.id

    profile = UserProfile(
        user_id=user.id,
        name=data["name"],
        age=data["age"],
        gender=data["gender"],
        seeking_gender=data["seeking_gender"],
        city=data["city"],
        bio=bio,
    )
    session.add(profile)
    await session.commit()

    await message.answer(
        f"🎉 Анкета создана!\n\n{_format_profile(profile)}",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(),
    )


def _format_profile(profile: UserProfile) -> str:
    gender = GENDER_LABELS.get(profile.gender or "", "—")
    seeking = SEEKING_LABELS.get(profile.seeking_gender or "", "—")
    lines = [
        f"👤 <b>Имя:</b> {profile.name}",
        f"🎂 <b>Возраст:</b> {profile.age}",
        f"⚧ <b>Пол:</b> {gender}",
        f"❤️ <b>Ищу:</b> {seeking}",
        f"📍 <b>Город:</b> {profile.city}",
    ]
    if profile.bio:
        lines.append(f"📝 <b>О себе:</b> {profile.bio}")
    return "\n".join(lines)
