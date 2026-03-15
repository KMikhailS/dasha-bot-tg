import logging
import os

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY", "")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY", "")
S3_BUCKET = os.getenv("S3_BUCKET", "")
S3_ENDPOINT = os.getenv("S3_ENDPOINT", "https://storage.yandexcloud.net")

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = boto3.client(
            "s3",
            endpoint_url=S3_ENDPOINT,
            aws_access_key_id=S3_ACCESS_KEY,
            aws_secret_access_key=S3_SECRET_KEY,
        )
    return _client


def upload_text(user_id: int, record_id: str, text: str, suffix: str = "transcription") -> str:
    """Загрузить текст в S3. Возвращает s3_key."""
    key = f"users/{user_id}/records/{record_id}/{suffix}.txt"
    try:
        _get_client().put_object(
            Bucket=S3_BUCKET,
            Key=key,
            Body=text.encode("utf-8"),
            ContentType="text/plain; charset=utf-8",
        )
        logger.info("Загружен текст в S3: %s", key)
        return key
    except ClientError as exc:
        logger.error("Ошибка загрузки в S3: %s", exc)
        raise


def download_text(s3_key: str) -> str:
    """Скачать текст из S3."""
    try:
        response = _get_client().get_object(Bucket=S3_BUCKET, Key=s3_key)
        return response["Body"].read().decode("utf-8")
    except ClientError as exc:
        logger.error("Ошибка скачивания из S3: %s", exc)
        raise


def delete_object(s3_key: str) -> None:
    """Удалить объект из S3."""
    try:
        _get_client().delete_object(Bucket=S3_BUCKET, Key=s3_key)
        logger.info("Удалён объект из S3: %s", s3_key)
    except ClientError as exc:
        logger.error("Ошибка удаления из S3: %s", exc)
