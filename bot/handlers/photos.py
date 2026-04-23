"""
Photo management handler.

Users can upload up to 5 photos. Each photo is stored as a Telegram file_id.
Management: view all photos, add, delete specific photo, reorder with ⬆️/⬇️ buttons.

After every change the photo preview is automatically refreshed in-place:
  old preview messages + old management message are deleted, fresh ones sent.
"""
from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Photo, User
from bot.keyboards.reply import main_menu_keyboard
from bot.services.rating import update_user_rating
from bot.states.photos import PhotoStates

router = Router()
MAX_PHOTOS = 5


# ── Keyboards ───────────────────────────────────────────────────────────────────

def _cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Отмена", callback_data="photo:cancel"),
    ]])


def _photo_mgmt_keyboard(photos: list[Photo], count: int) -> InlineKeyboardMarkup:
    rows = []
    for i, photo in enumerate(photos):
        row = [InlineKeyboardButton(
            text=f"🗑 Фото {i + 1}",
            callback_data=f"photo:del:{photo.id}",
        )]
        if i > 0:
            row.append(InlineKeyboardButton(text="⬆️", callback_data=f"photo:up:{photo.id}"))
        if i < len(photos) - 1:
            row.append(InlineKeyboardButton(text="⬇️", callback_data=f"photo:down:{photo.id}"))
        rows.append(row)

    rows.append([InlineKeyboardButton(text="📤 Добавить фото", callback_data="photo:add")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ── DB helpers ──────────────────────────────────────────────────────────────────

async def _get_user(session: AsyncSession, telegram_id: int) -> User | None:
    return await session.scalar(select(User).where(User.telegram_id == telegram_id))


async def _get_photos(session: AsyncSession, user_db_id: int) -> list[Photo]:
    result = await session.scalars(
        select(Photo).where(Photo.user_id == user_db_id).order_by(Photo.sort_order)
    )
    return list(result)


async def _renumber(session: AsyncSession, user_db_id: int) -> None:
    photos = await _get_photos(session, user_db_id)
    for i, p in enumerate(photos, 1):
        p.sort_order = i


# ── Refresh helper: delete old messages, send fresh ones ───────────────────────

async def _refresh_photo_view(
    bot: Bot,
    chat_id: int,
    user_db_id: int,
    session: AsyncSession,
    state: FSMContext,
    old_mgmt_msg_id: int,
) -> None:
    data = await state.get_data()

    # Delete old photo preview messages
    for mid in data.get("photo_preview_ids", []):
        try:
            await bot.delete_message(chat_id, mid)
        except Exception:
            pass

    # Delete old management message
    try:
        await bot.delete_message(chat_id, old_mgmt_msg_id)
    except Exception:
        pass

    photos = await _get_photos(session, user_db_id)
    count = len(photos)

    # Send fresh previews
    new_preview_ids: list[int] = []
    if photos:
        if count == 1:
            sent = await bot.send_photo(chat_id, photos[0].photo_url, caption="Фото 1")
            new_preview_ids = [sent.message_id]
        else:
            media = [
                InputMediaPhoto(media=p.photo_url, caption=f"Фото {i + 1}")
                for i, p in enumerate(photos)
            ]
            sent_msgs = await bot.send_media_group(chat_id, media)
            new_preview_ids = [m.message_id for m in sent_msgs]

    # Send fresh management message
    await bot.send_message(
        chat_id,
        f"📸 <b>Мои фото</b>\n\nЗагружено: {count}/{MAX_PHOTOS}"
        + ("" if photos else "\nФотографий пока нет."),
        parse_mode="HTML",
        reply_markup=_photo_mgmt_keyboard(photos, count),
    )

    await state.update_data(photo_preview_ids=new_preview_ids)


# ── Show photo menu ─────────────────────────────────────────────────────────────

@router.message(F.text == "📸 Мои фото")
async def cmd_photos(message: Message, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    user = await _get_user(session, message.from_user.id)
    if not user:
        await message.answer("Сначала зарегистрируйся через /start.")
        return

    photos = await _get_photos(session, user.id)
    count = len(photos)

    preview_ids: list[int] = []
    if photos:
        if count == 1:
            sent = await message.answer_photo(photos[0].photo_url, caption="Фото 1")
            preview_ids = [sent.message_id]
        else:
            media = [
                InputMediaPhoto(media=p.photo_url, caption=f"Фото {i + 1}")
                for i, p in enumerate(photos)
            ]
            sent_msgs = await message.answer_media_group(media)
            preview_ids = [m.message_id for m in sent_msgs]

    await message.answer(
        f"📸 <b>Мои фото</b>\n\nЗагружено: {count}/{MAX_PHOTOS}"
        + ("" if photos else "\nФотографий пока нет."),
        parse_mode="HTML",
        reply_markup=_photo_mgmt_keyboard(photos, count),
    )
    await state.update_data(photo_preview_ids=preview_ids)


# ── Start upload ────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "photo:add")
async def cb_add_photo(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await callback.answer()
    user = await _get_user(session, callback.from_user.id)
    if not user:
        return

    photos = await _get_photos(session, user.id)
    if len(photos) >= MAX_PHOTOS:
        await callback.message.edit_text(
            f"📸 <b>Мои фото</b>\n\nМаксимум {MAX_PHOTOS} фотографий. Сначала удали лишние.",
            parse_mode="HTML",
            reply_markup=_photo_mgmt_keyboard(photos, len(photos)),
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

    photos = await _get_photos(session, user_db_id)
    if len(photos) >= MAX_PHOTOS:
        await state.clear()
        await message.answer(
            f"Максимум {MAX_PHOTOS} фотографий достигнут.",
            reply_markup=main_menu_keyboard(),
        )
        return

    file_id = message.photo[-1].file_id
    session.add(Photo(user_id=user_db_id, photo_url=file_id, sort_order=len(photos) + 1))
    await update_user_rating(user_db_id, session)
    await session.commit()

    await state.clear()
    new_count = len(photos) + 1
    await message.answer(
        f"✅ Фото {new_count}/{MAX_PHOTOS} добавлено! Нажми «📸 Мои фото» чтобы управлять фотографиями.",
        reply_markup=main_menu_keyboard(),
    )


# ── Non-photo message while waiting ────────────────────────────────────────────

@router.message(PhotoStates.uploading)
async def handle_not_photo(message: Message) -> None:
    await message.answer("Пожалуйста, отправь именно фото, или нажми «Отмена».")


# ── Cancel upload ───────────────────────────────────────────────────────────────

@router.callback_query(PhotoStates.uploading, F.data == "photo:cancel")
async def cb_cancel_photo(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    await callback.answer()
    await state.clear()
    user = await _get_user(session, callback.from_user.id)
    if not user:
        await callback.message.edit_text("Добавление отменено.")
        return

    photos = await _get_photos(session, user.id)
    count = len(photos)
    await callback.message.edit_text(
        f"📸 <b>Мои фото</b>\n\nЗагружено: {count}/{MAX_PHOTOS}"
        + ("" if photos else "\nФотографий пока нет."),
        parse_mode="HTML",
        reply_markup=_photo_mgmt_keyboard(photos, count),
    )


# ── Delete specific photo ───────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("photo:del:"))
async def cb_delete_photo(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    photo_id = int(callback.data.split(":")[2])
    user = await _get_user(session, callback.from_user.id)
    if not user:
        await callback.answer()
        return

    photo = await session.get(Photo, photo_id)
    if not photo or photo.user_id != user.id:
        await callback.answer("Фото не найдено.")
        return

    await session.delete(photo)
    await session.flush()
    await _renumber(session, user.id)
    await update_user_rating(user.id, session)
    await session.commit()

    await callback.answer("🗑 Фото удалено")
    await _refresh_photo_view(
        callback.bot,
        callback.message.chat.id,
        user.id,
        session,
        state,
        callback.message.message_id,
    )


# ── Move photo up ───────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("photo:up:"))
async def cb_photo_up(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    photo_id = int(callback.data.split(":")[2])
    user = await _get_user(session, callback.from_user.id)
    if not user:
        await callback.answer()
        return

    photo = await session.get(Photo, photo_id)
    if not photo or photo.user_id != user.id or photo.sort_order <= 1:
        await callback.answer()
        return

    prev = await session.scalar(
        select(Photo).where(
            Photo.user_id == user.id,
            Photo.sort_order == photo.sort_order - 1,
        )
    )
    if prev:
        old_a, old_b = photo.sort_order, prev.sort_order
        photo.sort_order = 100  # temp value outside 1-5 range
        await session.flush()
        prev.sort_order = old_a
        await session.flush()
        photo.sort_order = old_b
        await session.commit()

    await callback.answer("⬆️")
    await _refresh_photo_view(
        callback.bot,
        callback.message.chat.id,
        user.id,
        session,
        state,
        callback.message.message_id,
    )


# ── Move photo down ─────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("photo:down:"))
async def cb_photo_down(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    photo_id = int(callback.data.split(":")[2])
    user = await _get_user(session, callback.from_user.id)
    if not user:
        await callback.answer()
        return

    photo = await session.get(Photo, photo_id)
    if not photo or photo.user_id != user.id:
        await callback.answer()
        return

    next_photo = await session.scalar(
        select(Photo).where(
            Photo.user_id == user.id,
            Photo.sort_order == photo.sort_order + 1,
        )
    )
    if next_photo:
        old_a, old_b = photo.sort_order, next_photo.sort_order
        photo.sort_order = 100  # temp value outside 1-5 range
        await session.flush()
        next_photo.sort_order = old_a
        await session.flush()
        photo.sort_order = old_b
        await session.commit()

    await callback.answer("⬇️")
    await _refresh_photo_view(
        callback.bot,
        callback.message.chat.id,
        user.id,
        session,
        state,
        callback.message.message_id,
    )
