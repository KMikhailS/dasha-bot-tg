import logging
import re

import yt_dlp
from pytubefix import YouTube

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


def _is_youtube_url(url: str) -> bool:
    return bool(_YOUTUBE_PATTERN.match(url))


def _download_youtube_audio(url: str, dest_dir: str) -> str:
    """Скачать аудиодорожку из YouTube через pytubefix.

    Возвращает путь к скачанному аудиофайлу (m4a/webm).
    """
    yt = YouTube(url)
    stream = yt.streams.get_audio_only()
    if not stream:
        raise RuntimeError("Не удалось найти аудио-поток для этого видео")

    output_path = stream.download(output_path=dest_dir)
    if not output_path:
        raise RuntimeError("Не удалось скачать аудио из YouTube")

    return output_path


def _download_other_audio(url: str, dest_dir: str) -> str:
    """Скачать аудиодорожку из Instagram/VK/OK через yt-dlp.

    Возвращает путь к скачанному аудиофайлу.
    """
    output_template = f"{dest_dir}/%(title).50s.%(ext)s"

    ydl_opts = {
        "format": "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best",
        "outtmpl": output_template,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return ydl.prepare_filename(info)


def download_audio_from_url(url: str, dest_dir: str) -> str:
    """Скачать аудиодорожку из YouTube/Instagram/VK/OK.

    YouTube — через pytubefix (с fallback на yt-dlp).
    Остальные платформы — через yt-dlp.

    Args:
        url: ссылка на видео.
        dest_dir: директория для сохранения файла.

    Returns:
        Путь к скачанному аудиофайлу.

    Raises:
        RuntimeError: если скачивание не удалось.
    """
    logger.info("Скачиваю аудио из: %s", url)

    try:
        if _is_youtube_url(url):
            try:
                audio_path = _download_youtube_audio(url, dest_dir)
            except Exception as exc:
                logger.warning("pytubefix не справился, пробую yt-dlp: %s", exc)
                audio_path = _download_other_audio(url, dest_dir)
        else:
            audio_path = _download_other_audio(url, dest_dir)
    except Exception as exc:
        raise RuntimeError(f"Не удалось скачать аудио: {exc}") from exc

    logger.info("Аудио скачано: %s", audio_path)
    return audio_path
