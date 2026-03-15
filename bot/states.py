"""FSM-состояния бота."""

from aiogram.fsm.state import State, StatesGroup


class RenameRecord(StatesGroup):
    waiting_for_title = State()
