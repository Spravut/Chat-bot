# Grafana dashboard + Anti-spam rate limiting

Две доп. фичи Этапа 4 (по 2 балла рубрики «Другое»).

## 1. Grafana dashboard

**Что**: преднастроенный dashboard «Dating Bot — Overview», который
визуализирует все `dating_bot_*` метрики из Prometheus.

**Зачем не Prometheus напрямую**: Prometheus UI — это интерфейс для query'ев,
не для постоянного мониторинга. Grafana — стандартный фронтенд: панели
обновляются автоматически, история сохраняется, легко собрать "один экран
со всем что нужно".

### Архитектура

```
Bot (:9100/metrics) ──scrape every 15s──> Prometheus (:9090)
                                              │
                                              │ query API
                                              ▼
                                          Grafana (:3000)
                                              │
                                              │ provisioned at startup:
                                              ├─ datasource → http://prometheus:9090
                                              └─ dashboard  → /var/lib/grafana/dashboards/
```

Файлы:
- [grafana/provisioning/datasources/prometheus.yml](../grafana/provisioning/datasources/prometheus.yml) — авто-конфигурация Prometheus как источника данных
- [grafana/provisioning/dashboards/dashboards.yml](../grafana/provisioning/dashboards/dashboards.yml) — provider, который сканирует папку с дашбордами
- [grafana/dashboards/dating-bot.json](../grafana/dashboards/dating-bot.json) — сам дашборд

При первом старте Grafana читает provisioning и создаёт всё. Никаких ручных
настроек через UI — open-and-go.

### Что на дашборде

**Top row — 6 счётчиков**: total likes, skips, matches, registrations,
referrals, rate-limited events.

**Rate panels** (1m rate):
- User actions per second (likes/skips/matches/registrations) на одном графике
- Telegram updates по типу (message / callback_query)

**Latency panels** (histogram quantile):
- Ranking query latency p50/p95/p99 — это SLI для скорости выдачи
- Handler duration p95 по каждому handler'у (видно какой медленнее)

**Operational panels**:
- Feed cache: rate of refills / candidates fetched
- Photo uploads: success vs failed
- RabbitMQ event publishing: ok vs failed (по task'у)
- Rate-limited actions per second
- Handler errors

### Как открыть

```
http://localhost:3000
Login: admin / admin  (или GRAFANA_USER/GRAFANA_PASSWORD из env)
```

Dashboard называется «Dating Bot — Overview». При первом входе будет
доступен сразу в Home.

## 2. Anti-spam rate limiting

**Что**: ограничение частоты «дорогих» действий в боте — лайков и жалоб.
Защищает от двух абьюзов:
- спам-бот лайкает 1000 анкет в минуту (накручивает рейтинг + засирает БД)
- юзер заваливает админку жалобами на одного и того же человека

### Алгоритм

Fixed-window counter в Redis. На каждое действие:
1. `INCR rl:{action}:{user_id}`
2. На первом инкременте: `EXPIRE` = размер окна
3. Если счётчик ≤ лимит → разрешено
4. Иначе → отказ, возвращаем TTL ключа как retry_after

Почему не token bucket / sliding window:
- Token bucket требует Lua-скрипт для атомарности — больше кода.
- Sliding window log хранит timestamp каждого hit'а — больше памяти.
- Для нашего use-case (поймать спам-бота с 1000 действий) обе аномалии
  fixed-window'а (boundary burst) несущественны: легитимный rate в десятки
  раз ниже лимита, спамер всё равно будет превышен.

### Политики (defaults)

| Действие | Лимит | Окно | Где срабатывает | Тюнинг через env |
|---|---|---|---|---|
| Любое сообщение/нажатие | 20 | 10 сек | `RateLimitMiddleware` (на каждый update) | `RATE_LIMIT_MESSAGES` / `_WINDOW` |
| Лайк | 30 | 60 сек | `_do_like` перед SERIALIZABLE | `RATE_LIMIT_LIKES` / `_WINDOW` |
| Жалоба | 5 | 300 сек | `cb_report_start` перед FSM | `RATE_LIMIT_REPORTS` / `_WINDOW` |

Слои:
1. **Глобальный** (20/10s) — middleware. Ловит любой спам командами/кнопками,
   дропает update'ы **до** открытия DB-сессии. Первая блокировка → одно
   предупреждение в чат; последующие в том же окне — молча. Защита от
   повторов реализована через ключ `rl_warned:{user_id}` (SETNX с TTL =
   размер окна).
2. **Per-action** — лайки и жалобы имеют более жёсткие лимиты, поскольку это
   "дорогие" в смысле БД-нагрузки и злоупотреблений действия.

30 лайков в минуту достаточно для нормального пользователя и блокирует
любую массовую спам-атаку. 5 жалоб за 5 минут — даже сильно недовольный
пользователь не накопит больше.

Для **демо** значения тут специально не очень большие: можно реально
триггернуть лимит за 30 секунд кликов.

### UX когда лимит превышен

- **Like**: бот пишет «⚠️ Слишком много лайков подряд. Подожди X сек.»
  и просто **не** обрабатывает лайк. Анкета остаётся на месте.
- **Report**: callback `answer(show_alert=True)` показывает попап
  «⚠️ Слишком много жалоб. Подожди X сек.» — пользователь видит причину
  без захламления чата.

### Метрика

```promql
rate(dating_bot_rate_limited_total[1m])
```

Лейбл `action` (`like` / `report`) — видно по чему именно бьёт лимит.
На Grafana панель «Rate-limited actions per second» уже на дашборде.

### Где НЕ применяется

- **Skip** — это лёгкая операция, не имеет смысла ограничивать. Юзер
  может скипать сколько угодно — это поведение нормальной "Tinder-сессии".
- **Profile / Photos / Browse list** — read-only действия, спам тут не
  опасен.
- **Block** — частая блокировка тоже не вредна, наоборот, мы хотим чтобы
  юзеры легко блокировали неприятных людей.

## Тесты

`tests/test_ratelimit.py` — 5 unit-тестов на FakeRedis:
- В пределах лимита разрешено
- За пределами — denied + правильный TTL
- На первом hit'е выставляется EXPIRE
- Счётчики per-user независимы
- Счётчики per-action независимы

```powershell
python -m pytest tests/test_ratelimit.py -v
```

## Демо на защите

**Часть 1 — Grafana** (60 сек):
1. Открой http://localhost:3000 (admin/admin)
2. Dashboards → «Dating Bot — Overview»
3. Покажи top-row счётчики и latency panel
4. Сделай несколько лайков в боте → панели «Telegram updates» и
   «User actions per second» обновляются через ~15 сек (scrape interval)

**Часть 2 — Rate limiting** (45 сек):
1. В боте быстро лайкни 30 анкет (или временно понизь `RATE_LIMIT_LIKES=3`
   в `.env` перед демо)
2. На следующем лайке бот ответит «⚠️ Слишком много лайков подряд...»
3. Открой Grafana → панель «Rate-limited actions per second» → видно всплеск

## По баллам

| Фича | Что нового | Балл |
|------|-----------|------|
| Grafana | Новая технология (Grafana), provisioning через файлы, готовый dashboard с 14 панелями | +2 |
| Rate limiting | Новое применение Redis (counter вместо queue), защита от спама, метрика + панель | +2 |

**Суммарно за всю Этап 4**: 31 (с админкой и блоками) + 4 = **35 баллов**.
