import asyncio
import json
import logging
import os
import tempfile

from bot.config import CHUNK_DURATION_MINUTES, MAX_FILE_SIZE_MB

logger = logging.getLogger(__name__)

# Ограничение параллельных ffmpeg-процессов (по числу vCPU)
_ffmpeg_semaphore = asyncio.Semaphore(2)

MAX_CHUNK_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024  # 24 МБ в байтах
CHUNK_DURATION_MS = CHUNK_DURATION_MINUTES * 60 * 1000  # в миллисекундах


async def _get_duration_ms(file_path: str) -> int:
    """Получить длительность аудиофайла в миллисекундах через ffprobe."""
    async with _ffmpeg_semaphore:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json",
            file_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffprobe завершился с кодом {proc.returncode}: {stderr.decode()}"
        )
    data = json.loads(stdout.decode())
    duration_sec = float(data["format"]["duration"])
    return int(duration_sec * 1000)


async def _create_chunk(
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

    async with _ffmpeg_semaphore:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg завершился с кодом {proc.returncode}: {stderr.decode()}"
        )


async def extract_audio(video_path: str) -> str:
    """Извлечь аудиодорожку из видеофайла в формат OGG.

    Возвращает путь к извлечённому аудиофайлу.
    """
    audio_path = os.path.splitext(video_path)[0] + ".ogg"
    async with _ffmpeg_semaphore:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y",
            "-i", video_path,
            "-vn",
            "-acodec", "libopus",
            "-loglevel", "error",
            audio_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg (extract_audio) завершился с кодом {proc.returncode}: {stderr.decode()}"
        )
    logger.info("Аудио извлечено из видео: %s → %s", video_path, audio_path)
    return audio_path


async def split_audio(file_path: str) -> list[str]:
    """Разбить аудиофайл на чанки по размеру или длительности.

    Нарезка происходит, если:
    - файл > MAX_FILE_SIZE_MB (лимит Whisper API), или
    - длительность > CHUNK_DURATION_MINUTES (для отслеживания прогресса).

    Если ни одно условие не выполнено — возвращает [file_path].

    Использует ffmpeg с -c copy (без перекодирования) для максимальной скорости.
    Если чанк окажется битым — перекодирование выполняется на этапе транскрибации.

    Чанки создаются параллельно через asyncio.gather (с ограничением через семафор).
    """
    file_size = os.path.getsize(file_path)
    total_duration_ms = await _get_duration_ms(file_path)

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
    tasks: list = []

    for i in range(num_chunks):
        start_ms = i * chunk_duration_ms
        end_ms = min((i + 1) * chunk_duration_ms, total_duration_ms)
        duration_ms = end_ms - start_ms

        start_sec = f"{start_ms / 1000:.3f}"
        dur_sec = f"{duration_ms / 1000:.3f}"
        chunk_path = os.path.join(chunk_dir, f"chunk_{i:03d}{ext}")

        chunk_paths.append(chunk_path)
        tasks.append(_create_chunk(file_path, chunk_path, start_sec, dur_sec))

    # Параллельная нарезка всех чанков (семафор внутри _create_chunk)
    await asyncio.gather(*tasks)

    for i, chunk_path in enumerate(chunk_paths):
        start_ms = i * chunk_duration_ms
        end_ms = min((i + 1) * chunk_duration_ms, total_duration_ms)
        logger.info(
            "Чанк %d: %d-%d мс → %s (%d байт)",
            i, start_ms, end_ms, chunk_path, os.path.getsize(chunk_path),
        )

    return chunk_paths


async def reencode_chunk(chunk_path: str) -> bool:
    """Перекодировать чанк на месте (fallback при ошибке транскрибации).

    Возвращает True, если перекодирование успешно.
    """
    tmp_path = chunk_path + ".reenc"
    try:
        async with _ffmpeg_semaphore:
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y",
                "-i", chunk_path,
                "-map", "a",
                "-loglevel", "error",
                tmp_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(
                f"ffmpeg завершился с кодом {proc.returncode}: {stderr.decode()}"
            )
        os.replace(tmp_path, chunk_path)
        logger.info("Чанк перекодирован: %s", chunk_path)
        return True
    except Exception:
        logger.warning("Не удалось перекодировать чанк: %s", chunk_path)
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        return False


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
