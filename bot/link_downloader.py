import logging
import re

import yt_dlp

logger = logging.getLogger(__name__)

# Паттерны URL для YouTube и Instagram
_YOUTUBE_PATTERN = re.compile(
    r"https?://(?:www\.)?(?:youtube\.com/(?:watch\?[^\s]*v=|shorts/|live/)|youtu\.be/)[^\s]+",
)
_INSTAGRAM_PATTERN = re.compile(
    r"https?://(?:www\.)?instagram\.com/(?:reel|p|tv)/[^\s]+",
)
_VK_PATTERN = re.compile(
    r"https?://(?:www\.)?(?:vk\.com|vk\.ru|vkvideo\.ru)/(?:video|clip)-?\d+_\d+[^\s]*",
)
_OK_PATTERN = re.compile(
    r"https?://(?:www\.)?ok\.ru/video(?:embed)?/\d+[^\s]*",
)


def extract_media_url(text: str) -> str | None:
    """Извлечь YouTube, Instagram или VK URL из текста.

    Возвращает первый найденный URL или None.
    """
    for pattern in (_YOUTUBE_PATTERN, _INSTAGRAM_PATTERN, _VK_PATTERN, _OK_PATTERN):
        match = pattern.search(text)
        if match:
            return match.group(0)
    return None


def download_audio_from_url(url: str, dest_dir: str) -> str:
    """Скачать аудиодорожку из YouTube/Instagram через yt-dlp.

    Args:
        url: ссылка на видео.
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
        raise RuntimeError(f"Не удалось скачать аудио: {exc}") from exc

    logger.info("Аудио скачано: %s", audio_path)
    return audio_path
