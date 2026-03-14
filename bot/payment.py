import logging
import uuid

import httpx

from bot.config import YOKASSA_SHOP_ID, YOKASSA_SECRET_KEY

logger = logging.getLogger(__name__)

YOKASSA_API_URL = "https://api.yookassa.ru/v3/payments"
RETURN_URL = "https://t.me"


def create_payment(amount: int, description: str) -> tuple[str, str] | None:
    """Создать платёж в ЮKassa.

    Args:
        amount: сумма в рублях (целое число).
        description: описание платежа.

    Returns:
        (payment_id, confirmation_url) или None при ошибке.
    """
    idempotence_key = uuid.uuid4().hex

    payload = {
        "amount": {
            "value": f"{amount}.00",
            "currency": "RUB",
        },
        "confirmation": {
            "type": "redirect",
            "return_url": RETURN_URL,
        },
        "capture": True,
        "description": description,
    }

    try:
        resp = httpx.post(
            YOKASSA_API_URL,
            json=payload,
            auth=(YOKASSA_SHOP_ID, YOKASSA_SECRET_KEY),
            headers={
                "Idempotence-Key": idempotence_key,
                "Content-Type": "application/json",
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        payment_id = data.get("id")
        confirmation_url = data.get("confirmation", {}).get("confirmation_url")
        if payment_id and confirmation_url:
            logger.info("Создан платёж %s, URL: %s", payment_id, confirmation_url)
            return payment_id, confirmation_url
        logger.error("Нет id или confirmation_url в ответе ЮKassa: %s", data)
        return None
    except httpx.HTTPError as exc:
        logger.error("Ошибка создания платежа в ЮKassa: %s", exc)
        return None


def get_payment_status(payment_id: str) -> str | None:
    """Получить статус платежа из ЮKassa.

    Returns:
        Статус платежа ('pending', 'succeeded', 'canceled' и др.) или None при ошибке.
    """
    try:
        resp = httpx.get(
            f"{YOKASSA_API_URL}/{payment_id}",
            auth=(YOKASSA_SHOP_ID, YOKASSA_SECRET_KEY),
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        status = data.get("status")
        logger.info("Статус платежа %s: %s", payment_id, status)
        return status
    except httpx.HTTPError as exc:
        logger.error("Ошибка получения статуса платежа %s: %s", payment_id, exc)
        return None
