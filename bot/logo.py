"""Отправка сообщений с логотипом. Кэширует file_id после первой отправки."""

import os

from aiogram.types import FSInputFile, Message

LOGO_PATH = os.path.join(os.path.dirname(__file__), "..", "images", "dasha-main-logo.png")
_logo_exists = os.path.exists(LOGO_PATH)
_cached_file_id: str | None = None


def _get_photo():
    """Вернуть file_id из кэша или FSInputFile для первой загрузки."""
    if _cached_file_id:
        return _cached_file_id
    return FSInputFile(LOGO_PATH)


def _cache_from_message(msg: Message) -> None:
    """Сохранить file_id из ответа Telegram в кэш."""
    global _cached_file_id
    if _cached_file_id is None and msg.photo:
        _cached_file_id = msg.photo[-1].file_id


async def send_logo(message: Message, text: str, reply_markup=None, parse_mode=None) -> Message:
    """Отправить сообщение с логотипом (photo + caption)."""
    if _logo_exists and len(text) <= 1024:
        sent = await message.answer_photo(
            photo=_get_photo(),
            caption=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
        _cache_from_message(sent)
        return sent
    return await message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)


async def edit_or_send_logo(message: Message, text: str, reply_markup=None, parse_mode=None) -> None:
    """Удалить старое сообщение и отправить новое с логотипом."""
    try:
        await message.delete()
    except Exception:
        pass
    try:
        await send_logo(message, text, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception:
        try:
            await message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
        except Exception:
            pass
