"""
Photo management handler.

Users can upload up to 5 photos. Each photo is stored as a Telegram file_id
in the user_photos table. Photos are shown as profile cards during browsing.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Photo, User
from bot.keyboards.reply import main_menu_keyboard
from bot.services.rating import update_user_rating
from bot.states.photos import PhotoStates

router = Router()
MAX_PHOTOS = 5


def _cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Отмена", callback_data="photo:cancel"),
    ]])


def _photo_menu_keyboard(has_photos: bool) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="📤 Добавить фото", callback_data="photo:add")]]
    if has_photos:
        rows.append([InlineKeyboardButton(text="🗑 Удалить последнее фото", callback_data="photo:delete_last")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _get_user(session: AsyncSession, telegram_id: int) -> User | None:
    return await session.scalar(select(User).where(User.telegram_id == telegram_id))


async def _photo_count(session: AsyncSession, user_db_id: int) -> int:
    return await session.scalar(
        select(func.count()).select_from(Photo).where(Photo.user_id == user_db_id)
    ) or 0


# ── Show photo menu ─────────────────────────────────────────────────────────────

@router.message(F.text == "📸 Мои фото")
async def cmd_photos(message: Message, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    user = await _get_user(session, message.from_user.id)
    if not user:
        await message.answer("Сначала зарегистрируйся через /start.")
        return

    count = await _photo_count(session, user.id)
    await message.answer(
        f"📸 <b>Мои фото</b>\n\nЗагружено: {count}/{MAX_PHOTOS}",
        parse_mode="HTML",
        reply_markup=_photo_menu_keyboard(count > 0),
    )


# ── Start upload ────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "photo:add")
async def cb_add_photo(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await callback.answer()
    user = await _get_user(session, callback.from_user.id)
    if not user:
        return

    count = await _photo_count(session, user.id)
    if count >= MAX_PHOTOS:
        await callback.message.edit_text(
            f"Максимум {MAX_PHOTOS} фотографий. Сначала удали старые.",
            reply_markup=_photo_menu_keyboard(True),
        )
        return

    await state.set_state(PhotoStates.uploading)
    await state.update_data(user_db_id=user.id)
    await callback.message.edit_text(
        "Отправь фото (до 5 МБ):",
        reply_markup=_cancel_keyboard(),
    )


# ── Receive photo ───────────────────────────────────────────────────────────────

@router.message(PhotoStates.uploading, F.photo)
async def handle_photo(message: Message, session: AsyncSession, state: FSMContext) -> None:
    data = await state.get_data()
    user_db_id: int = data["user_db_id"]

    count = await _photo_count(session, user_db_id)
    if count >= MAX_PHOTOS:
        await state.clear()
        await message.answer(
            f"Максимум {MAX_PHOTOS} фотографий достигнут.",
            reply_markup=main_menu_keyboard(),
        )
        return

    file_id = message.photo[-1].file_id  # largest available size
    session.add(Photo(user_id=user_db_id, photo_url=file_id, sort_order=count + 1))

    # Recalculate L1 rating (photo count changed)
    await update_user_rating(user_db_id, session)
    await session.commit()

    await state.clear()
    new_count = count + 1
    await message.answer(
        f"✅ Фото добавлено! Всего фото: {new_count}/{MAX_PHOTOS}",
        reply_markup=main_menu_keyboard(),
    )


# ── Non-photo message while waiting ────────────────────────────────────────────

@router.message(PhotoStates.uploading)
async def handle_not_photo(message: Message) -> None:
    await message.answer("Пожалуйста, отправь именно фото, или нажми «Отмена».")


# ── Cancel ──────────────────────────────────────────────────────────────────────

@router.callback_query(PhotoStates.uploading, F.data == "photo:cancel")
async def cb_cancel_photo(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.clear()
    await callback.message.edit_text("Добавление фото отменено.")


# ── Delete last photo ───────────────────────────────────────────────────────────

@router.callback_query(F.data == "photo:delete_last")
async def cb_delete_last(callback: CallbackQuery, session: AsyncSession) -> None:
    await callback.answer()
    user = await _get_user(session, callback.from_user.id)
    if not user:
        return

    last_photo = await session.scalar(
        select(Photo)
        .where(Photo.user_id == user.id)
        .order_by(Photo.sort_order.desc())
        .limit(1)
    )
    if not last_photo:
        await callback.message.edit_text("Фотографий нет.", reply_markup=_photo_menu_keyboard(False))
        return

    await session.delete(last_photo)
    await update_user_rating(user.id, session)
    await session.commit()

    count = await _photo_count(session, user.id)
    await callback.message.edit_text(
        f"🗑 Фото удалено. Осталось: {count}/{MAX_PHOTOS}",
        reply_markup=_photo_menu_keyboard(count > 0),
    )
