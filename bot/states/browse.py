from aiogram.fsm.state import State, StatesGroup


class BrowseStates(StatesGroup):
    typing_message = State()
    # Report flow: choose reason → optionally add comment → submit
    report_choosing_reason = State()
    report_adding_comment = State()
