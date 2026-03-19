"""FSM-состояния бота."""

from aiogram.fsm.state import State, StatesGroup


class RenameRecord(StatesGroup):
    waiting_for_title = State()


class AskQuestion(StatesGroup):
    waiting_for_question = State()


class WaitingPhone(StatesGroup):
    waiting_for_phone = State()
