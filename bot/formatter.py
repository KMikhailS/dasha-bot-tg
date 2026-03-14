import logging

from openai import OpenAI

from bot.config import FORMATTER_MAX_CHARS, OPENROUTER_API_KEY

logger = logging.getLogger(__name__)

_client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
)

# _MODEL = "anthropic/claude-sonnet-4.6"
_MODEL = "anthropic/claude-haiku-4.5"

_SYSTEM_PROMPT = (
    "You are a text formatting assistant. You receive a raw speech-to-text transcription "
    "and must return a clean, well-formatted version of the same text.\n\n"
    "Rules:\n"
    "1. Split the text into logical paragraphs.\n"
    "2. Fix obvious punctuation errors (missing periods, commas, capitalization).\n"
    "3. Do NOT change the meaning, wording, or language of the text.\n"
    "4. Do NOT add, remove, or rephrase any content.\n"
    "5. Do NOT translate the text — keep the original language.\n"
    "6. Do NOT add any headers, titles, summaries, or comments.\n"
    "7. Return ONLY the formatted text, nothing else."
)


def format_text(raw_text: str) -> str:
    """Отформатировать текст транскрипции через Claude (OpenRouter).

    При ошибке или слишком большом тексте возвращает исходный текст без изменений.
    """
    if len(raw_text) > FORMATTER_MAX_CHARS:
        logger.info(
            "Текст слишком большой (%d символов > %d), форматирование пропущено",
            len(raw_text), FORMATTER_MAX_CHARS,
        )
        return raw_text

    try:
        response = _client.chat.completions.create(
            model=_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": raw_text},
            ],
        )
        result = response.choices[0].message.content
        if result and result.strip():
            logger.info("Форматирование завершено: %d → %d символов", len(raw_text), len(result))
            return result.strip()

        logger.warning("Claude вернул пустой ответ, используем оригинальный текст")
        return raw_text

    except Exception as exc:
        logger.error("Ошибка форматирования через OpenRouter: %s", exc)
        return raw_text
