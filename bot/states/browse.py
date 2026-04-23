from aiogram.fsm.state import State, StatesGroup


class BrowseStates(StatesGroup):
    typing_message = State()
