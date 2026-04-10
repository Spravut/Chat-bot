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

    result = await session.execute(
        select(User)
        .where(User.telegram_id == message.from_user.id)
        .options(selectinload(User.profile))
    )
    user = result.scalar_one_or_none()

    if user is None:
        await message.answer(
            "👋 Добро пожаловать в <b>Dating Bot</b>!\n\n"
            "Давай создадим твою анкету.\n"
            "Как тебя зовут?",
            parse_mode="HTML",
        )
        await state.set_state(RegistrationStates.name)
    else:
        name = user.profile.name if user.profile else message.from_user.first_name
        await message.answer(
            f"👋 С возвращением, <b>{name}</b>!",
            parse_mode="HTML",
            reply_markup=main_menu_keyboard(),
        )
