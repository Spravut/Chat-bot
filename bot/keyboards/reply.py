from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove

remove_keyboard = ReplyKeyboardRemove()


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="👤 Моя анкета")],
            [KeyboardButton(text="✏️ Редактировать анкету")],
        ],
        resize_keyboard=True,
    )
