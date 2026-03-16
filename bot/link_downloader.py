import logging
import re

import yt_dlp

logger = logging.getLogger(__name__)

# Общий паттерн для извлечения URL из текста
_URL_PATTERN = re.compile(r"https?://[^\s]+")


def extract_media_url(text: str) -> str | None:
    """Извлечь URL из текста.

    Возвращает первый найденный URL или None.
    """
    match = _URL_PATTERN.search(text)
    return match.group(0) if match else None


def download_audio_from_url(url: str, dest_dir: str) -> str:
    """Скачать аудиодорожку по ссылке через yt-dlp.

    Args:
        url: ссылка на медиа.
        dest_dir: директория для сохранения файла.

    Returns:
        Путь к скачанному аудиофайлу.

    Raises:
        RuntimeError: если скачивание не удалось.
    """
    output_template = f"{dest_dir}/%(title).50s.%(ext)s"

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": output_template,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            },
        ],
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    }

    logger.info("Скачиваю аудио из: %s", url)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            # yt-dlp после постобработки меняет расширение на mp3
            filename = ydl.prepare_filename(info)
            # Заменяем расширение на .mp3 (постпроцессор конвертирует)
            audio_path = re.sub(r"\.[^.]+$", ".mp3", filename)

    except Exception as exc:
        raise RuntimeError(
            "Не удалось скачать аудио по этой ссылке. "
            "Убедитесь, что ссылка корректна и ведёт на поддерживаемый ресурс."
        ) from exc

    logger.info("Аудио скачано: %s", audio_path)
    return audio_path
