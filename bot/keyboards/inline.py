from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def gender_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Мужской", callback_data="gender:male"),
            InlineKeyboardButton(text="Женский", callback_data="gender:female"),
        ],
    ])


def seeking_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Мужчину", callback_data="seeking:male"),
            InlineKeyboardButton(text="Женщину", callback_data="seeking:female"),
        ],
        [
            InlineKeyboardButton(text="Не важно", callback_data="seeking:any"),
        ],
    ])


def skip_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Пропустить", callback_data="skip")],
    ])
