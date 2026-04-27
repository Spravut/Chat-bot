from aiogram import Router, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, ReplyKeyboardRemove
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Referral, User, UserProfile
from bot.keyboards.inline import gender_keyboard, seeking_keyboard, skip_keyboard
from bot.keyboards.reply import main_menu_keyboard
from bot.services.rating import update_user_rating
from bot.states.registration import RegistrationStates

router = Router()

GENDER_LABELS = {"male": "Мужской", "female": "Женский"}
SEEKING_LABELS = {"male": "Мужчину", "female": "Женщину", "any": "Не важно"}


# ── Cancel (must be registered FIRST so it runs before text-step handlers) ─────

@router.message(StateFilter(RegistrationStates), Command("cancel"))
async def cancel_registration(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    await state.clear()

    user = await session.scalar(select(User).where(User.telegram_id == message.from_user.id))
    if user:
        profile = await session.scalar(
            select(UserProfile).where(UserProfile.user_id == user.id)
        )
        if profile:
            await message.answer(
                "✅ Редактирование отменено. Твоя анкета осталась без изменений.",
                reply_markup=main_menu_keyboard(),
            )
            return

    await message.answer(
        "❌ Регистрация отменена.\n\nНапиши /start чтобы зарегистрироваться.",
        reply_markup=ReplyKeyboardRemove(),
    )


# ── Step 1: name ───────────────────────────────────────────────────────────────

@router.message(RegistrationStates.name)
async def process_name(message: Message, state: FSMContext) -> None:
    name = message.text.strip() if message.text else ""
    if not (2 <= len(name) <= 50):
        await message.answer("Имя должно быть от 2 до 50 символов. Попробуй ещё раз.")
        return

    await state.update_data(name=name)
    await message.answer(f"Отлично, <b>{name}</b>! Сколько тебе лет?", parse_mode="HTML")
    await state.set_state(RegistrationStates.age)


# ── Step 2: age ────────────────────────────────────────────────────────────────

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


# ── Step 3: gender ─────────────────────────────────────────────────────────────

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


# ── Step 4: seeking gender ─────────────────────────────────────────────────────

@router.callback_query(RegistrationStates.seeking_gender, F.data.startswith("seeking:"))
async def process_seeking(callback: CallbackQuery, state: FSMContext) -> None:
    seeking = callback.data.split(":")[1]
    await state.update_data(seeking_gender=seeking)
    await callback.answer()
    await callback.message.edit_text(
        f"Ищу: {SEEKING_LABELS[seeking]} ✓\n\nИз какого ты города?"
    )
    await state.set_state(RegistrationStates.city)


# ── Step 5: city ───────────────────────────────────────────────────────────────

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


# ── Step 6: bio (text) ─────────────────────────────────────────────────────────

@router.message(RegistrationStates.bio)
async def process_bio(message: Message, state: FSMContext) -> None:
    bio = message.text.strip() if message.text else None
    if bio and len(bio) > 500:
        await message.answer("Слишком длинно. Напиши не более 500 символов.")
        return
    await state.update_data(bio=bio)
    await message.answer("С какого возраста ищешь партнёра? (например: 18)")
    await state.set_state(RegistrationStates.age_min)


# ── Step 6: bio (skip) ─────────────────────────────────────────────────────────

@router.callback_query(RegistrationStates.bio, F.data == "skip")
async def skip_bio(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await callback.message.edit_text("О себе: пропущено ✓")
    await state.update_data(bio=None)
    await callback.message.answer("С какого возраста ищешь партнёра? (например: 18)")
    await state.set_state(RegistrationStates.age_min)


# ── Step 7: age_min ────────────────────────────────────────────────────────────

@router.message(RegistrationStates.age_min)
async def process_age_min(message: Message, state: FSMContext) -> None:
    try:
        age_min = int(message.text.strip())
        if not (16 <= age_min <= 100):
            raise ValueError
    except (ValueError, AttributeError):
        await message.answer("Укажи возраст числом (от 16 до 100).")
        return

    await state.update_data(age_min=age_min)
    await message.answer("До какого возраста? (например: 30)")
    await state.set_state(RegistrationStates.age_max)


# ── Step 8: age_max ────────────────────────────────────────────────────────────

@router.message(RegistrationStates.age_max)
async def process_age_max(message: Message, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    age_min = data.get("age_min", 16)
    try:
        age_max = int(message.text.strip())
        if not (age_min <= age_max <= 100):
            raise ValueError
    except (ValueError, AttributeError):
        await message.answer(f"Укажи возраст числом (от {age_min} до 100).")
        return

    await state.update_data(age_max=age_max)
    bio = data.get("bio")
    await _save_and_finish(message, state, session, bio, message.from_user.id)


# ── Save to DB ─────────────────────────────────────────────────────────────────

async def _save_and_finish(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    bio: str | None,
    telegram_id: int,
) -> None:
    data = await state.get_data()
    ref_telegram_id: int | None = data.get("ref_telegram_id")
    username: str | None = data.get("username")
    age_min: int | None = data.get("age_min")
    age_max: int | None = data.get("age_max")
    await state.clear()

    user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
    is_new = user is None

    if is_new:
        user = User(telegram_id=telegram_id, username=username)
        session.add(user)
        await session.flush()
    else:
        if username is not None:
            user.username = username
        existing_profile = await session.scalar(
            select(UserProfile).where(UserProfile.user_id == user.id)
        )
        if existing_profile:
            await session.delete(existing_profile)
            await session.flush()

    profile = UserProfile(
        user_id=user.id,
        name=data["name"],
        age=data["age"],
        gender=data["gender"],
        seeking_gender=data["seeking_gender"],
        city=data["city"],
        bio=bio,
        age_min=age_min,
        age_max=age_max,
    )
    session.add(profile)
    await session.flush()

    await update_user_rating(user.id, session)

    if is_new and ref_telegram_id:
        inviter = await session.scalar(
            select(User).where(User.telegram_id == ref_telegram_id)
        )
        if inviter and inviter.id != user.id:
            already = await session.scalar(
                select(Referral).where(Referral.referred_user_id == user.id)
            )
            if not already:
                session.add(Referral(inviter_user_id=inviter.id, referred_user_id=user.id))
                await session.flush()
                await update_user_rating(inviter.id, session)

    await session.commit()

    verb = "создана" if is_new else "обновлена"
    await message.answer(
        f"🎉 Анкета {verb}!\n\n{_format_profile(profile)}",
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
    if profile.age_min and profile.age_max:
        lines.append(f"🔞 <b>Возраст партнёра:</b> {profile.age_min}–{profile.age_max}")
    return "\n".join(lines)
