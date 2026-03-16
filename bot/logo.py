"""Отправка сообщений с изображениями. Кэширует file_id после первой отправки."""

import logging
import os

from aiogram.types import FSInputFile, Message

logger = logging.getLogger(__name__)

IMAGES_DIR = os.path.join(os.path.dirname(__file__), "..", "images")

# Маппинг имён изображений на файлы
IMAGE_FILES = {
    "logo": "dasha-main-logo.png",
    "voice_message": "dasha-voice-message.jpg",
    "send_file": "dasha-send-file.jpg",
    "invite_friend": "dasha-invite-friend.jpg",
    "faq": "dasha-faq.jpg",
    "my_notes": "dasha-my-notes.jpg",
    "payments": "dasha-payments.jpg",
    "time_limit": "dasha-time-limit.jpg",
    "error": "dasha_error.jpg",
}

# Кэш file_id по имени изображения
_cached_file_ids: dict[str, str] = {}


def _get_image_path(image_name: str) -> str | None:
    """Получить путь к файлу изображения."""
    filename = IMAGE_FILES.get(image_name)
    if not filename:
        return None
    path = os.path.join(IMAGES_DIR, filename)
    return path if os.path.exists(path) else None


def _get_photo(image_name: str = "logo"):
    """Вернуть file_id из кэша или FSInputFile для первой загрузки."""
    cached = _cached_file_ids.get(image_name)
    if cached:
        return cached
    path = _get_image_path(image_name)
    if path:
        return FSInputFile(path)
    return None


def _cache_from_message(image_name: str, msg: Message) -> None:
    """Сохранить file_id из ответа Telegram в кэш."""
    if image_name not in _cached_file_ids and msg.photo:
        _cached_file_ids[image_name] = msg.photo[-1].file_id


async def send_logo(
    message: Message,
    text: str,
    reply_markup=None,
    parse_mode=None,
    image: str = "logo",
) -> Message:
    """Отправить сообщение с изображением (photo + caption)."""
    photo = _get_photo(image)
    if photo and len(text) <= 1024:
        sent = await message.answer_photo(
            photo=photo,
            caption=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
        _cache_from_message(image, sent)
        return sent
    return await message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)


async def edit_or_send_logo(
    message: Message,
    text: str,
    reply_markup=None,
    parse_mode=None,
    image: str = "logo",
) -> None:
    """Удалить старое сообщение и отправить новое с изображением."""
    try:
        await message.delete()
    except Exception:
        pass
    try:
        await send_logo(message, text, reply_markup=reply_markup, parse_mode=parse_mode, image=image)
    except Exception:
        try:
            await message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
        except Exception:
            pass
