import logging
import sqlite3
import threading
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

DB_PATH = "/app/data/transcriber.db"

_local = threading.local()

# Тарифы: code → (name, minutes, price_rub)
# minutes = -1 означает безлимит
PLANS = {
    "basic":     ("Basic", 100, 200),
    "standard":  ("Standard", 500, 500),
    "pro":       ("Pro", 5000, 4000),
}


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
            amount      INTEGER NOT NULL DEFAULT 0,
            price       INTEGER NOT NULL DEFAULT 0,
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
            ref_code        TEXT,
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
            payment_id        TEXT    PRIMARY KEY,
            user_id           INTEGER NOT NULL,
            amount            INTEGER NOT NULL,
            subscription_code TEXT,
            status            TEXT    NOT NULL DEFAULT 'pending',
            createstamp       TEXT    NOT NULL,
            changestamp       TEXT    NOT NULL,
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

        CREATE TABLE IF NOT EXISTS referrals (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_id     INTEGER NOT NULL,
            referred_id     INTEGER NOT NULL UNIQUE,
            minutes_awarded INTEGER NOT NULL DEFAULT 60,
            created_at      TEXT    NOT NULL,
            FOREIGN KEY (referrer_id) REFERENCES user_info(id),
            FOREIGN KEY (referred_id) REFERENCES user_info(id)
        );
    """)

    # ── Миграции для существующих БД (ДО вставки данных) ──

    # is_onboarded
    try:
        conn.execute("SELECT is_onboarded FROM user_info LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE user_info ADD COLUMN is_onboarded INTEGER NOT NULL DEFAULT 0")
        conn.execute("UPDATE user_info SET is_onboarded = 1")

    # ref_code
    try:
        conn.execute("SELECT ref_code FROM user_info LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE user_info ADD COLUMN ref_code TEXT")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_user_ref_code ON user_info(ref_code)")

    # price в subscriptions
    try:
        conn.execute("SELECT price FROM subscriptions LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE subscriptions ADD COLUMN price INTEGER NOT NULL DEFAULT 0")

    # subscription_code в payments
    try:
        conn.execute("SELECT subscription_code FROM payments LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE payments ADD COLUMN subscription_code TEXT")

    # Создаём/обновляем тарифы
    now = datetime.now(timezone.utc).isoformat()
    # Внутренний тариф для новых пользователей (не отображается в UI)
    all_plans = {**PLANS, "free": ("Бесплатный", 60, 0)}
    for code, (name, minutes, price) in all_plans.items():
        conn.execute(
            """
            INSERT INTO subscriptions (code, name, amount, price, active, createstamp, changestamp)
            VALUES (?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(code) DO UPDATE SET name=?, amount=?, price=?, changestamp=?
            """,
            (code, name, minutes, price, now, now, name, minutes, price, now),
        )

    conn.commit()
    logger.info("База данных инициализирована")


def get_or_create_user(user_id: int, username: str | None = None, first_name: str | None = None) -> None:
    """Записать нового пользователя с подпиской 'free'. Если уже есть — ничего не делать."""
    conn = _get_conn()

    row = conn.execute("SELECT id FROM user_info WHERE id = ?", (user_id,)).fetchone()
    if row:
        logger.info("Пользователь %d уже существует", user_id)
        return

    # Получаем бесплатный тариф
    sub = conn.execute("SELECT id, amount FROM subscriptions WHERE code = 'free'").fetchone()
    if not sub:
        # Fallback на старую подписку
        sub = conn.execute("SELECT id, amount FROM subscriptions WHERE code = 'start'").fetchone()
    sub_id = sub["id"] if sub else None
    sub_balance = sub["amount"] if sub else 30

    ref_code = uuid.uuid4().hex[:8]
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO user_info (id, username, first_name, subscription_id, role, ref_code, createstamp, changestamp)
        VALUES (?, ?, ?, ?, 'USER', ?, ?, ?)
        """,
        (user_id, username, first_name, sub_id, ref_code, now, now),
    )

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


def save_payment(payment_id: str, user_id: int, amount: int, subscription_code: str | None = None) -> None:
    """Сохранить новый платёж со статусом pending."""
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO payments (payment_id, user_id, amount, subscription_code, status, createstamp, changestamp)
        VALUES (?, ?, ?, ?, 'pending', ?, ?)
        """,
        (payment_id, user_id, amount, subscription_code, now, now),
    )
    conn.commit()
    logger.info("Платёж %s сохранён для пользователя %d (план: %s)", payment_id, user_id, subscription_code)


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


def mark_payment_paid(payment_id: str, user_id: int, subscription_code: str | None = None) -> None:
    """Зачислить оплату: деактивировать текущую подписку, создать новую, обновить статус платежа.

    Если subscription_code не указан, берём его из записи платежа.
    Минуты тарифа добавляются к текущему балансу (безлимит = -1).
    """
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()

    # Определяем код тарифа
    if not subscription_code:
        pay_row = conn.execute(
            "SELECT subscription_code FROM payments WHERE payment_id = ?", (payment_id,)
        ).fetchone()
        subscription_code = pay_row["subscription_code"] if pay_row else "basic"

    # Получаем тариф
    sub = conn.execute(
        "SELECT id, amount FROM subscriptions WHERE code = ?", (subscription_code,)
    ).fetchone()
    if not sub:
        logger.error("Подписка с кодом '%s' не найдена в БД", subscription_code)
        return
    sub_id = sub["id"]
    plan_minutes = sub["amount"]  # -1 для безлимита

    # Текущий баланс
    row = conn.execute(
        "SELECT balance FROM selected_subscriptions WHERE user_id = ? AND is_active = 1 ORDER BY id DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    current_balance = row["balance"] if row else 0

    # Рассчитываем новый баланс
    if plan_minutes == -1:
        new_balance = -1  # безлимит
    elif current_balance == -1:
        new_balance = -1  # уже безлимит — не понижаем
    else:
        new_balance = current_balance + plan_minutes

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
        (sub_id, user_id, new_balance, now, now),
    )

    # Обновить user_info.subscription_id
    conn.execute(
        "UPDATE user_info SET subscription_id = ?, changestamp = ? WHERE id = ?",
        (sub_id, now, user_id),
    )

    # Обновить статус платежа
    conn.execute(
        "UPDATE payments SET status = 'succeeded', changestamp = ? WHERE payment_id = ?",
        (now, payment_id),
    )

    conn.commit()
    logger.info(
        "Платёж %s зачислен: пользователь %d, план '%s', баланс %d → %d",
        payment_id, user_id, subscription_code, current_balance, new_balance,
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


# ── Роль пользователя ─────────────────────────────────────

def get_user_role(user_id: int) -> str:
    """Получить роль пользователя (USER, ADMIN и т.д.)."""
    conn = _get_conn()
    row = conn.execute("SELECT role FROM user_info WHERE id = ?", (user_id,)).fetchone()
    return row["role"] if row else "USER"


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


# ── Баланс и тарифы ──────────────────────────────────────

def deduct_balance(user_id: int, minutes: int) -> bool:
    """Списать минуты с баланса. Возвращает True если успешно.

    Безлимит (balance == -1) не списывается.
    """
    conn = _get_conn()
    row = conn.execute(
        "SELECT id, balance FROM selected_subscriptions WHERE user_id = ? AND is_active = 1 ORDER BY id DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    if not row:
        return False

    current = row["balance"]
    if current == -1:
        return True  # безлимит

    if current < minutes:
        return False

    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE selected_subscriptions SET balance = balance - ?, changestamp = ? WHERE id = ?",
        (minutes, now, row["id"]),
    )
    conn.commit()
    return True


def has_sufficient_balance(user_id: int) -> bool:
    """Проверить, есть ли у пользователя хоть 1 минута (или безлимит)."""
    balance = get_user_balance(user_id)
    return balance == -1 or balance > 0


def get_user_plan_info(user_id: int) -> dict:
    """Получить информацию о текущем тарифе пользователя."""
    conn = _get_conn()
    row = conn.execute(
        """
        SELECT s.code, s.name, s.amount as plan_minutes, s.price,
               ss.balance
        FROM selected_subscriptions ss
        JOIN subscriptions s ON s.id = ss.subscription_id
        WHERE ss.user_id = ? AND ss.is_active = 1
        ORDER BY ss.id DESC LIMIT 1
        """,
        (user_id,),
    ).fetchone()
    if row:
        return dict(row)
    return {"code": "free", "name": "Бесплатный", "plan_minutes": 60, "price": 0, "balance": 0}


# ── Реферальная программа ─────────────────────────────────

def get_user_ref_code(user_id: int) -> str:
    """Получить или сгенерировать реферальный код пользователя."""
    conn = _get_conn()
    row = conn.execute("SELECT ref_code FROM user_info WHERE id = ?", (user_id,)).fetchone()
    if row and row["ref_code"]:
        return row["ref_code"]
    # Генерируем код
    ref_code = uuid.uuid4().hex[:8]
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE user_info SET ref_code = ?, changestamp = ? WHERE id = ?",
        (ref_code, now, user_id),
    )
    conn.commit()
    return ref_code


def find_user_by_ref_code(ref_code: str) -> int | None:
    """Найти user_id по реферальному коду."""
    conn = _get_conn()
    row = conn.execute("SELECT id FROM user_info WHERE ref_code = ?", (ref_code,)).fetchone()
    return row["id"] if row else None


def add_referral(referrer_id: int, referred_id: int, minutes: int = 30) -> bool:
    """Создать реферальную запись и начислить минуты владельцу ссылки.

    Возвращает False если реферал уже существует или referrer == referred.
    """
    if referrer_id == referred_id:
        return False

    conn = _get_conn()
    # Проверяем, не привязан ли уже
    existing = conn.execute(
        "SELECT id FROM referrals WHERE referred_id = ?", (referred_id,)
    ).fetchone()
    if existing:
        return False

    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO referrals (referrer_id, referred_id, minutes_awarded, created_at) VALUES (?, ?, ?, ?)",
        (referrer_id, referred_id, minutes, now),
    )

    # Начислить минуты только владельцу ссылки
    row = conn.execute(
        "SELECT id, balance FROM selected_subscriptions WHERE user_id = ? AND is_active = 1 ORDER BY id DESC LIMIT 1",
        (referrer_id,),
    ).fetchone()
    if row and row["balance"] != -1:
        conn.execute(
            "UPDATE selected_subscriptions SET balance = balance + ?, changestamp = ? WHERE id = ?",
            (minutes, now, row["id"]),
        )

    conn.commit()
    logger.info("Реферал: %d пригласил %d, начислено %d мин владельцу", referrer_id, referred_id, minutes)
    return True


def get_referral_count(user_id: int) -> int:
    """Количество приглашённых пользователей."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM referrals WHERE referrer_id = ?", (user_id,)
    ).fetchone()
    return row["cnt"] if row else 0


def get_referral_minutes_earned(user_id: int) -> int:
    """Суммарно заработанных минут по рефералам."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT COALESCE(SUM(minutes_awarded), 0) as total FROM referrals WHERE referrer_id = ?",
        (user_id,),
    ).fetchone()
    return row["total"] if row else 0
