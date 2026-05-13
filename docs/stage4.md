# Stage 4 — Production-readiness и инфраструктура

На четвёртом этапе бот переезжает с минимального стека (aiogram + Postgres + Redis)
на полноценную распределённую архитектуру: RabbitMQ для событий, Celery для
фоновых задач, MinIO для медиа, Prometheus для метрик и structlog для логов.

## Что нового

### 1. Celery + RabbitMQ (`bot/worker/`)

- **RabbitMQ** как брокер — durable очереди, topic-exchange `dating`.
- **Celery worker** слушает три очереди:
  - `ratings`     — пересчёт рейтинга конкретного пользователя.
  - `events`      — фан-аут события взаимодействия (like/skip/match/referral).
  - `maintenance` — периодические задачи (Beat).
- **Celery Beat** — расписание:
  - **Каждый час** — `recalculate_all_ratings` (страховка от пропущенных событий).
  - **03:15 UTC** ежедневно — `cleanup_old_rating_events` (удаление старше 30 дней).
- **Reliability**: `task_acks_late=True`, `reject_on_worker_lost=True`,
  retry с exponential backoff и jitter.

**Архитектурное решение про async vs sync рейтинг**:
- Рейтинг **для самого пользователя** (после сохранения профиля, добавления
  фото) считается **inline** в хэндлере — пользователь ждёт ответа в чате.
- Рейтинг **для другого пользователя** (когда ему поставили лайк/скип,
  создался мэтч, пришёл реферал) уходит в **Celery** — он не ждёт.
- Если RabbitMQ недоступен, события молча игнорируются (логируются), и Beat
  раз в час пересчитает всё; пользовательский флоу не страдает.

### 2. MinIO как S3-хранилище фото (`bot/services/storage.py`)

- При загрузке фото бот качает байты из Telegram → отправляет в MinIO →
  сохраняет MinIO-ключ в `Photo.photo_url`.
- Telegram `file_id` хранится в новой колонке `telegram_file_id` для
  быстрого re-render'а в Telegram (без скачивания).
- При удалении фото — объект в MinIO тоже удаляется.
- Бакет создаётся автоматически (idempotent), а также через `minio-setup`
  сервис в docker-compose.
- Helper `display_ref(photo)` единообразно решает, что передавать в
  `bot.send_photo` (file_id → MinIO presigned URL → fallback).

### 3. Метрики (Prometheus, `bot/services/metrics.py`)

Экспортируется на `:9100/metrics`. Категории:
- **Telegram**: `tg_updates_total{update_type}`, `handler_duration_seconds`,
  `handler_errors_total`.
- **Business**: `users_registered_total`, `likes_total`, `skips_total`,
  `matches_total`, `referrals_total`.
- **Feed cache**: `feed_refills_total`, `feed_candidates_fetched_total`,
  `feed_queue_length`.
- **Ranking**: `ranking_query_seconds` histogram — главный SLI для
  скорости выдачи.
- **Photos**: `photo_uploads_total{outcome}`, `photo_upload_seconds`.
- **Events**: `events_published_total{task}`, `events_publish_failed_total`.

Middleware `MetricsMiddleware` оборачивает каждый Telegram-update.

### 4. Structured logging (`bot/logging_config.py`)

- `structlog` с JSON-renderer в продакшене (`LOG_JSON=true`) и pretty
  console-renderer для локальной разработки.
- Stdlib `logging` мостится в structlog — логи aiogram/SQLAlchemy тоже
  попадают в JSON.
- ISO timestamps, log level, контекстные переменные через
  `structlog.contextvars`.

### 5. DB оптимизация (`alembic/versions/004_performance_indexes.py`)

Индексы под горячие запросы:
- `idx_ratings_level3_desc` — `ORDER BY level3_score DESC NULLS LAST` в выдаче.
- `idx_user_profiles_gender_age` — фильтр кандидатов.
- `idx_user_profiles_seeking_gender` — обратный фильтр пола.
- `idx_rating_events_user_event_created` — Level 2 + 24h skip-cooldown.
- `idx_rating_events_skip_recent` — **частичный** индекс только для
  `event_type='skipped'`, минимизирует размер на горячем пути.
- `idx_user_photos_user_sort` — отображение фото в порядке.

### 6. Тесты (`tests/`)

- `test_rating.py` — арифметика всех трёх уровней, кэпы, фильтры выдачи.
- `test_rating_consistency.py` — sync и async реализации рейтинга совпадают.
- `test_cache.py` — Redis-очередь фида (FIFO, TTL, refill).
- `test_events.py` — публикация в RabbitMQ корректно маршрутизуется,
  ошибки брокера не валят хэндлер.

В CI запускается на каждом push/PR в `main`.

### 7. CI/CD (`.github/workflows/ci.yml`)

- **`test`**: ставит зависимости → `compileall` (синтаксис) → миграции на
  Postgres из service-контейнера → pytest.
- **`docker-build`**: после успешных тестов собирает образ бота и валидирует
  `docker-compose config`.

### 8. Нагрузочное тестирование (`loadtest/`)

JMeter-план `dating_bot_load.jmx`: два сценария (steady 50×60s, spike 200×30s)
против `/metrics`. Параллельно через Prometheus наблюдаются прикладные
метрики (latency ранжирования, частота лайков). Подробности в
[loadtest/README.md](../loadtest/README.md).

## Соответствие рубрике ТЗ

| Пункт                  | Реализация                                                                                              | Балл  |
|------------------------|---------------------------------------------------------------------------------------------------------|-------|
| Рейтинг (все 3 уровня) | `bot/services/rating.py` + `bot/worker/rating_sync.py`                                                  | 3     |
| Redis                  | FSM-storage, кэш фида, Celery result-backend (DB1)                                                      | 2     |
| Celery                 | Worker + Beat; пересчёт рейтинга и периодические задачи                                                  | 2     |
| MQ                     | RabbitMQ как broker Celery + topic-routing для событий                                                  | 2     |
| Метрики + логи         | Prometheus `/metrics` + structlog JSON; middleware на каждый update                                    | 2     |
| S3                     | MinIO для фото, presigned URLs, отдельная `telegram_file_id` колонка                                   | 2     |
| CI/CD                  | GitHub Actions: tests + docker build                                                                    | 1     |
| Этап планирования      | `docs/architecture.md`, `db_schema.md`, `services.md` (Stage 1)                                         | 3     |
| Этап базовой функ.     | aiogram-бот, регистрация, профиль                                                                       | 3     |
| Система ранжирования   | `get_ranked_candidates` + Redis-кэш + интеграция с ботом                                                | 3     |
| База данных            | Postgres + 4 миграции; полная схема из Этапа 1                                                          | 3     |
| Ручные тесты           | Бот стабильно проходит свайп-флоу, мэтчи, рефералы                                                      | 3     |
| JMeter                 | `loadtest/dating_bot_load.jmx` — steady + spike сценарии                                                | 1     |

**Итого: 30 баллов** (план до бонусов).

## Запуск Stage 4

```bash
# 1. Поднять весь стек (Postgres + Redis + RabbitMQ + MinIO + Bot + Worker + Beat + Prometheus)
docker-compose up --build

# 2. Применить миграции (если ещё не применены автоматически)
docker-compose exec bot alembic upgrade head

# 3. Открыть управляющие UI:
#    - RabbitMQ:   http://localhost:15672  (rabbit / rabbit)
#    - MinIO:      http://localhost:9001   (minioadmin / minioadmin)
#    - Prometheus: http://localhost:9090
#    - Metrics:    http://localhost:9100/metrics
```

## Тесты локально

```bash
pip install -r requirements.txt
pytest -v
```
