import asyncio
import logging
import os
import re
from collections.abc import Callable

from openai import OpenAI

from bot.audio_splitter import cleanup_chunks, reencode_chunk, split_audio
from bot.config import (
    OPENAI_API_KEY,
    SUPPORTED_AUDIO_EXTENSIONS,
    MODEL,
    WHISPER_PROMPT_CHARS,
)

# Тип callback-а для отслеживания прогресса: (current_chunk, total_chunks)
ProgressCallback = Callable[[int, int], None]

logger = logging.getLogger(__name__)

client = OpenAI(api_key=OPENAI_API_KEY)

# Порог: если одно и то же слово/фраза повторяется подряд >= этого числа раз,
# считаем это галлюцинацией и удаляем
_HALLUCINATION_REPEAT_THRESHOLD = 4

# Максимальная длина повторяющейся фразы (в словах) для поиска галлюцинаций
_MAX_PHRASE_WORDS = 5


class TranscriptionError(Exception):
    """Ошибка транскрибации."""


def validate_audio_file(file_path: str) -> None:
    """Проверить, что файл имеет поддерживаемый формат."""
    ext = os.path.splitext(file_path)[1].lower()
    if ext not in SUPPORTED_AUDIO_EXTENSIONS:
        raise TranscriptionError(
            f"Неподдерживаемый формат файла: {ext}. "
            f"Поддерживаемые: {', '.join(sorted(SUPPORTED_AUDIO_EXTENSIONS))}"
        )


def _remove_repeated_ngrams(words: list[str], n: int) -> list[str]:
    """Удалить последовательные повторы n-грамм из списка слов.

    Если одна и та же фраза из n слов повторяется подряд
    >= _HALLUCINATION_REPEAT_THRESHOLD раз, все повторы удаляются.
    Также удаляется оборванный «хвост» — неполное повторение в конце
    (например, «ЗВО» после сотни «ЗВОНОК ТЕЛЕФОНА»).
    """
    if len(words) < n * _HALLUCINATION_REPEAT_THRESHOLD:
        return words

    result: list[str] = []
    i = 0

    while i < len(words):
        # Хватает ли слов для threshold повторений?
        if i + n * _HALLUCINATION_REPEAT_THRESHOLD <= len(words):
            ngram = " ".join(words[i : i + n])

            # Считаем последовательные повторы
            count = 1
            j = i + n
            while j + n <= len(words) and " ".join(words[j : j + n]) == ngram:
                count += 1
                j += n

            if count >= _HALLUCINATION_REPEAT_THRESHOLD:
                logger.debug(
                    "Галлюцинация: «%s» × %d раз", ngram, count,
                )
                # Пропускаем оборванный хвост: неполное повторение фразы
                # (пример: «ЗВО» после «ЗВОНОК ТЕЛЕФОНА» × 200)
                ngram_words = words[i : i + n]
                remaining = len(words) - j
                if 0 < remaining < n:
                    tail = words[j : j + remaining]
                    # Проверяем пословное совпадение начала фразы
                    match = all(
                        tail[k] == ngram_words[k] for k in range(remaining)
                    )
                    # Или последнее слово — обрезанное начало следующего слова
                    if not match and remaining == 1:
                        match = (
                            len(tail[0]) < len(ngram_words[0])
                            and ngram_words[0].lower().startswith(tail[0].lower())
                        )
                    if match:
                        j += remaining

                i = j
                continue

        result.append(words[i])
        i += 1

    return result


def _clean_hallucinations(text: str) -> str:
    """Удалить из текста повторяющиеся залипания (hallucination loops).

    Whisper иногда при шумах генерирует сотни повторов одного слова или фразы:
      - «Мг! Мг! Мг! Мг! ...»  (1 слово)
      - «ЗВОНОК ТЕЛЕФОНА ЗВОНОК ТЕЛЕФОНА ...» (2 слова)
    Функция ищет повторы фраз длиной от 1 до _MAX_PHRASE_WORDS слов
    и удаляет их, если число последовательных повторов >= порога.
    """
    original_len = len(text)

    words = text.split()

    # Ищем повторы от коротких фраз к длинным:
    # сначала удаляем одиночные повторяющиеся слова (n=1),
    # затем двусловные фразы (n=2) и т.д.
    for n in range(1, _MAX_PHRASE_WORDS + 1):
        words = _remove_repeated_ngrams(words, n)

    cleaned = " ".join(words)

    # Нормализуем множественные пробелы
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()

    removed_chars = original_len - len(cleaned)
    if removed_chars > 0:
        logger.info(
            "Обнаружены и удалены галлюцинации Whisper: удалено %d символов (%.1f%%)",
            removed_chars,
            removed_chars / original_len * 100,
        )

    return cleaned


def _transcribe_single(file_path: str, prompt: str = "") -> str:
    """Транскрибировать один файл через OpenAI Transcription API.

    Args:
        file_path: путь к аудиофайлу.
        prompt: текст-подсказка (конец предыдущего чанка для контекста).
    """
    kwargs: dict = {
        "model": MODEL,
        "temperature": 0.0,
        "response_format": "json",
    }
    if prompt:
        kwargs["prompt"] = prompt

    with open(file_path, "rb") as f:
        kwargs["file"] = f
        response = client.audio.transcriptions.create(**kwargs)

    return response.text.strip()


async def transcribe_audio(
    file_path: str,
    on_progress: ProgressCallback | None = None,
) -> str:
    """Транскрибировать аудиофайл (с автоматической нарезкой, если нужно).

    1. Валидирует формат
    2. Нарезает на чанки, если файл > 24 МБ
    3. Транскрибирует каждый чанк
    4. Склеивает тексты
    5. Удаляет галлюцинации (повторяющиеся токены)
    6. Очищает временные файлы

    Args:
        file_path: путь к аудиофайлу.
        on_progress: опциональный callback (current_chunk, total_chunks),
                     вызывается после каждого обработанного чанка.

    Returns:
        Текст транскрипции.

    Raises:
        TranscriptionError: при ошибке валидации или транскрибации.
    """
    validate_audio_file(file_path)

    chunk_paths = await split_audio(file_path)
    total = len(chunk_paths)
    logger.info("Чанков для транскрибации: %d", total)

    texts: list[str] = []
    failed_chunks: list[int] = []
    prev_prompt = ""  # prompt chaining: конец предыдущего чанка
    try:
        for i, chunk_path in enumerate(chunk_paths):
            logger.info("Транскрибирую чанк %d/%d: %s", i + 1, total, chunk_path)
            try:
                text = await asyncio.to_thread(
                    _transcribe_single, chunk_path, prev_prompt,
                )
            except Exception as exc:
                # Попытка перекодировать и повторить транскрибацию
                logger.warning(
                    "Чанк %d/%d: ошибка транскрибации, пробую перекодировать: %s",
                    i + 1, total, exc,
                )
                text = None
                if await reencode_chunk(chunk_path):
                    try:
                        text = await asyncio.to_thread(
                            _transcribe_single, chunk_path, prev_prompt,
                        )
                    except Exception as retry_exc:
                        logger.warning(
                            "Чанк %d/%d не удалось транскрибировать после перекодирования, пропускаю: %s",
                            i + 1, total, retry_exc,
                        )
                else:
                    logger.warning(
                        "Чанк %d/%d: перекодирование не удалось, пропускаю",
                        i + 1, total,
                    )
                if text is None:
                    failed_chunks.append(i + 1)
                    if on_progress is not None:
                        try:
                            on_progress(i + 1, total)
                        except Exception as cb_exc:
                            logger.warning("Ошибка progress callback: %s", cb_exc)
                    continue
            texts.append(text)
            logger.info("Чанк %d: получено %d символов", i + 1, len(text))

            # Сохраняем конец текста как prompt для следующего чанка
            prev_prompt = text[-WHISPER_PROMPT_CHARS:] if text else ""

            if on_progress is not None:
                try:
                    on_progress(i + 1, total)
                except Exception as cb_exc:
                    logger.warning("Ошибка progress callback: %s", cb_exc)
    except Exception as exc:
        raise TranscriptionError(f"Ошибка при транскрибации: {exc}") from exc
    finally:
        cleanup_chunks(chunk_paths, original_path=file_path)

    if failed_chunks:
        logger.warning(
            "Не удалось транскрибировать %d из %d чанков: %s",
            len(failed_chunks), total, failed_chunks,
        )

    if not texts:
        raise TranscriptionError("Ни один чанк не был успешно транскрибирован")

    result = " ".join(texts)
    logger.info("Транскрибация завершена: %d символов (до очистки)", len(result))

    # Постобработка: удаление hallucination loops
    result = _clean_hallucinations(result)
    logger.info("После очистки галлюцинаций: %d символов", len(result))

    return result
