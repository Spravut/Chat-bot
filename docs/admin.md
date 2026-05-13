# Админ-панель + модерация (Этап 4 / доп. баллы)

Две связанных фичи поверх Этапа 4:

1. **Web-админка** (FastAPI + Jinja2) на порту `:8000` — отдельный сервис в
   docker-compose.
2. **User-side модерация** в самом боте — кнопки «🚫 Заблокировать» и
   «🚨 Пожаловаться» в карточке анкеты.

## Архитектура

```
┌──────────────┐                ┌───────────────┐                ┌──────────────┐
│ Telegram     │                │ Bot           │                │ Postgres     │
│ user         ├──┐  message ───┤ (aiogram)     ├── block insert─┤              │
└──────────────┘  │             │               │── report insert│  users       │
                  │             │               │── is_banned?   │  user_blocks │
                  │             └───────┬───────┘                │  reports     │
                  │                     │ reads is_banned        │              │
                  │                     │                        │              │
                  │             ┌───────┴───────┐                │              │
                  │             │ Admin (FastAPI│                │              │
                  └─ browser ───┤ + Jinja2)     │── ban / dismiss┤              │
                                │ :8000         │── reads stats  │              │
                                └───────────────┘                └──────────────┘
```

Бот и админ — **независимые процессы**, общаются только через БД. Никакого
shared state в памяти, никаких внутренних API — модератор тыкает «забанить»
в браузере → выполняется `UPDATE users SET is_banned=true` → следующий
запрос бота к `get_ranked_candidates` видит флаг и исключает анкету.

## Модель данных (миграция 005)

| Таблица / колонка | Что хранит | Где используется |
|---|---|---|
| `users.is_banned` | флаг бана | filter в `get_ranked_candidates`, guard в `/start` |
| `user_blocks` (blocker_id, blocked_id) | направленные блоки | filter в обе стороны в `get_ranked_candidates`, защита в `_persist_like_and_match` |
| `reports` (reporter_id, reported_id, reason, comment, status) | жалобы | админ-страница `/admin/reports` |

Индексы:
- `idx_users_is_banned` — partial `WHERE is_banned = true` (мало строк, частый запрос)
- `idx_user_blocks_blocker_id`, `idx_user_blocks_blocked_id` — оба направления
- `idx_reports_pending` — partial `WHERE status = 'pending'` (горячий путь админки)

## User flow в боте

### Блок
1. В карточке анкеты есть кнопка «🚫 Заблокировать»
2. Тап → `INSERT user_blocks` → `clear_feed(redis)` (инвалидация очереди)
3. Бот: «🚫 Пользователь заблокирован» → следующая анкета

Блок симметричен на уровне выдачи: ни одна сторона не увидит другую в фиде.
Существующие мэтчи не удаляются, но контакт больше не показывается (выдача
матчей читает через ту же фильтрацию).

### Жалоба
FSM-флоу в три шага:
1. Тап «🚨 Пожаловаться» → state `report_choosing_reason`, показывает 4 кнопки:
   - 📢 Спам / реклама
   - 🎭 Фейк / не настоящее фото
   - 🔞 Неприемлемое содержание
   - 📝 Другое
2. Выбор причины → state `report_adding_comment`, опциональный комментарий
   (до 500 символов) или кнопка «↩️ Без комментария»
3. `INSERT reports(status='pending')` → «✅ Жалоба отправлена»

Никакого автоматического бана — все жалобы попадают модератору.

## Админка

`http://localhost:8000` — HTTP Basic auth (`ADMIN_USER` / `ADMIN_PASSWORD`
из env, дефолт `admin`/`admin` — **обязательно сменить в проде**).

| Страница | URL | Возможности |
|---|---|---|
| Пользователи | `/admin/users` | Список + поиск по имени/городу + чекбокс «Show banned» + кнопки Ban / Unban |
| Жалобы | `/admin/reports` | Pending по умолчанию, фильтры по статусу, две кнопки на каждой: «Confirm + Ban» / «Dismiss» |
| Статистика | `/admin/stats` | 10 headline-счётчиков + график регистраций/лайков/мэтчей за последние 14 дней (Chart.js) |

### Как работает «Confirm + Ban»

Одна транзакция:
1. `reports.status = 'confirmed'`, `reviewed_at = now()`
2. `users.is_banned = true` для `reports.reported_id`

После этого:
- Бот при `/start` от забаненного → «🚫 Аккаунт заблокирован»
- Анкета исчезает из всех чужих фидов на следующем `get_ranked_candidates`
- Существующие лайки/мэтчи в БД остаются (история не теряется), но банер
  больше не может взаимодействовать

### Безопасность

- `HTTPBasic` + `secrets.compare_digest` для проверки креденшелов (защита от
  timing attack)
- Креденшелы из env, не хардкод
- Все mutating-эндпоинты — POST, не GET (защита от CSRF через картинки)
- Confirm-диалоги в шаблонах перед бан/dismiss

## Запуск

```bash
# Полный стек теперь включает admin на :8000
docker-compose up --build

# Применить миграцию (если поднимаешь поверх старой БД)
docker-compose exec bot alembic upgrade head

# Открыть в браузере
# http://localhost:8000/admin
# Логин: admin / admin (или ADMIN_USER/ADMIN_PASSWORD из .env)
```

## Тесты

- `tests/test_moderation.py` — фильтрация забаненных, обе стороны блока,
  CHECK constraint на self-report, server-default `status='pending'`
- `tests/test_admin.py` — auth (401 без креденшелов, 401 с неверным паролем,
  200 с верным), все 3 страницы загружаются, `/healthz` публичный

Тесты админки — интеграционные (нужен Postgres), запускаются с
`INTEGRATION_PG_URL`. Юнит-тесты модерации идут на SQLite без env-флага.

## Соответствие рубрике («Другое» — по 2 балла за каждый пункт)

1. **Веб-админка на FastAPI + Jinja2** — две новых для проекта технологии
   (FastAPI, Jinja2 templates), реальная функциональность (бан/анбан, разбор
   жалоб, статистика).
2. **User-side модерация (блок/жалобы)** — две новые таблицы, новая FSM-цепочка
   в боте, end-to-end интеграция с админкой (жалоба → модератор → бан).
