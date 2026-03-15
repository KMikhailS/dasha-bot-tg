import logging
import sqlite3
import threading
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

DB_PATH = "/app/data/transcriber.db"

_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    """Получить соединение с БД (per-thread)."""
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(DB_PATH)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
    return _local.conn


def init_db() -> None:
    """Создать таблицы и дефолтную подписку если их нет."""
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            code        TEXT    NOT NULL UNIQUE,
            name        TEXT    NOT NULL,
            amount     INTEGER NOT NULL DEFAULT 0,
            active      INTEGER NOT NULL DEFAULT 1,
            createstamp TEXT    NOT NULL,
            changestamp TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS user_info (
            id              INTEGER PRIMARY KEY,
            username        TEXT,
            first_name      TEXT,
            phone           TEXT,
            subscription_id INTEGER,
            role            TEXT    NOT NULL DEFAULT 'USER',
            is_onboarded    INTEGER NOT NULL DEFAULT 0,
            createstamp     TEXT    NOT NULL,
            changestamp     TEXT    NOT NULL,
            FOREIGN KEY (subscription_id) REFERENCES subscriptions(id)
        );

        CREATE TABLE IF NOT EXISTS selected_subscriptions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            subscription_id INTEGER NOT NULL,
            user_id         INTEGER NOT NULL,
            balance         INTEGER NOT NULL DEFAULT 0,
            is_active       INTEGER NOT NULL DEFAULT 1,
            createstamp     TEXT    NOT NULL,
            changestamp     TEXT    NOT NULL,
            FOREIGN KEY (subscription_id) REFERENCES subscriptions(id),
            FOREIGN KEY (user_id) REFERENCES user_info(id)
        );

        CREATE TABLE IF NOT EXISTS payments (
            payment_id  TEXT    PRIMARY KEY,
            user_id     INTEGER NOT NULL,
            amount      INTEGER NOT NULL,
            status      TEXT    NOT NULL DEFAULT 'pending',
            createstamp TEXT    NOT NULL,
            changestamp TEXT    NOT NULL,
            FOREIGN KEY (user_id) REFERENCES user_info(id)
        );

        CREATE TABLE IF NOT EXISTS records (
            id              TEXT    PRIMARY KEY,
            user_id         INTEGER NOT NULL,
            title           TEXT    NOT NULL,
            transcription_text TEXT,
            text_s3_key     TEXT,
            duration_seconds INTEGER,
            source_type     TEXT    NOT NULL DEFAULT 'audio',
            source_url      TEXT,
            created_at      TEXT    NOT NULL,
            FOREIGN KEY (user_id) REFERENCES user_info(id)
        );

        CREATE TABLE IF NOT EXISTS user_settings (
            user_id                INTEGER PRIMARY KEY,
            transcription_language TEXT    NOT NULL DEFAULT 'ru',
            diarization            INTEGER NOT NULL DEFAULT 0,
            export_format          TEXT    NOT NULL DEFAULT 'txt',
            auto_title             INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY (user_id) REFERENCES user_info(id)
        );
    """)

    # Создаём стартовую подписку если её ещё нет
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT OR IGNORE INTO subscriptions (code, name, amount, active, createstamp, changestamp)
        VALUES ('start', 'Стартовая', 300, 1, ?, ?)
        """,
        (now, now),
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO subscriptions (code, name, amount, active, createstamp, changestamp)
        VALUES ('basic', 'Базовая', 1000, 1, ?, ?)
        """,
        (now, now),
    )
    # Миграция: добавить is_onboarded если отсутствует (для существующих БД)
    try:
        conn.execute("SELECT is_onboarded FROM user_info LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE user_info ADD COLUMN is_onboarded INTEGER NOT NULL DEFAULT 0")
        # Существующие пользователи уже видели бота — помечаем как onboarded
        conn.execute("UPDATE user_info SET is_onboarded = 1")

    conn.commit()
    logger.info("База данных инициализирована")


def get_or_create_user(user_id: int, username: str | None = None, first_name: str | None = None) -> None:
    """Записать нового пользователя с подпиской 'start'. Если уже есть — ничего не делать."""
    conn = _get_conn()

    # Проверяем, существует ли пользователь
    row = conn.execute("SELECT id FROM user_info WHERE id = ?", (user_id,)).fetchone()
    if row:
        logger.info("Пользователь %d уже существует", user_id)
        return

    # Получаем стартовую подписку
    sub = conn.execute("SELECT id, amount FROM subscriptions WHERE code = 'start'").fetchone()
    sub_id = sub["id"] if sub else None
    sub_balance = sub["amount"] if sub else 0

    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO user_info (id, username, first_name, subscription_id, role, createstamp, changestamp)
        VALUES (?, ?, ?, ?, 'USER', ?, ?)
        """,
        (user_id, username, first_name, sub_id, now, now),
    )

    # Создаём назначенную подписку с балансом из шаблона
    if sub_id is not None:
        conn.execute(
            """
            INSERT INTO selected_subscriptions (subscription_id, user_id, balance, is_active, createstamp, changestamp)
            VALUES (?, ?, ?, 1, ?, ?)
            """,
            (sub_id, user_id, sub_balance, now, now),
        )

    conn.commit()
    logger.info("Создан пользователь %d (%s)", user_id, username or "no username")


def save_payment(payment_id: str, user_id: int, amount: int) -> None:
    """Сохранить новый платёж со статусом pending."""
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO payments (payment_id, user_id, amount, status, createstamp, changestamp)
        VALUES (?, ?, ?, 'pending', ?, ?)
        """,
        (payment_id, user_id, amount, now, now),
    )
    conn.commit()
    logger.info("Платёж %s сохранён для пользователя %d", payment_id, user_id)


def get_pending_payment(user_id: int) -> tuple[str, int] | None:
    """Получить последний pending-платёж пользователя.

    Возвращает (payment_id, amount) или None.
    """
    conn = _get_conn()
    row = conn.execute(
        "SELECT payment_id, amount FROM payments WHERE user_id = ? AND status = 'pending' ORDER BY createstamp DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    if row:
        return row["payment_id"], row["amount"]
    return None


def mark_payment_paid(payment_id: str, user_id: int, amount: int) -> None:
    """Зачислить оплату: деактивировать текущую подписку, создать новую (basic), обновить статус платежа.

    Всё выполняется в одной транзакции.
    """
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()

    # Текущий баланс
    row = conn.execute(
        "SELECT balance FROM selected_subscriptions WHERE user_id = ? AND is_active = 1 ORDER BY id DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    current_balance = row["balance"] if row else 0

    # id подписки basic
    basic = conn.execute("SELECT id FROM subscriptions WHERE code = 'basic'").fetchone()
    if not basic:
        logger.error("Подписка с кодом 'basic' не найдена в БД")
        return
    basic_id = basic["id"]

    # Деактивировать текущую активную подписку
    conn.execute(
        "UPDATE selected_subscriptions SET is_active = 0, changestamp = ? WHERE user_id = ? AND is_active = 1",
        (now, user_id),
    )

    # Создать новую активную подписку
    conn.execute(
        """
        INSERT INTO selected_subscriptions (subscription_id, user_id, balance, is_active, createstamp, changestamp)
        VALUES (?, ?, ?, 1, ?, ?)
        """,
        (basic_id, user_id, current_balance + amount, now, now),
    )

    # Обновить статус платежа
    conn.execute(
        "UPDATE payments SET status = 'succeeded', changestamp = ? WHERE payment_id = ?",
        (now, payment_id),
    )

    conn.commit()
    logger.info(
        "Платёж %s зачислен: пользователь %d, баланс %d → %d",
        payment_id, user_id, current_balance, current_balance + amount,
    )


def get_user_balance(user_id: int) -> int:
    """Получить текущий баланс пользователя из активной назначенной подписки."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT balance FROM selected_subscriptions WHERE user_id = ? AND is_active = 1 ORDER BY id DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    if row:
        return row["balance"]
    return 0


# ── Онбординг ──────────────────────────────────────────────

def is_user_onboarded(user_id: int) -> bool:
    conn = _get_conn()
    row = conn.execute("SELECT is_onboarded FROM user_info WHERE id = ?", (user_id,)).fetchone()
    return bool(row and row["is_onboarded"])


def set_user_onboarded(user_id: int) -> None:
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE user_info SET is_onboarded = 1, changestamp = ? WHERE id = ?",
        (now, user_id),
    )
    conn.commit()


# ── Записи (records) ──────────────────────────────────────

def save_record(
    record_id: str,
    user_id: int,
    title: str,
    transcription_text: str | None = None,
    text_s3_key: str | None = None,
    duration_seconds: int | None = None,
    source_type: str = "audio",
    source_url: str | None = None,
) -> None:
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO records (id, user_id, title, transcription_text, text_s3_key,
                             duration_seconds, source_type, source_url, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (record_id, user_id, title, transcription_text, text_s3_key,
         duration_seconds, source_type, source_url, now),
    )
    conn.commit()


def get_user_records(user_id: int, limit: int = 20, offset: int = 0) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, title, duration_seconds, source_type, created_at "
        "FROM records WHERE user_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (user_id, limit, offset),
    ).fetchall()
    return [dict(r) for r in rows]


def get_records_count(user_id: int) -> int:
    conn = _get_conn()
    row = conn.execute("SELECT COUNT(*) as cnt FROM records WHERE user_id = ?", (user_id,)).fetchone()
    return row["cnt"] if row else 0


def get_record(record_id: str) -> dict | None:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM records WHERE id = ?", (record_id,)).fetchone()
    return dict(row) if row else None


def delete_record(record_id: str) -> None:
    conn = _get_conn()
    conn.execute("DELETE FROM records WHERE id = ?", (record_id,))
    conn.commit()


def rename_record(record_id: str, new_title: str) -> None:
    conn = _get_conn()
    conn.execute("UPDATE records SET title = ? WHERE id = ?", (new_title, record_id))
    conn.commit()


# ── Настройки пользователя ────────────────────────────────

def get_user_settings(user_id: int) -> dict:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM user_settings WHERE user_id = ?", (user_id,)).fetchone()
    if row:
        return dict(row)
    # Создаём дефолтные настройки
    conn.execute("INSERT OR IGNORE INTO user_settings (user_id) VALUES (?)", (user_id,))
    conn.commit()
    return {"user_id": user_id, "transcription_language": "ru", "diarization": 0,
            "export_format": "txt", "auto_title": 1}


def update_user_setting(user_id: int, key: str, value: str | int) -> None:
    allowed = {"transcription_language", "diarization", "export_format", "auto_title"}
    if key not in allowed:
        return
    conn = _get_conn()
    conn.execute("INSERT OR IGNORE INTO user_settings (user_id) VALUES (?)", (user_id,))
    conn.execute(f"UPDATE user_settings SET {key} = ? WHERE user_id = ?", (value, user_id))
    conn.commit()
