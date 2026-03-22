import os
import sys

from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
TBANK_TERMINAL_KEY = os.getenv("TBANK_TERMINAL_KEY", "")
TBANK_PASSWORD = os.getenv("TBANK_PASSWORD", "")
TBANK_TAXATION = os.getenv("TBANK_TAXATION", "usn_income")  # система налогообложения

# Whisper
MODEL = "whisper-1"
# MODEL = "gpt-4o-mini-transcribe-2025-12-15"
WHISPER_LANGUAGE = "ru"  # явно указываем язык — снижает галлюцинации
# Пороги для фильтрации «плохих» сегментов (борьба с галлюцинациями)
WHISPER_NO_SPEECH_THRESHOLD = 0.6     # вероятность «нет речи» выше → отбрасываем
WHISPER_COMPRESSION_RATIO_THRESHOLD = 2.4  # коэффициент сжатия выше → повторы → отбрасываем
WHISPER_AVG_LOGPROB_THRESHOLD = -1.0  # средняя уверенность ниже → отбрасываем
WHISPER_PROMPT_CHARS = 224  # сколько символов конца предыдущего чанка передавать как prompt
MAX_FILE_SIZE_MB = 24  # лимит Whisper 25 МБ, берём с запасом
FORMATTER_MAX_CHARS = 15000  # тексты длиннее этого не форматируем (слишком долго)
SUMMARIZER_MAX_CHARS = 200000  # тексты длиннее этого не суммаризируем
CHUNK_DURATION_MINUTES = 3  # макс. длительность одного чанка (для прогресса)
SUPPORTED_AUDIO_EXTENSIONS = {
    ".mp3", ".mp4", ".mpeg", ".mpga", ".m4a", ".wav", ".webm", ".ogg",
}


def validate_config() -> None:
    """Проверяет, что все обязательные переменные окружения заданы."""
    if not TELEGRAM_BOT_TOKEN:
        sys.exit("Ошибка: переменная TELEGRAM_BOT_TOKEN не задана")
    if not OPENAI_API_KEY:
        sys.exit("Ошибка: переменная OPENAI_API_KEY не задана")
    if not OPENROUTER_API_KEY:
        sys.exit("Ошибка: переменная OPENROUTER_API_KEY не задана")
