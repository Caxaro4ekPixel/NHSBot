from aiogram.fsm.state import State, StatesGroup


class RealesState(StatesGroup):
    choosing = State()


class SetReales(StatesGroup):
    PickRelease = State()
    PickTranslator = State()
    PickVoice = State()
    PickTiming = State()
    PickCurator = State()
    PickDesigner = State()