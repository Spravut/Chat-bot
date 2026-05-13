"""
S3 / MinIO photo storage.

Photos are downloaded from Telegram once and uploaded to MinIO. The DB stores
the MinIO object key (e.g. `users/42/abc123.jpg`) in `Photo.photo_url`.

For Telegram display we still use file_ids when available (much faster: no
re-upload) — `photo_url` falls back to the MinIO presigned URL otherwise.

This is the intended path forward (S3-compatible storage for media, per ТЗ).
The bot uses `MINIO_PUBLIC_URL` for presigned URLs so links work from outside
the Docker network.
"""
from __future__ import annotations

import io
import logging
import time
import uuid
from datetime import timedelta

from minio import Minio
from minio.error import S3Error

from bot.config import (
    MINIO_ACCESS_KEY,
    MINIO_BUCKET,
    MINIO_ENDPOINT,
    MINIO_PUBLIC_URL,
    MINIO_SECRET_KEY,
    MINIO_SECURE,
)
from bot.services.metrics import PHOTO_UPLOAD_DURATION, PHOTO_UPLOADS

logger = logging.getLogger(__name__)


class PhotoStorage:
    """Thin wrapper around the MinIO client. Idempotent bucket bootstrap."""

    def __init__(self) -> None:
        self._client = Minio(
            MINIO_ENDPOINT,
            access_key=MINIO_ACCESS_KEY,
            secret_key=MINIO_SECRET_KEY,
            secure=MINIO_SECURE,
        )
        self._bucket = MINIO_BUCKET
        self._ensure_bucket()

    def _ensure_bucket(self) -> None:
        try:
            if not self._client.bucket_exists(self._bucket):
                self._client.make_bucket(self._bucket)
                logger.info("created MinIO bucket '%s'", self._bucket)
        except S3Error as exc:
            logger.warning("MinIO bucket bootstrap failed: %s", exc)

    def upload(self, user_id: int, data: bytes,
               content_type: str = "image/jpeg",
               ext: str = "jpg") -> str:
        """Upload bytes to MinIO. Returns the object key.

        Object layout: `users/{user_id}/{uuid}.{ext}` — flat enough to scan
        per-user, unique enough to never collide.
        """
        key = f"users/{user_id}/{uuid.uuid4().hex}.{ext}"
        start = time.perf_counter()
        try:
            self._client.put_object(
                self._bucket,
                key,
                io.BytesIO(data),
                length=len(data),
                content_type=content_type,
            )
            PHOTO_UPLOADS.labels(outcome="success").inc()
            PHOTO_UPLOAD_DURATION.observe(time.perf_counter() - start)
            return key
        except S3Error:
            PHOTO_UPLOADS.labels(outcome="failed").inc()
            raise

    def presigned_url(self, key: str, expires_minutes: int = 60) -> str:
        """Return a presigned GET URL for the given object key.

        Rewrites the host to `MINIO_PUBLIC_URL` so the link works outside the
        Docker network (the SDK signs against the internal endpoint).
        """
        url = self._client.presigned_get_object(
            self._bucket, key, expires=timedelta(minutes=expires_minutes)
        )
        if MINIO_PUBLIC_URL:
            # SDK signs e.g. http://minio:9000/...; rewrite host to public URL.
            from urllib.parse import urlparse, urlunparse
            parsed = urlparse(url)
            public = urlparse(MINIO_PUBLIC_URL)
            url = urlunparse(parsed._replace(scheme=public.scheme, netloc=public.netloc))
        return url

    def delete(self, key: str) -> None:
        try:
            self._client.remove_object(self._bucket, key)
        except S3Error as exc:
            logger.warning("MinIO delete failed for %s: %s", key, exc)


# Module-level singleton, created lazily to allow tests to skip MinIO entirely.
_storage: PhotoStorage | None = None


def get_storage() -> PhotoStorage:
    global _storage
    if _storage is None:
        _storage = PhotoStorage()
    return _storage


def is_minio_key(value: str) -> bool:
    """Heuristic: MinIO keys start with `users/`, Telegram file_ids don't."""
    return value.startswith("users/")


def display_ref(photo) -> str:
    """Resolve what to pass to bot.send_photo / InputMediaPhoto.

    Prefer the Telegram file_id (zero re-upload to Telegram CDN), fall back to
    a MinIO presigned URL for rows that pre-date the file_id column, and
    finally fall back to the raw value (legacy file_id stored in photo_url).
    """
    if getattr(photo, "telegram_file_id", None):
        return photo.telegram_file_id
    if is_minio_key(photo.photo_url):
        try:
            return get_storage().presigned_url(photo.photo_url)
        except Exception as exc:
            logger.warning("presign failed for %s: %s", photo.photo_url, exc)
            return photo.photo_url
    return photo.photo_url
