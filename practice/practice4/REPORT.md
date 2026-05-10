# Отчёт: Аномалии изоляции в SQL

## Выбранные аномалии

| № | Аномалия | Уровень изоляции, при котором проявляется |
|---|----------|-------------------------------------------|
| 1 | Dirty Read | READ UNCOMMITTED (PostgreSQL предотвращает) |
| 2 | Non-Repeatable Read | READ COMMITTED |
| 3 | Phantom Read | READ COMMITTED |
| 4 | Lost Update | READ COMMITTED |

**СУБД:** PostgreSQL 16 (Docker)  
**Таблицы:** `accounts` (баланс), `orders` (заказы)  
**Скрипт демонстрации:** `demo.py` (Python + psycopg2, потоки)

---

## Аномалия 1 — Dirty Read («грязное чтение»)

### Описание

Транзакция T1 читает строку, изменённую транзакцией T2, которая ещё **не была закоммичена**.  
Если T2 впоследствии делает `ROLLBACK`, T1 уже работала с «несуществующими» данными.

### Тестовые данные

```sql
INSERT INTO accounts (owner, balance) VALUES ('Alice', 1000.00);
```

### Шаги воспроизведения

| Момент | T1 (READ UNCOMMITTED) | T2 (READ UNCOMMITTED) |
|--------|----------------------|----------------------|
| t1 | BEGIN | — |
| t2 | — | BEGIN |
| t3 | — | UPDATE accounts SET balance = 9999 WHERE owner = 'Alice' |
| t4 | SELECT balance … → ? | — |
| t5 | — | ROLLBACK |
| t6 | COMMIT | — |

**MySQL / MSSQL** (настоящий READ UNCOMMITTED): T1 на шаге t4 видит `9999`.  
**PostgreSQL**: T1 видит `1000` — READ UNCOMMITTED здесь синоним READ COMMITTED.

### Результат

**[SCREENSHOT: pic/01_dirty_read.png]**  
*(Терминал с выводом demo.py, блок «АНОМАЛИЯ 1: DIRTY READ»)*

На скриншоте видно, что T1 прочитала `1000`, а не `9999` — PostgreSQL предотвращает dirty read.

### Как избежать

PostgreSQL защищает автоматически — минимальный уровень фактически READ COMMITTED.  
В MySQL использовать уровень изоляции **READ COMMITTED** или выше.

---

## Аномалия 2 — Non-Repeatable Read («неповторяемое чтение»)

### Описание

T1 дважды читает **одну и ту же строку** в рамках одной транзакции и получает разные значения, потому что T2 успела изменить строку и закоммитить изменения между двумя чтениями T1.

### Тестовые данные

```sql
INSERT INTO accounts (owner, balance) VALUES ('Alice', 1000.00);
```

### Шаги воспроизведения

| Момент | T1 (READ COMMITTED) | T2 (READ COMMITTED) |
|--------|---------------------|---------------------|
| t1 | BEGIN | — |
| t2 | SELECT balance WHERE owner='Alice' → **1000** | — |
| t3 | — | BEGIN |
| t4 | — | UPDATE accounts SET balance = 1500 WHERE owner='Alice' |
| t5 | — | COMMIT |
| t6 | SELECT balance WHERE owner='Alice' → **1500** | — |
| t7 | COMMIT | — |

T1 в рамках одной транзакции видит две разные версии строки.

### Результат

**[SCREENSHOT: pic/02_non_repeatable_read.png]**  
*(Терминал с выводом demo.py, блок «АНОМАЛИЯ 2: NON-REPEATABLE READ»)*

На скриншоте видно `1000 → 1500` — non-repeatable read воспроизведён.

### Как избежать

Повысить уровень изоляции до **REPEATABLE READ**:

```sql
SET TRANSACTION ISOLATION LEVEL REPEATABLE READ;
```

На этом уровне T1 всегда видит снимок данных на момент начала транзакции.

---

## Аномалия 3 — Phantom Read («фантомное чтение»)

### Описание

T1 дважды выполняет один и тот же `SELECT` с условием `WHERE` и получает разное **количество строк**, потому что T2 успела вставить новую строку, удовлетворяющую условию.

### Тестовые данные

```sql
INSERT INTO orders (customer, amount) VALUES
    ('Alice',   50.00),
    ('Bob',    150.00),
    ('Charlie', 200.00);
```

### Шаги воспроизведения

| Момент | T1 (READ COMMITTED) | T2 (READ COMMITTED) |
|--------|---------------------|---------------------|
| t1 | BEGIN | — |
| t2 | SELECT COUNT(*) FROM orders WHERE amount > 100 → **2** | — |
| t3 | — | BEGIN |
| t4 | — | INSERT INTO orders (customer, amount) VALUES ('Dave', 350.00) |
| t5 | — | COMMIT |
| t6 | SELECT COUNT(*) FROM orders WHERE amount > 100 → **3** | — |
| t7 | COMMIT | — |

T1 видит «фантомную» строку Dave, которой не было на момент начала транзакции.

### Результат

**[SCREENSHOT: pic/03_phantom_read.png]**  
*(Терминал с выводом demo.py, блок «АНОМАЛИЯ 3: PHANTOM READ»)*

На скриншоте видно `2 → 3` — phantom read воспроизведён.

### Как избежать

Повысить уровень изоляции до **SERIALIZABLE**:

```sql
SET TRANSACTION ISOLATION LEVEL SERIALIZABLE;
```

Либо использовать **REPEATABLE READ** (в PostgreSQL снимок-данных включает диапазоны строк, поэтому REPEATABLE READ тоже предотвращает фантомы, хотя по стандарту SQL — нет).

---

## Аномалия 4 — Lost Update («потерянное обновление»)

### Описание

T1 и T2 оба читают значение, вычисляют новое на его основе и пишут обратно.  
Второй `UPDATE` перезаписывает результат первого — одно изменение **теряется**.

Типичная ситуация: два кассира одновременно пополняют счёт клиента.

### Тестовые данные

```sql
INSERT INTO accounts (owner, balance) VALUES ('Alice', 1000.00);
```

### Шаги воспроизведения

| Момент | T1 (+500) | T2 (+300) |
|--------|-----------|-----------|
| t1 | BEGIN; SELECT balance → **1000** | BEGIN; SELECT balance → **1000** |
| t2 | (вычисляет 1000+500 = 1500) | (вычисляет 1000+300 = 1300) |
| t3 | UPDATE accounts SET balance = **1500** | — |
| t4 | COMMIT | — |
| t5 | — | UPDATE accounts SET balance = **1300** |
| t6 | — | COMMIT |

Итого: `1300`. Должно быть: `1800`. Потеря: `500`.

### Результат

**[SCREENSHOT: pic/04_lost_update.png]**  
*(Терминал с выводом demo.py, блок «АНОМАЛИЯ 4: LOST UPDATE»)*

На скриншоте итоговый баланс `1300` вместо `1800`.

### Как избежать

**Вариант 1 — Пессимистичная блокировка** (`SELECT ... FOR UPDATE`):

```sql
BEGIN;
SELECT balance FROM accounts WHERE owner = 'Alice' FOR UPDATE;
-- T2 заблокирована до COMMIT/ROLLBACK T1
UPDATE accounts SET balance = balance + 500 WHERE owner = 'Alice';
COMMIT;
```

**Вариант 2 — Атомарный UPDATE** (без чтения в приложении):

```sql
UPDATE accounts SET balance = balance + 500 WHERE owner = 'Alice';
```

**Вариант 3 — Оптимистичная блокировка** (поле `version`):

```sql
UPDATE accounts
SET balance = 1500, version = version + 1
WHERE owner = 'Alice' AND version = 3;
-- Если обновлено 0 строк — повторить транзакцию
```

**Вариант 4 — SERIALIZABLE** (PostgreSQL обнаружит конфликт и выбросит ошибку).

---

## Итоговая таблица уровней изоляции

| Уровень изоляции | Dirty Read | Non-Repeatable Read | Phantom Read | Lost Update |
|------------------|:----------:|:-------------------:|:------------:|:-----------:|
| READ UNCOMMITTED | возможен* | возможен | возможен | возможен |
| READ COMMITTED | защищён | **возможен** | **возможен** | **возможен** |
| REPEATABLE READ | защищён | защищён | возможен** | защищён |
| SERIALIZABLE | защищён | защищён | защищён | защищён |

\* PostgreSQL не реализует настоящий READ UNCOMMITTED — он синоним READ COMMITTED.  
\*\* PostgreSQL REPEATABLE READ использует snapshot isolation и тоже защищает от фантомов.

---

## Скриншоты

| Файл | Описание |
|------|----------|
| [pic/01_dirty_read.png](pic/01_dirty_read.png) | Вывод `demo.py` — блок Dirty Read |
| [pic/02_non_repeatable_read.png](pic/02_non_repeatable_read.png) | Вывод `demo.py` — блок Non-Repeatable Read |
| [pic/03_phantom_read.png](pic/03_phantom_read.png) | Вывод `demo.py` — блок Phantom Read |
| [pic/04_lost_update.png](pic/04_lost_update.png) | Вывод `demo.py` — блок Lost Update |
| [pic/05_db_state.png](pic/05_db_state.png) | Итоговое состояние таблиц в БД |
