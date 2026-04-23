from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove

remove_keyboard = ReplyKeyboardRemove()


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔍 Смотреть анкеты"), KeyboardButton(text="💌 Мои мэтчи")],
            [KeyboardButton(text="👤 Моя анкета"),      KeyboardButton(text="📸 Мои фото")],
            [KeyboardButton(text="✏️ Редактировать анкету"), KeyboardButton(text="🔗 Пригласить друга")],
        ],
        resize_keyboard=True,
    )
