import logging

from openai import OpenAI

from bot.config import OPENROUTER_API_KEY, SUMMARIZER_MAX_CHARS

logger = logging.getLogger(__name__)

_client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
)

# _MODEL = "anthropic/claude-sonnet-4.6"
_MODEL = "anthropic/claude-haiku-4.5"

_SYSTEM_PROMPT = (
    "You are a text summarization assistant. You receive a transcription of an audio recording "
    "and must return a clear, well-structured summary of its content.\n\n"
    "Rules:\n"
    "1. Summarize the key points, decisions, and conclusions from the text.\n"
    "2. Organize the summary with logical sections or bullet points if appropriate.\n"
    "3. Keep the summary concise but comprehensive — capture all important information.\n"
    "4. Write the summary in the SAME language as the original text.\n"
    "5. Do NOT add information that is not present in the original text.\n"
    "6. Do NOT add meta-commentary like 'Here is the summary' — return ONLY the summary itself.\n"
    "7. Do NOT use markdown tables — use bullet points or lists instead."
)


def summarize_text(raw_text: str) -> str | None:
    """Создать саммари текста через Claude (OpenRouter).

    Возвращает текст саммари или None при ошибке.
    """
    if len(raw_text) > SUMMARIZER_MAX_CHARS:
        logger.info(
            "Текст слишком большой для саммари (%d символов > %d), пропущено",
            len(raw_text), SUMMARIZER_MAX_CHARS,
        )
        return None

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
            logger.info("Саммари создано: %d → %d символов", len(raw_text), len(result))
            return result.strip()

        logger.warning("Claude вернул пустой ответ при создании саммари")
        return None

    except Exception as exc:
        logger.error("Ошибка создания саммари через OpenRouter: %s", exc)
        return None
