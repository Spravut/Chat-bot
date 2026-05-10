# Online Store Transactions — Practice 1

Учебный проект: демонстрация транзакций SQL через Python-сервис с использованием ORM (SQLAlchemy) и PostgreSQL в Docker.

---

## Структура проекта

```
practice1/
├── models.py          # ORM-модели (структура таблиц)
├── transactions.py    # Три транзакционных сценария
├── main.py            # Точка входа: seed + запуск сценариев
├── requirements.txt   # Зависимости Python
├── Dockerfile         # Инструкция сборки образа приложения
└── docker-compose.yml # Оркестрация: БД + приложение
```

---

## Схема базы данных

```
Customers
─────────────────────────────
CustomerID  INT  PK  AUTO
FirstName   VARCHAR(100)
LastName    VARCHAR(100)
Email       VARCHAR(255)  UNIQUE

Products
─────────────────────────────
ProductID   INT  PK  AUTO
ProductName VARCHAR(255)
Price       NUMERIC(10,2)

Orders
─────────────────────────────
OrderID      INT  PK  AUTO
CustomerID   INT  FK → Customers
OrderDate    DATETIME
TotalAmount  NUMERIC(10,2)

OrderItems
─────────────────────────────
OrderItemID  INT  PK  AUTO
OrderID      INT  FK → Orders
ProductID    INT  FK → Products
Quantity     INT
Subtotal     NUMERIC(10,2)
```

Связи:
- Один `Customer` → много `Orders`
- Один `Order` → много `OrderItems`
- Один `Product` → много `OrderItems`

---

## Описание файлов

### `models.py` — ORM-модели

**Зачем:** Описывает структуру базы данных на Python-классах вместо сырого SQL. SQLAlchemy сам создаст таблицы (`CREATE TABLE`) и будет транслировать Python-операции в SQL-запросы.

**Что внутри:**

```python
class Customer(Base):
    __tablename__ = "Customers"
    CustomerID = Column(Integer, primary_key=True, autoincrement=True)
    Email      = Column(String(255), nullable=False, unique=True)
    ...
```

- `DeclarativeBase` — базовый класс, от которого наследуются все модели. SQLAlchemy через него знает, что класс = таблица.
- `Column(...)` — описывает колонку: тип, ограничения (`nullable`, `unique`, `primary_key`).
- `ForeignKey(...)` — внешний ключ, связывает таблицы на уровне БД.
- `relationship(...)` — связь на уровне Python-объектов. Позволяет писать `order.items` вместо отдельного SQL-запроса.
- `cascade="all, delete-orphan"` у `Order.items` — при удалении заказа все его позиции удалятся автоматически.
- `Numeric(10, 2)` для цен и сумм — хранит деньги без ошибок округления (в отличие от `Float`).

---

### `transactions.py` — три транзакционных сценария

**Зачем:** Содержит бизнес-логику. Каждая функция — отдельный сценарий, обёрнутый в транзакцию.

**Что такое транзакция:** Набор SQL-операций, которые выполняются как единое целое. Либо все операции применяются (`COMMIT`), либо ни одна (`ROLLBACK`). Это гарантирует целостность данных при ошибках.

---

#### Сценарий 1 — `place_order(session, customer_id, items)`

Имитирует оформление заказа. Шаги внутри одной транзакции:

1. Проверяет, что покупатель существует.
2. Создаёт строку в `Orders` с `TotalAmount = 0`.
3. `session.flush()` — отправляет INSERT в БД **без** коммита, чтобы получить `OrderID`.
4. Для каждого товара: загружает цену, считает `subtotal = price × quantity`, добавляет `OrderItem`.
5. Ещё один `flush()` — все позиции записаны.
6. Пересчитывает `TotalAmount` через `SUM(OrderItems.Subtotal)` — данные берутся из самой БД, а не из Python-переменных (защита от ошибок округления).
7. `session.commit()` — фиксирует всё разом.

Если на любом шаге произойдёт ошибка — SQLAlchemy откатит транзакцию и в БД не останется ни пустого заказа, ни лишних позиций.

```python
# Пересчёт суммы через SQL — надёжнее, чем складывать в Python
total = session.execute(
    select(func.sum(OrderItem.Subtotal)).where(
        OrderItem.OrderID == new_order.OrderID
    )
).scalar()
new_order.TotalAmount = total
```

---

#### Сценарий 2 — `update_customer_email(session, customer_id, new_email)`

Атомарно обновляет email клиента.

- Если клиент не найден → `ValueError` до любых изменений в БД.
- Меняет `customer.Email` и вызывает `session.commit()`.
- Если новый email уже занят (нарушение `UNIQUE`) → PostgreSQL выбрасывает `IntegrityError` → код перехватывает его, делает `session.rollback()` и бросает понятную ошибку.
- **Атомарность:** между чтением старого email и записью нового нет никакого промежуточного состояния в БД — операция либо полностью прошла, либо полностью откатилась.

```python
try:
    session.commit()
except IntegrityError:
    session.rollback()   # откат — БД вернётся к прежнему состоянию
    raise ValueError("Email уже занят другим клиентом.")
```

---

#### Сценарий 3 — `add_product(session, product_name, price)`

Атомарно добавляет новый товар.

- Валидация **до** INSERT: пустое имя и отрицательная цена отклоняются ещё в Python, не доходя до БД.
- `session.add(new_product)` → `session.commit()`.
- Если по какой-то причине INSERT упадёт (`IntegrityError`) — `session.rollback()` возвращает БД в чистое состояние.

Принцип: **база данных никогда не должна получить невалидные данные**. Проверки на уровне приложения — первый рубеж, ограничения БД — второй.

---

### `main.py` — точка входа

**Зачем:** Собирает всё вместе: подключается к БД, создаёт таблицы, наполняет тестовыми данными и запускает все три сценария.

**Что внутри:**

```python
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://...")
```
URL подключения читается из переменной окружения — это стандартная практика. Менять URL можно через `docker-compose.yml` без правки кода.

```python
def wait_for_db(engine, retries=10, delay=3):
    ...
```
PostgreSQL стартует не мгновенно. Эта функция пытается подключиться до 10 раз с паузой в 3 секунды. Без неё приложение упало бы с ошибкой, если запустилось раньше БД.

```python
Base.metadata.create_all(engine)
```
Создаёт все таблицы по описанным моделям (`CREATE TABLE IF NOT EXISTS`). Идемпотентно — повторный запуск ничего не сломает.

```python
def seed_data(session):
    ...
```
Добавляет тестовых покупателей и товары, чтобы было на чём демонстрировать транзакции.

Для каждого сценария также запускается **негативный тест** — попытка выполнить недопустимую операцию, чтобы наглядно показать, что система корректно отклоняет её и не портит данные.

---

### `requirements.txt` — зависимости

```
sqlalchemy==2.0.30
psycopg2-binary==2.9.9
```

- **SQLAlchemy** — ORM-библиотека. Версия зафиксирована (`==`), чтобы сборка была воспроизводима на любой машине.
- **psycopg2-binary** — драйвер, через который SQLAlchemy общается с PostgreSQL. Суффикс `-binary` означает, что внутри уже скомпилированный `.so`-файл — не нужны системные заголовки C для сборки.

---

### `Dockerfile` — сборка образа приложения

**Зачем:** Описывает, как упаковать приложение в Docker-образ — изолированную среду, которая работает одинаково на любой машине.

```dockerfile
FROM python:3.12-slim
```
Берём официальный минималистичный образ Python. `slim` — урезанная версия без лишних системных утилит, весит ~150 МБ против ~900 МБ у полного образа.

```dockerfile
COPY requirements.txt .
RUN pip install --no-cache-dir --timeout=120 -r requirements.txt
```
Сначала копируем только `requirements.txt` и устанавливаем зависимости. Это умный приём: Docker кэширует слои. Если изменить только код (`models.py`), этот слой не будет пересобираться и зависимости не переустановятся — экономия времени.

`--no-cache-dir` — не хранить кэш pip внутри образа (меньше размер).
`--timeout=120` — увеличенный таймаут на случай медленной сети.

```dockerfile
COPY models.py transactions.py main.py ./
```
Копируем код после зависимостей — именно поэтому изменение кода не инвалидирует кэш установленных пакетов.

```dockerfile
ENV DATABASE_URL=postgresql://store_user:store_pass@db:5432/online_store
CMD ["python", "main.py"]
```
`ENV` задаёт переменную окружения по умолчанию. `CMD` — команда при запуске контейнера.

---

### `docker-compose.yml` — оркестрация

**Зачем:** Запускает и связывает два контейнера (БД и приложение) одной командой.

```yaml
db:
  image: postgres:16-alpine
```
Официальный образ PostgreSQL 16. `alpine` — ещё более лёгкая база (~80 МБ).

```yaml
  environment:
    POSTGRES_DB: online_store
    POSTGRES_USER: store_user
    POSTGRES_PASSWORD: store_pass
```
PostgreSQL при первом запуске автоматически создаёт БД и пользователя с этими параметрами.

```yaml
  healthcheck:
    test: ["CMD-SHELL", "pg_isready -U store_user -d online_store"]
    interval: 5s
    retries: 10
```
Docker проверяет каждые 5 секунд, готова ли БД принимать подключения. Контейнер получит статус `healthy` только после успешной проверки.

```yaml
app:
  depends_on:
    db:
      condition: service_healthy
```
Приложение стартует **только после** того, как БД получила статус `healthy`. Именно это вместе с `wait_for_db()` в коде гарантирует корректный порядок запуска.

```yaml
  restart: "no"
```
Приложение выполняет сценарии и завершается (`exit code 0`). `restart: "no"` говорит Docker не перезапускать его — это не веб-сервер, а одноразовый скрипт.

```yaml
volumes:
  pg_data:
```
Именованный том для данных PostgreSQL. Данные переживают перезапуск контейнера — при повторном `docker compose up` БД не будет пустой.

---

## Запуск

```bash
# Первый запуск — собрать образ и запустить
docker compose up --build

# Повторный запуск (образ уже собран)
docker compose up

# Остановить и удалить контейнеры
docker compose down

# Остановить и удалить контейнеры + тома (полный сброс БД)
docker compose down -v
```

---

## Ожидаемый вывод

```
Database is ready.
--- Seed data inserted ---

=== Scenario 1: Place an Order ===
[Scenario 1] Order placed successfully: <Order id=1 customer_id=1 total=1059.97>
  Order total: 1059.97

=== Scenario 2: Update Customer Email ===
[Scenario 2] Email updated: 'bob@example.com' -> 'bob.updated@example.com'
  [Test] Attempting to set Bob's email to Alice's address (should fail):
  Correctly rejected: Email 'alice@example.com' is already registered to another customer.

=== Scenario 3: Add a New Product ===
[Scenario 3] Product added successfully: <Product id=4 name='USB-C Hub' price=49.99>
  [Test] Attempting to add product with negative price (should fail):
  Correctly rejected: Product price must be non-negative (got -5.00).

All scenarios completed successfully.
```

---

## Стек технологий

| Технология | Версия | Роль |
|---|---|---|
| Python | 3.12 | Язык приложения |
| SQLAlchemy | 2.0.30 | ORM — работа с БД через Python-классы |
| psycopg2-binary | 2.9.9 | Драйвер подключения к PostgreSQL |
| PostgreSQL | 16 | Реляционная СУБД |
| Docker | — | Контейнеризация |
| Docker Compose | — | Оркестрация контейнеров |
