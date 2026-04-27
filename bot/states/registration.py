from aiogram.fsm.state import State, StatesGroup


class RegistrationStates(StatesGroup):
    name = State()
    age = State()
    gender = State()
    seeking_gender = State()
    city = State()
    bio = State()
    age_min = State()
    age_max = State()
