from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.db.models import User
from bot.keyboards.reply import main_menu_keyboard
from bot.states.registration import RegistrationStates

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()

    ref_telegram_id: int | None = None
    text = message.text or ""
    parts = text.split(maxsplit=1)
    if len(parts) == 2 and parts[1].startswith("ref_"):
        try:
            ref_telegram_id = int(parts[1][4:])
            if ref_telegram_id == message.from_user.id:
                ref_telegram_id = None
        except ValueError:
            pass

    result = await session.execute(
        select(User)
        .where(User.telegram_id == message.from_user.id)
        .options(selectinload(User.profile))
    )
    user = result.scalar_one_or_none()

    if user is None:
        if ref_telegram_id:
            await state.update_data(ref_telegram_id=ref_telegram_id)
        await state.update_data(username=message.from_user.username)
        await message.answer(
            "👋 Добро пожаловать в <b>Dating Bot</b>!\n\n"
            "Давай создадим твою анкету.\n"
            "Как тебя зовут?\n\n"
            "<i>Напиши /cancel чтобы отменить регистрацию.</i>",
            parse_mode="HTML",
        )
        await state.set_state(RegistrationStates.name)
    else:
        # Update username if changed
        new_username = message.from_user.username
        if user.username != new_username:
            user.username = new_username
            await session.commit()

        name = user.profile.name if user.profile else message.from_user.first_name
        await message.answer(
            f"👋 С возвращением, <b>{name}</b>!",
            parse_mode="HTML",
            reply_markup=main_menu_keyboard(),
        )
