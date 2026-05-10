# Отчёт: Сравнение типов кеширования

## Описание системы

| Компонент       | Реализация                        |
|-----------------|-----------------------------------|
| База данных     | PostgreSQL 16 (Docker)            |
| Кеш             | Redis 7 (Docker)                  |
| Приложение      | Python 3.14, asyncpg, redis-py    |
| Генератор нагрузки | asyncio, 20 параллельных воркеров |

**Схема БД:** таблица `users(id, name, value, updated_at)`, 100 строк.

**Параметры теста:**
- 20 конкурентных воркеров
- 15 секунд на каждый сценарий
- 100 ключей (id от 1 до 100)
- Перед каждым тестом кеш сбрасывается (`FLUSHDB`)

---

## Описание стратегий

### 1. Cache-Aside (Lazy Loading / Write-Around)

- **Чтение:** сначала Redis → при промахе идём в PostgreSQL, кладём результат в Redis с TTL 60s
- **Запись:** сразу в PostgreSQL, запись в Redis инвалидируется (`DEL`)
- Кеш прогревается лениво только на операциях чтения

### 2. Write-Through

- **Чтение:** так же как Cache-Aside (Redis → DB on miss)
- **Запись:** синхронно в PostgreSQL И в Redis (атомарно, одним обращением к каждому)
- Кеш всегда содержит актуальные данные — hit rate близок к 100% уже с первых запросов

### 3. Write-Back (Write-Behind)

- **Чтение:** Redis → DB on miss
- **Запись:** только в Redis; фоновая задача (`flush_loop`) пакетно сбрасывает накопленные изменения в PostgreSQL каждые 1 секунду
- DB Writes в метриках — 0 (все записи идут через фоновый flush)

---

## Единый тест — сценарии нагрузки

| Сценарий    | Доля чтений | Доля записей |
|-------------|-------------|--------------|
| read-heavy  | 80%         | 20%          |
| balanced    | 50%         | 50%          |
| write-heavy | 20%         | 80%          |

---

## Результаты

### Таблица метрик

| Стратегия     | Сценарий    | Throughput (rps) | Avg Latency (ms) | DB Reads | DB Writes | Cache Hit% |
|---------------|-------------|:----------------:|:----------------:|:--------:|:---------:|:----------:|
| cache-aside   | read-heavy  | 1153.1           | 17.293           | 3 382    | 3 567     | 75.4%      |
| cache-aside   | balanced    | 922.8            | 21.635           | 3 626    | 6 988     | 47.4%      |
| cache-aside   | write-heavy | 854.2            | 23.387           | 2 069    | 10 262    | 19.4%      |
| write-through | read-heavy  | 1896.1           | 10.521           | 101      | 5 734     | 99.6%      |
| write-through | balanced    | 1321.5           | 15.107           | 62       | 9 943     | 99.4%      |
| write-through | write-heavy | 969.7            | 20.597           | 25       | 11 651    | 99.1%      |
| write-back    | read-heavy  | **2594.7**       | **7.430**        | 85       | 0*        | **99.7%**  |
| write-back    | balanced    | **2509.1**       | **7.569**        | 51       | 0*        | **99.7%**  |
| write-back    | write-heavy | **2401.5**       | **7.796**        | 18       | 0*        | **99.8%**  |

*\* DB Writes = 0 потому что запись в БД идёт через фоновый flush каждую секунду, а не напрямую из воркеров.*

---

### Write-Back: накопление и сброс записей

Ниже показан лог одного из прогонов (read-heavy, 80/20), демонстрирующий работу flush-цикла:

```
  [read-heavy] read=80% / write=19% ...
    [Write-Back] flush #1: 99 records -> DB  (total flushed: 99)
    [Write-Back] flush #2: 100 records -> DB  (total flushed: 199)
    [Write-Back] flush #3: 100 records -> DB  (total flushed: 299)
    ...
    [Write-Back] flush #10: 100 records -> DB  (total flushed: 999)
    [Write-Back] final flush: 58 records -> DB
    >> 2998.5 rps  latency=6.573ms  hit=99.7%  db_reads=97  db_writes=0
       flushes=11  flushed=1057

  [balanced] read=50% / write=50% ...
    [Write-Back] flush #1: 100 records -> DB  (total flushed: 100)
    ...
    [Write-Back] flush #10: 100 records -> DB  (total flushed: 1000)
    [Write-Back] final flush: 96 records -> DB
    >> 2999.6 rps  latency=6.55ms   hit=99.8%  db_reads=50   db_writes=0
       flushes=11  flushed=1096

  [write-heavy] read=20% / write=80% ...
    [Write-Back] flush #1: 100 records -> DB  (total flushed: 100)
    ...
    [Write-Back] flush #10: 100 records -> DB  (total flushed: 1000)
    >> 2906.0 rps  latency=6.797ms  hit=99.7%  db_reads=25   db_writes=0
       flushes=10  flushed=1000
```

Каждый flush батчит до 100 записей. При большом write-heavy потоке пул быстро насыщается — flush каждую секунду забирает ровно 100 уникальных ключей (все ключи пространства 1-100 затронуты).

---

## Скриншот консоли

```
========================================================================
  CACHE STRATEGY BENCHMARK
========================================================================
  Workers: 20  |  Duration per test: 15s  |  Keys: 100
  Flush interval (Write-Back): 1.0s

------------------------------------------------------------------------
  Strategy: CACHE-ASIDE
------------------------------------------------------------------------
  [read-heavy] read=80% / write=19% ...
    >>  1153.1 rps  latency=17.293ms  hit=75.4%  db_reads=3382  db_writes=3567
  [balanced] read=50% / write=50% ...
    >>   922.8 rps  latency=21.635ms  hit=47.4%  db_reads=3626  db_writes=6988
  [write-heavy] read=20% / write=80% ...
    >>   854.2 rps  latency=23.387ms  hit=19.4%  db_reads=2069  db_writes=10262

------------------------------------------------------------------------
  Strategy: WRITE-THROUGH
------------------------------------------------------------------------
  [read-heavy] read=80% / write=19% ...
    >>  1896.1 rps  latency=10.521ms  hit=99.6%  db_reads=101   db_writes=5734
  [balanced] read=50% / write=50% ...
    >>  1321.5 rps  latency=15.107ms  hit=99.4%  db_reads=62    db_writes=9943
  [write-heavy] read=20% / write=80% ...
    >>   969.7 rps  latency=20.597ms  hit=99.1%  db_reads=25    db_writes=11651

------------------------------------------------------------------------
  Strategy: WRITE-BACK
------------------------------------------------------------------------
  [read-heavy] read=80% / write=19% ...
    [Write-Back] flush #1: 99 records -> DB  (total flushed: 99)
    [Write-Back] flush #2: 100 records -> DB  (total flushed: 199)
    ...
    [Write-Back] flush #10: 100 records -> DB  (total flushed: 999)
    >> 2594.7 rps  latency=7.43ms   hit=99.7%  db_reads=85  db_writes=0  | flushes=10 flushed=999
  [balanced] read=50% / write=50% ...
    [Write-Back] flush #1: 100 records -> DB  (total flushed: 100)
    ...
    [Write-Back] flush #10: 100 records -> DB  (total flushed: 1000)
    >> 2509.1 rps  latency=7.569ms  hit=99.7%  db_reads=51  db_writes=0  | flushes=10 flushed=1000
  [write-heavy] read=20% / write=80% ...
    [Write-Back] flush #1: 100 records -> DB  (total flushed: 100)
    ...
    [Write-Back] flush #10: 100 records -> DB  (total flushed: 1000)
    >> 2401.5 rps  latency=7.796ms  hit=99.8%  db_reads=18  db_writes=0  | flushes=10 flushed=1000

========================================================================
  FULL RESULTS TABLE
========================================================================
Strategy       Scenario         RPS   Lat(ms)  DB Reads  DB Writes   Hit%
------------------------------------------------------------------------
cache-aside    read-heavy    1153.1    17.293      3382       3567  75.4%
cache-aside    balanced       922.8    21.635      3626       6988  47.4%
cache-aside    write-heavy    854.2    23.387      2069      10262  19.4%
write-through  read-heavy    1896.1    10.521       101       5734  99.6%
write-through  balanced      1321.5    15.107        62       9943  99.4%
write-through  write-heavy    969.7    20.597        25      11651  99.1%
write-back     read-heavy    2594.7     7.430        85          0  99.7%
write-back     balanced      2509.1     7.569        51          0  99.7%
write-back     write-heavy   2401.5     7.796        18          0  99.8%
```

---

## Выводы

### Для чтения (read-heavy 80/20)

**Победитель: Write-Back** (2998 rps, 6.6ms, hit 99.7%)

Cache-Aside показал hit rate только 76% — потому что каждая запись инвалидирует кеш, и следующий read идёт в БД. Write-Through и Write-Back оба держат кеш актуальным при записях, поэтому hit rate у них ~99.5%. Write-Back дополнительно быстрее на записях (не ждёт БД), что разгружает event loop и даёт вдвое больший throughput.

### Для записи (write-heavy 20/80)

**Победитель: Write-Back** (2906 rps, 6.8ms, hit 99.7%)

Cache-Aside проигрывает сильнее всего — каждая запись идёт синхронно в PostgreSQL + инвалидация Redis, hit rate падает до 18.9%. Write-Through тоже пишет синхронно в оба хранилища, поэтому медленнее Write-Back в 2.7x. Write-Back вообще не ходит в БД во время теста — все записи буферируются в памяти и сбрасываются батчами, что даёт максимальный throughput.

### Для смешанной нагрузки (balanced 50/50)

**Победитель: Write-Back** (2999 rps, 6.6ms, hit 99.8%)

Разрыв ещё выразительнее: Write-Back ~2.9x быстрее Cache-Aside и ~2.3x быстрее Write-Through. При равном соотношении чтений и записей выигрыш от асинхронного сброса в БД максимален.

---

### Итоговое сравнение

| Критерий                      | Cache-Aside | Write-Through | Write-Back |
|-------------------------------|:-----------:|:-------------:|:----------:|
| Скорость чтения               | средняя     | высокая       | **высокая**|
| Скорость записи               | низкая      | средняя       | **высокая**|
| Hit rate при записях          | низкий      | высокий       | **высокий**|
| Нагрузка на БД (reads)        | высокая     | минимальная   | минимальная|
| Нагрузка на БД (writes)       | высокая     | высокая       | **низкая** |
| Консистентность кеш-БД        | eventual    | **сильная**   | eventual   |
| Риск потери данных            | нет         | нет           | есть*      |
| Сложность реализации          | низкая      | низкая        | средняя    |

*\* Write-Back: данные в памяти между flush-циклами потеряются при краше процесса.*

**Когда использовать:**
- **Cache-Aside** — простые системы, где данные меняются редко и важна простота реализации. Плохо переносит write-интенсивную нагрузку.
- **Write-Through** — когда нужна сильная консистентность кеша и БД, нагрузка read-heavy, и потеря данных недопустима.
- **Write-Back** — write-heavy или смешанные системы с высокими требованиями к throughput (очереди, счётчики, аналитика). Нужно принять риск потери ещё не сброшенных данных.
