-- ============================================================
-- Таблицы и тестовые данные для демонстрации аномалий изоляции
-- ============================================================

-- Используется для: Dirty Read, Non-Repeatable Read, Lost Update
CREATE TABLE IF NOT EXISTS accounts (
    id      SERIAL PRIMARY KEY,
    owner   VARCHAR(50)    NOT NULL,
    balance NUMERIC(10, 2) NOT NULL
);

-- Используется для: Phantom Read
CREATE TABLE IF NOT EXISTS orders (
    id         SERIAL PRIMARY KEY,
    customer   VARCHAR(50)    NOT NULL,
    amount     NUMERIC(10, 2) NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

INSERT INTO accounts (owner, balance) VALUES
    ('Alice', 1000.00),
    ('Bob',    500.00);

INSERT INTO orders (customer, amount) VALUES
    ('Alice',   50.00),
    ('Bob',    150.00),
    ('Charlie', 200.00);
