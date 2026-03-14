import json
import logging
import os
import subprocess
import tempfile

from bot.config import CHUNK_DURATION_MINUTES, MAX_FILE_SIZE_MB

logger = logging.getLogger(__name__)

MAX_CHUNK_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024  # 24 МБ в байтах
CHUNK_DURATION_MS = CHUNK_DURATION_MINUTES * 60 * 1000  # в миллисекундах


def _get_duration_ms(file_path: str) -> int:
    """Получить длительность аудиофайла в миллисекундах через ffprobe."""
    result = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json",
            file_path,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(result.stdout)
    duration_sec = float(data["format"]["duration"])
    return int(duration_sec * 1000)


def _validate_chunk(file_path: str) -> bool:
    """Проверить, что аудиочанк можно декодировать.

    Прогоняет файл через ffmpeg с выходом в null.
    Возвращает True, если файл валиден.
    """
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", file_path, "-f", "null", "-"],
        capture_output=True,
    )
    return result.returncode == 0


def _create_chunk(
    file_path: str,
    chunk_path: str,
    start_sec: str,
    duration_sec: str,
    *,
    reencode: bool = False,
) -> None:
    """Создать один чанк из исходного файла.

    Args:
        file_path: путь к исходному аудиофайлу.
        chunk_path: путь для сохранения чанка.
        start_sec: время начала в секундах (строка).
        duration_sec: длительность в секундах (строка).
        reencode: если True — перекодировать (медленнее, но надёжнее).
    """
    cmd = [
        "ffmpeg", "-y",
        "-ss", start_sec,
        "-t", duration_sec,
        "-i", file_path,
    ]
    if reencode:
        cmd += ["-map", "a"]
    else:
        cmd += ["-c", "copy"]
    cmd += ["-loglevel", "error", chunk_path]

    subprocess.run(cmd, check=True)


def split_audio(file_path: str) -> list[str]:
    """Разбить аудиофайл на чанки по размеру или длительности.

    Нарезка происходит, если:
    - файл > MAX_FILE_SIZE_MB (лимит Whisper API), или
    - длительность > CHUNK_DURATION_MINUTES (для отслеживания прогресса).

    Если ни одно условие не выполнено — возвращает [file_path].

    Использует ffmpeg с -c copy (без перекодирования) для максимальной скорости.
    Если чанк оказывается битым — пересоздаёт его с перекодированием.
    """
    file_size = os.path.getsize(file_path)
    total_duration_ms = _get_duration_ms(file_path)

    # Сколько чанков нужно по каждому критерию
    chunks_by_size = max(1, -(-file_size // MAX_CHUNK_BYTES))        # ceil
    chunks_by_duration = max(1, -(-total_duration_ms // CHUNK_DURATION_MS))  # ceil
    num_chunks = max(chunks_by_size, chunks_by_duration)

    if num_chunks <= 1:
        logger.info(
            "Файл %s (%d байт, %.0f сек) — нарезка не требуется",
            file_path, file_size, total_duration_ms / 1000,
        )
        return [file_path]

    logger.info(
        "Файл %s (%d байт, %.0f сек) — нарезаю на %d чанков",
        file_path, file_size, total_duration_ms / 1000, num_chunks,
    )

    chunk_duration_ms = total_duration_ms // num_chunks
    ext = os.path.splitext(file_path)[1] or ".mp3"

    chunk_dir = tempfile.mkdtemp(prefix="audio_chunks_")
    chunk_paths: list[str] = []

    for i in range(num_chunks):
        start_ms = i * chunk_duration_ms
        end_ms = min((i + 1) * chunk_duration_ms, total_duration_ms)
        duration_ms = end_ms - start_ms

        start_sec = f"{start_ms / 1000:.3f}"
        dur_sec = f"{duration_ms / 1000:.3f}"
        chunk_path = os.path.join(chunk_dir, f"chunk_{i:03d}{ext}")

        # Быстрая нарезка без перекодирования
        _create_chunk(file_path, chunk_path, start_sec, dur_sec)

        # Проверяем валидность; если битый — пересоздаём с перекодированием
        if not _validate_chunk(chunk_path):
            logger.warning(
                "Чанк %d битый после -c copy, пересоздаю с перекодированием",
                i,
            )
            try:
                _create_chunk(file_path, chunk_path, start_sec, dur_sec, reencode=True)
            except subprocess.CalledProcessError:
                logger.warning(
                    "Чанк %d: исходный файл повреждён на этом участке, пропускаю",
                    i,
                )
                continue

        chunk_paths.append(chunk_path)

        logger.info(
            "Чанк %d: %d-%d мс → %s (%d байт)",
            i, start_ms, end_ms, chunk_path, os.path.getsize(chunk_path),
        )

    return chunk_paths


def cleanup_chunks(chunk_paths: list[str], original_path: str) -> None:
    """Удалить временные файлы чанков (не трогает оригинал)."""
    for path in chunk_paths:
        if path != original_path:
            try:
                os.remove(path)
            except OSError:
                pass

    # Удаляем временную директорию, если она пуста
    if chunk_paths and chunk_paths[0] != original_path:
        chunk_dir = os.path.dirname(chunk_paths[0])
        try:
            os.rmdir(chunk_dir)
        except OSError:
            pass
