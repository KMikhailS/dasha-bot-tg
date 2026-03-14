import logging
from dataclasses import dataclass

from pyannote.audio import Pipeline

from bot.config import HF_TOKEN

logger = logging.getLogger(__name__)


@dataclass
class DiarizationSegment:
    """Сегмент диаризации: кто говорил и когда."""
    start: float   # начало в секундах
    end: float     # конец в секундах
    speaker: str   # метка спикера, напр. "SPEAKER_00"


# Модель загружается один раз при первом вызове (ленивая инициализация)
_pipeline: Pipeline | None = None


def _get_pipeline() -> Pipeline:
    """Загрузить модель pyannote (один раз, потом кешируется)."""
    global _pipeline
    if _pipeline is None:
        logger.info("Загружаю модель pyannote для диаризации...")
        _pipeline = Pipeline.from_pretrained(
            "pyannote/speaker-diarization-3.1",
            use_auth_token=HF_TOKEN,
        )
        logger.info("Модель pyannote загружена")
    return _pipeline


def diarize(file_path: str) -> list[DiarizationSegment]:
    """Определить спикеров в аудиофайле.

    Args:
        file_path: путь к аудиофайлу.

    Returns:
        Список сегментов с метками спикеров, отсортированный по времени.
    """
    pipeline = _get_pipeline()

    logger.info("Запускаю диаризацию: %s", file_path)
    diarization = pipeline(file_path)

    segments: list[DiarizationSegment] = []
    for turn, _, speaker in diarization.itertracks(yield_label=True):
        segments.append(DiarizationSegment(
            start=turn.start,
            end=turn.end,
            speaker=speaker,
        ))

    logger.info("Диаризация завершена: %d сегментов, спикеров: %s",
                len(segments),
                len({s.speaker for s in segments}))
    return segments


def find_speaker(diarization_segments: list[DiarizationSegment],
                 start: float, end: float) -> str:
    """Найти спикера для данного временного интервала.

    Ищем сегмент диаризации с максимальным пересечением по времени.

    Args:
        diarization_segments: результат диаризации.
        start: начало интервала (секунды).
        end: конец интервала (секунды).

    Returns:
        Метка спикера (напр. "SPEAKER_00") или "UNKNOWN".
    """
    best_speaker = "UNKNOWN"
    best_overlap = 0.0

    for seg in diarization_segments:
        # Вычисляем пересечение двух интервалов
        overlap_start = max(start, seg.start)
        overlap_end = min(end, seg.end)
        overlap = max(0.0, overlap_end - overlap_start)

        if overlap > best_overlap:
            best_overlap = overlap
            best_speaker = seg.speaker

    return best_speaker
