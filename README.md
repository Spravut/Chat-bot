# Chat-bot (Dating App)

Dating-бот для Telegram: анкеты, лайки/матчи и система ранжирования пользователей (уровни 1–3).

## Стек

| Технология | Роль |
|---|---|
| `aiogram 3` | Async-фреймворк для Telegram Bot API |
| `PostgreSQL` | Основная база данных |
| `SQLAlchemy 2 (async)` | ORM |
| `asyncpg` | Async-драйвер PostgreSQL |
| `Alembic` | Версионные миграции схемы БД |
| `Redis` | FSM-состояния + кеш фида кандидатов |
| `Docker / docker-compose` | Запуск всего стека одной командой |

---

## Быстрый старт

```bash
# Вариант 1 — Docker (PostgreSQL + Redis + бот)
docker-compose up --build

# Вариант 2 — локально
pip install -r requirements.txt
alembic upgrade head
python -m bot.main
```

`.env` (скопировать из `.env.example`):
```
BOT_TOKEN=...
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/dating_bot
REDIS_URL=redis://localhost:6379/0
```

---

## Этапы разработки

### Этап 1: Планирование и проектирование ✅

- `docs/architecture.md` — mermaid-диаграмма архитектуры
- `docs/db_schema.md` — ER-диаграмма и описание таблиц
- `docs/services.md` — ответственность сервисов

### Этап 2: Базовая функциональность ✅

- FSM-регистрация через aiogram
- Хранение профилей в PostgreSQL
- Docker-окружение с healthcheck

### Этап 3: Профили, рейтинг и матчи ✅ ← текущий этап

- **Анкетирование** — полный FSM-флоу: имя → возраст → пол → кого ищет → город → о себе → возраст партнёра (min/max)
- **Фотографии** — загрузка, удаление, смена порядка; до 5 фото на профиль
- **Свайп-лента** (`/browse`) — просмотр анкет с лайком / скипом
- **Матчи** (`/matches`) — взаимные лайки, показ контакта (@username)
- **Рейтинг** — трёхуровневый алгоритм (полнота анкеты + поведение + рефералы)
- **Кеш фида** — очередь кандидатов в Redis (TTL 30 мин, дополняется при исчерпании)
- **Реферальная система** — бонус к рейтингу за приглашённых пользователей
- **Редактирование профиля** — перезапуск FSM с сохранением referral-истории

---

## Архитектура

```
Telegram Update
      │
      ▼
  Dispatcher (aiogram)
      │
      ├── DatabaseMiddleware  → session: AsyncSession (на каждый апдейт)
      │                         redis: Redis (через dp["redis"])
      │
      ├── start.router        — /start, реферальные ссылки
      ├── registration.router — FSM регистрации / редактирования
      ├── profile.router      — просмотр анкеты, фото
      ├── browse.router       — свайп, лайк, скип
      ├── photos.router       — загрузка и управление фото
      └── matches.router      — список матчей
```

### Рейтинговая система (`bot/services/rating.py`)

| Уровень | Что считает | Макс. |
|---|---|---|
| Level 1 | Полнота анкеты + фото | 10.0 |
| Level 2 | Лайки, соотношение лайк/скип, матчи | 10.0 |
| Level 3 | `L1 × 0.4 + L2 × 0.6 + бонус рефералов` | — |

Рейтинг пересчитывается при: сохранении профиля, добавлении/удалении фото, получении лайка, создании реферала.

### Флоу регистрации

```
/start
  ├─ новый пользователь → FSM:
  │    имя → возраст → пол → кого ищет → город → о себе → возраст партнёра (min/max)
  │    → сохранение User + UserProfile → пересчёт рейтинга → главное меню
  │
  └─ уже зарегистрирован → приветствие по имени + главное меню
```

---

## Структура проекта

```
Chat-bot/
├── bot/
│   ├── config.py
│   ├── main.py                  # сборка приложения, подключение роутеров
│   ├── db/
│   │   ├── models.py            # SQLAlchemy-модели всех таблиц
│   │   └── session.py
│   ├── handlers/
│   │   ├── start.py
│   │   ├── registration.py
│   │   ├── profile.py
│   │   ├── browse.py
│   │   ├── photos.py
│   │   └── matches.py
│   ├── services/
│   │   ├── rating.py            # трёхуровневый рейтинг + ранжирование кандидатов
│   │   └── cache.py             # Redis-кеш фида
│   ├── keyboards/
│   │   ├── reply.py
│   │   └── inline.py
│   ├── middlewares/
│   │   └── db.py
│   └── states/
│       ├── registration.py
│       ├── browse.py
│       └── photos.py
├── alembic/
│   └── versions/
│       ├── 001_initial_schema.py
│       └── 002_add_username.py
├── docs/                        # документация Этапа 1
├── recalc_ratings.py            # разовый пересчёт рейтинга всех пользователей
├── alembic.ini
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```
