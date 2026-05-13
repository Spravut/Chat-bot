import os
from dotenv import load_dotenv

load_dotenv()

# ── Bot / Telegram ────────────────────────────────────────────────────────────

BOT_TOKEN: str = os.environ.get("BOT_TOKEN", "")

# ── PostgreSQL ────────────────────────────────────────────────────────────────

DATABASE_URL: str = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/dating_bot",
)
# Sync URL is needed for Celery workers (Celery is sync; we use psycopg sync driver).
DATABASE_URL_SYNC: str = os.environ.get(
    "DATABASE_URL_SYNC",
    DATABASE_URL.replace("+asyncpg", "+psycopg"),
)

# ── Redis ─────────────────────────────────────────────────────────────────────

REDIS_URL: str = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

# ── RabbitMQ / Celery ─────────────────────────────────────────────────────────

RABBITMQ_URL: str = os.environ.get(
    "RABBITMQ_URL", "amqp://rabbit:rabbit@localhost:5672//"
)
CELERY_BROKER_URL: str = os.environ.get("CELERY_BROKER_URL", RABBITMQ_URL)
CELERY_RESULT_BACKEND: str = os.environ.get(
    "CELERY_RESULT_BACKEND", "redis://localhost:6379/1"
)

# ── MinIO / S3 ────────────────────────────────────────────────────────────────

MINIO_ENDPOINT: str = os.environ.get("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY: str = os.environ.get("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY: str = os.environ.get("MINIO_SECRET_KEY", "minioadmin")
MINIO_BUCKET: str = os.environ.get("MINIO_BUCKET", "photos")
# Public URL (for the bot to send to Telegram if direct links are desired).
MINIO_PUBLIC_URL: str = os.environ.get("MINIO_PUBLIC_URL", "http://localhost:9000")
MINIO_SECURE: bool = os.environ.get("MINIO_SECURE", "false").lower() == "true"

# ── Metrics ───────────────────────────────────────────────────────────────────

METRICS_PORT: int = int(os.environ.get("METRICS_PORT", "9100"))
METRICS_ENABLED: bool = os.environ.get("METRICS_ENABLED", "true").lower() == "true"
