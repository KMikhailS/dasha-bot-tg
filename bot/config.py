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
HF_TOKEN = os.getenv("HF_TOKEN", "")

# Telegram Local Bot API
TELEGRAM_API_ID = os.getenv("TELEGRAM_API_ID", "")
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH", "")
LOCAL_BOT_API_URL = os.getenv("LOCAL_BOT_API_URL", "")

# Whisper
# MODEL = "whisper-1"
MODEL = "gpt-4o-mini-transcribe-2025-12-15"
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
    required = [
        "TELEGRAM_BOT_TOKEN",
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        "S3_ACCESS_KEY",
        "S3_SECRET_KEY",
        "S3_BUCKET",
        "S3_ENDPOINT",
        "TBANK_TERMINAL_KEY",
        "TBANK_PASSWORD",
    ]
    missing = [v for v in required if not os.getenv(v)]
    if missing:
        sys.exit(f"Отсутствуют обязательные переменные окружения: {', '.join(missing)}")

    # Опциональные переменные — предупреждение, не ошибка
    optional = ["HF_TOKEN", "TG_CHANNEL_ID"]
    for var in optional:
        if not os.getenv(var):
            print(f"Предупреждение: переменная {var} не задана (опциональная функция недоступна)")
