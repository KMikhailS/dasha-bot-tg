import hashlib
import logging
import uuid

import httpx

from bot.config import TBANK_TERMINAL_KEY, TBANK_PASSWORD, TBANK_TAXATION

logger = logging.getLogger(__name__)

TBANK_API_URL = "https://securepay.tinkoff.ru/v2"
RETURN_URL = "https://t.me"


def _generate_token(params: dict) -> str:
    """Сгенерировать подпись (Token) для запроса к T-Bank API.

    Алгоритм:
    1. Добавить Password в словарь параметров
    2. Исключить вложенные объекты (dict, list) — только скалярные значения
    3. Отсортировать по ключам
    4. Склеить значения в строку
    5. SHA-256 хеш
    """
    sign_params = {**params, "Password": TBANK_PASSWORD}
    # Исключаем вложенные объекты (Receipt, Items и т.д.)
    flat_params = {k: v for k, v in sign_params.items() if not isinstance(v, (dict, list))}
    sorted_values = "".join(str(flat_params[k]) for k in sorted(flat_params))
    return hashlib.sha256(sorted_values.encode("utf-8")).hexdigest()


def _build_receipt(amount: int, description: str, phone: str) -> dict:
    """Сформировать объект Receipt для T-Bank API (54-ФЗ).

    Args:
        amount: сумма в рублях.
        description: название позиции.
        phone: телефон покупателя в формате +7XXXXXXXXXX.
    """
    amount_kopecks = amount * 100
    return {
        "Phone": phone,
        "Taxation": TBANK_TAXATION,
        "Items": [
            {
                "Name": description,
                "Price": amount_kopecks,
                "Quantity": 1.0,
                "Amount": amount_kopecks,
                "Tax": "none",
                "PaymentMethod": "full_payment",
                "PaymentObject": "service",
            }
        ],
    }


def create_payment(amount: int, description: str, phone: str) -> tuple[str, str] | None:
    """Создать платёж в T-Bank с чеком (54-ФЗ).

    Args:
        amount: сумма в рублях (целое число).
        description: описание платежа.
        phone: телефон покупателя в формате +7XXXXXXXXXX.

    Returns:
        (payment_id, payment_url) или None при ошибке.
    """
    order_id = uuid.uuid4().hex

    params = {
        "TerminalKey": TBANK_TERMINAL_KEY,
        "Amount": amount * 100,  # T-Bank принимает сумму в копейках
        "OrderId": order_id,
        "Description": description,
    }
    params["Token"] = _generate_token(params)
    # Receipt добавляем после генерации Token — вложенные объекты не участвуют в подписи
    params["Receipt"] = _build_receipt(amount, description, phone)

    try:
        resp = httpx.post(
            f"{TBANK_API_URL}/Init",
            json=params,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        if not data.get("Success"):
            logger.error("Ошибка T-Bank Init: %s %s", data.get("ErrorCode"), data.get("Message"))
            return None

        payment_id = str(data.get("PaymentId", ""))
        payment_url = data.get("PaymentURL", "")
        if payment_id and payment_url:
            logger.info("Создан платёж %s (OrderId=%s), URL: %s", payment_id, order_id, payment_url)
            return payment_id, payment_url
        logger.error("Нет PaymentId или PaymentURL в ответе T-Bank: %s", data)
        return None
    except httpx.HTTPError as exc:
        logger.error("Ошибка создания платежа в T-Bank: %s", exc)
        return None


def get_payment_status(payment_id: str) -> str | None:
    """Получить статус платежа из T-Bank.

    Returns:
        Нормализованный статус: 'succeeded', 'canceled', 'pending' или None при ошибке.
    """
    params = {
        "TerminalKey": TBANK_TERMINAL_KEY,
        "PaymentId": payment_id,
    }
    params["Token"] = _generate_token(params)

    try:
        resp = httpx.post(
            f"{TBANK_API_URL}/GetState",
            json=params,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        raw_status = data.get("Status", "")
        logger.info("Статус платежа %s: %s", payment_id, raw_status)

        # Маппинг статусов T-Bank → внутренние статусы
        if raw_status == "CONFIRMED":
            return "succeeded"
        if raw_status in ("REJECTED", "CANCELED", "DEADLINE_EXPIRED", "AUTH_FAIL"):
            return "canceled"
        return "pending"
    except httpx.HTTPError as exc:
        logger.error("Ошибка получения статуса платежа %s: %s", payment_id, exc)
        return None
