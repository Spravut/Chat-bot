"""
Демонстрация 4 аномалий изоляции SQL.

Каждая функция запускает две параллельные транзакции через threading.Event,
воспроизводя классическую временну́ю последовательность (interleaving).

Запуск:
    pip install psycopg2-binary
    python demo.py
"""

import os
import threading
import time

import psycopg2
import psycopg2.extensions

DB_DSN = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5433/isolation_demo",
)

ISOLATION = {
    "READ_UNCOMMITTED": psycopg2.extensions.ISOLATION_LEVEL_READ_UNCOMMITTED,
    "READ_COMMITTED":   psycopg2.extensions.ISOLATION_LEVEL_READ_COMMITTED,
    "REPEATABLE_READ":  psycopg2.extensions.ISOLATION_LEVEL_REPEATABLE_READ,
    "SERIALIZABLE":     psycopg2.extensions.ISOLATION_LEVEL_SERIALIZABLE,
}


def connect(level: str = "READ_COMMITTED"):
    conn = psycopg2.connect(DB_DSN)
    conn.set_isolation_level(ISOLATION[level])
    conn.autocommit = False
    return conn


def reset():
    conn = psycopg2.connect(DB_DSN)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("DELETE FROM accounts")
        cur.execute("DELETE FROM orders")
        cur.execute(
            "INSERT INTO accounts (owner, balance) VALUES ('Alice', 1000.00), ('Bob', 500.00)"
        )
        cur.execute(
            "INSERT INTO orders (customer, amount) VALUES "
            "('Alice', 50.00), ('Bob', 150.00), ('Charlie', 200.00)"
        )
    conn.close()


def sep(title: str):
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)


# ──────────────────────────────────────────────────────────────
# 1. DIRTY READ
# ──────────────────────────────────────────────────────────────
def demo_dirty_read():
    sep("АНОМАЛИЯ 1: DIRTY READ")
    print(
        "Суть: T1 читает незакоммиченные изменения T2.\n"
        "В PostgreSQL READ UNCOMMITTED ведёт себя как READ COMMITTED,\n"
        "поэтому PG ЗАЩИЩАЕТ от dirty read.\n"
        "Ниже показано, что T1 НЕ видит «грязные» данные T2.\n"
    )
    reset()

    t2_updated = threading.Event()
    t1_read    = threading.Event()

    def transaction_1():
        conn = connect("READ_UNCOMMITTED")
        cur = conn.cursor()

        t2_updated.wait()  # дождаться, пока T2 обновит, но не закоммитит

        cur.execute("SELECT balance FROM accounts WHERE owner = 'Alice'")
        val = cur.fetchone()[0]
        print(f"[T1] READ balance Alice = {val}  (ожидалось 1000, 'грязное' значение = 9999)")
        if val == 1000:
            print("[T1] ✓ PostgreSQL НЕ показывает незакоммиченные данные — dirty read предотвращён")
        else:
            print("[T1] ✗ Dirty read произошёл!")

        t1_read.set()
        conn.commit()
        cur.close()
        conn.close()

    def transaction_2():
        conn = connect("READ_UNCOMMITTED")
        cur = conn.cursor()

        cur.execute("UPDATE accounts SET balance = 9999 WHERE owner = 'Alice'")
        print("[T2] UPDATE balance → 9999 (не коммитим)")
        t2_updated.set()

        t1_read.wait()  # дождаться, пока T1 прочитает

        conn.rollback()
        print("[T2] ROLLBACK — изменения отменены")
        cur.close()
        conn.close()

    t1 = threading.Thread(target=transaction_1)
    t2 = threading.Thread(target=transaction_2)
    t2.start(); t1.start()
    t1.join(); t2.join()

    print("\nЧтобы воспроизвести dirty read — нужна СУБД с настоящим READ UNCOMMITTED (MySQL/MSSQL).")


# ──────────────────────────────────────────────────────────────
# 2. NON-REPEATABLE READ
# ──────────────────────────────────────────────────────────────
def demo_non_repeatable_read():
    sep("АНОМАЛИЯ 2: NON-REPEATABLE READ")
    print(
        "Суть: T1 дважды читает одну строку и получает разные значения,\n"
        "потому что T2 успел её изменить и закоммитить между двумя чтениями T1.\n"
    )
    reset()

    first_read_done = threading.Event()
    t2_committed    = threading.Event()

    def transaction_1():
        conn = connect("READ_COMMITTED")
        cur = conn.cursor()

        cur.execute("SELECT balance FROM accounts WHERE owner = 'Alice'")
        v1 = cur.fetchone()[0]
        print(f"[T1] Первое чтение  balance Alice = {v1}")
        first_read_done.set()

        t2_committed.wait()

        cur.execute("SELECT balance FROM accounts WHERE owner = 'Alice'")
        v2 = cur.fetchone()[0]
        print(f"[T1] Второе чтение balance Alice = {v2}")

        if v1 != v2:
            print(f"[T1] ✗ NON-REPEATABLE READ: {v1} → {v2} в одной транзакции!")
        else:
            print("[T1] Значения совпали (аномалии нет).")

        conn.commit()
        cur.close()
        conn.close()

    def transaction_2():
        first_read_done.wait()
        conn = connect("READ_COMMITTED")
        cur = conn.cursor()

        cur.execute("UPDATE accounts SET balance = 1500 WHERE owner = 'Alice'")
        conn.commit()
        print("[T2] UPDATE balance Alice → 1500, COMMIT")
        t2_committed.set()
        cur.close()
        conn.close()

    t1 = threading.Thread(target=transaction_1)
    t2 = threading.Thread(target=transaction_2)
    t1.start(); t2.start()
    t1.join(); t2.join()

    print("\nИзбежать: повысить уровень изоляции до REPEATABLE READ.")


# ──────────────────────────────────────────────────────────────
# 3. PHANTOM READ
# ──────────────────────────────────────────────────────────────
def demo_phantom_read():
    sep("АНОМАЛИЯ 3: PHANTOM READ")
    print(
        "Суть: T1 дважды выполняет один SELECT с фильтром и получает разное\n"
        "количество строк, потому что T2 вставил новую строку между запросами.\n"
    )
    reset()

    first_count_done = threading.Event()
    t2_committed     = threading.Event()

    def transaction_1():
        conn = connect("READ_COMMITTED")
        cur = conn.cursor()

        cur.execute("SELECT COUNT(*) FROM orders WHERE amount > 100")
        c1 = cur.fetchone()[0]
        print(f"[T1] Первый COUNT(*) WHERE amount > 100 = {c1}")
        first_count_done.set()

        t2_committed.wait()

        cur.execute("SELECT COUNT(*) FROM orders WHERE amount > 100")
        c2 = cur.fetchone()[0]
        print(f"[T1] Второй COUNT(*) WHERE amount > 100 = {c2}")

        if c1 != c2:
            print(f"[T1] ✗ PHANTOM READ: {c1} → {c2} строк в одной транзакции!")
        else:
            print("[T1] Количество строк не изменилось (аномалии нет).")

        conn.commit()
        cur.close()
        conn.close()

    def transaction_2():
        first_count_done.wait()
        conn = connect("READ_COMMITTED")
        cur = conn.cursor()

        cur.execute("INSERT INTO orders (customer, amount) VALUES ('Dave', 350.00)")
        conn.commit()
        print("[T2] INSERT orders Dave 350.00, COMMIT")
        t2_committed.set()
        cur.close()
        conn.close()

    t1 = threading.Thread(target=transaction_1)
    t2 = threading.Thread(target=transaction_2)
    t1.start(); t2.start()
    t1.join(); t2.join()

    print("\nИзбежать: повысить уровень изоляции до REPEATABLE READ или SERIALIZABLE.")


# ──────────────────────────────────────────────────────────────
# 4. LOST UPDATE
# ──────────────────────────────────────────────────────────────
def demo_lost_update():
    sep("АНОМАЛИЯ 4: LOST UPDATE")
    print(
        "Суть: T1 и T2 оба читают одно значение, вычисляют новое и пишут обратно.\n"
        "Последний UPDATE перезаписывает результат первого — одно изменение теряется.\n"
    )
    reset()

    both_read = threading.Barrier(2)

    def transaction_1():
        conn = connect("READ_COMMITTED")
        cur = conn.cursor()

        cur.execute("SELECT balance FROM accounts WHERE owner = 'Alice'")
        balance = cur.fetchone()[0]
        print(f"[T1] Прочитал balance = {balance}")
        both_read.wait()

        new_balance = balance + 500
        cur.execute("UPDATE accounts SET balance = %s WHERE owner = 'Alice'", (new_balance,))
        conn.commit()
        print(f"[T1] UPDATE balance → {new_balance}, COMMIT  (+500)")
        cur.close()
        conn.close()

    def transaction_2():
        conn = connect("READ_COMMITTED")
        cur = conn.cursor()

        cur.execute("SELECT balance FROM accounts WHERE owner = 'Alice'")
        balance = cur.fetchone()[0]
        print(f"[T2] Прочитал balance = {balance}")
        both_read.wait()

        time.sleep(0.05)  # T2 чуть позже, чтобы перезаписать T1
        new_balance = balance + 300
        cur.execute("UPDATE accounts SET balance = %s WHERE owner = 'Alice'", (new_balance,))
        conn.commit()
        print(f"[T2] UPDATE balance → {new_balance}, COMMIT  (+300)")
        cur.close()
        conn.close()

    t1 = threading.Thread(target=transaction_1)
    t2 = threading.Thread(target=transaction_2)
    t1.start(); t2.start()
    t1.join(); t2.join()

    conn = psycopg2.connect(DB_DSN)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SELECT balance FROM accounts WHERE owner = 'Alice'")
        final = cur.fetchone()[0]
    conn.close()

    print(f"\nИтоговый баланс Alice = {final}")
    print(f"Ожидалось: 1000 + 500 + 300 = 1800")
    if final != 1800:
        print(f"✗ LOST UPDATE: одно из обновлений потеряно! Потеря = {1800 - final:.2f}")
    else:
        print("✓ Потерь нет (аномалия не проявилась в этом запуске).")

    print("\nИзбежать: SELECT ... FOR UPDATE, оптимистичная блокировка, или SERIALIZABLE.")


# ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    demo_dirty_read()
    demo_non_repeatable_read()
    demo_phantom_read()
    demo_lost_update()
    print("\n" + "=" * 60)
    print("  Все демонстрации завершены.")
    print("=" * 60)
