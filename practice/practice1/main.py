"""
Entry point — seeds the database and runs all three transaction scenarios.

Environment variable DATABASE_URL is read at startup so the same image
works with any SQLAlchemy-compatible database just by changing the compose
file (PostgreSQL is used in the provided docker-compose.yml).
"""

import os
import time
from decimal import Decimal

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from models import Base, Customer, Product
from transactions import add_product, place_order, update_customer_email


# ---------------------------------------------------------------------------
# Database connection
# ---------------------------------------------------------------------------

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://store_user:store_pass@db:5432/online_store",
)


def wait_for_db(engine, retries: int = 10, delay: int = 3) -> None:
    """Wait until the database is ready to accept connections."""
    for attempt in range(1, retries + 1):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            print("Database is ready.")
            return
        except Exception as exc:
            print(f"Waiting for database... attempt {attempt}/{retries} ({exc})")
            time.sleep(delay)
    raise RuntimeError("Could not connect to the database after several retries.")


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------

def get_or_create_customer(session, first_name, last_name, email) -> Customer:
    """Return existing customer by email or insert a new one."""
    customer = session.execute(
        select(Customer).where(Customer.Email == email)
    ).scalar_one_or_none()
    if customer is None:
        customer = Customer(FirstName=first_name, LastName=last_name, Email=email)
        session.add(customer)
        session.flush()
    return customer


def get_or_create_product(session, product_name, price) -> Product:
    """Return existing product by name or insert a new one."""
    product = session.execute(
        select(Product).where(Product.ProductName == product_name)
    ).scalar_one_or_none()
    if product is None:
        product = Product(ProductName=product_name, Price=price)
        session.add(product)
        session.flush()
    return product


def seed_data(session) -> tuple:
    """Insert sample customers and products if they don't exist yet, return their IDs."""
    alice    = get_or_create_customer(session, "Alice", "Smith", "alice@example.com")
    bob      = get_or_create_customer(session, "Bob",   "Jones", "bob@example.com")
    laptop   = get_or_create_product(session, "Laptop",               Decimal("999.99"))
    mouse    = get_or_create_product(session, "Wireless Mouse",        Decimal("29.99"))
    keyboard = get_or_create_product(session, "Mechanical Keyboard",   Decimal("79.99"))

    session.commit()
    print("\n--- Seed data ready ---")
    print(f"  {alice}")
    print(f"  {bob}")
    print(f"  {laptop}")
    print(f"  {mouse}")
    print(f"  {keyboard}")

    return alice.CustomerID, bob.CustomerID, laptop.ProductID, mouse.ProductID


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    engine = create_engine(DATABASE_URL, echo=False)
    wait_for_db(engine)

    # Create all tables (idempotent)
    Base.metadata.create_all(engine)

    Session = sessionmaker(bind=engine)

    # ---- Seed ----------------------------------------------------------------
    with Session() as session:
        alice_id, bob_id, laptop_id, mouse_id = seed_data(session)

    # ---- Scenario 1: place an order ------------------------------------------
    print("\n=== Scenario 1: Place an Order ===")
    with Session() as session:
        order = place_order(
            session,
            customer_id=alice_id,
            items=[
                (laptop_id, 1),   # 1 × Laptop  = 999.99
                (mouse_id, 2),    # 2 × Mouse   =  59.98
            ],
        )
        print(f"  Order total: {order.TotalAmount}")  # expected: 1059.97

    # ---- Scenario 2: update customer email -----------------------------------
    print("\n=== Scenario 2: Update Customer Email ===")
    with Session() as session:
        update_customer_email(
            session,
            customer_id=bob_id,
            new_email="bob.updated@example.com",
        )

    # Demonstrate atomicity: try to steal Alice's email (must fail)
    print("\n  [Test] Attempting to set Bob's email to Alice's address (should fail):")
    with Session() as session:
        try:
            update_customer_email(
                session,
                customer_id=bob_id,
                new_email="alice@example.com",
            )
        except ValueError as exc:
            print(f"  Correctly rejected: {exc}")

    # ---- Scenario 3: add a new product ---------------------------------------
    print("\n=== Scenario 3: Add a New Product ===")
    with Session() as session:
        add_product(session, product_name="USB-C Hub", price=Decimal("49.99"))

    # Demonstrate atomicity: try to add a product with a negative price (must fail)
    print("\n  [Test] Attempting to add product with negative price (should fail):")
    with Session() as session:
        try:
            add_product(session, product_name="Bad Product", price=Decimal("-5.00"))
        except ValueError as exc:
            print(f"  Correctly rejected: {exc}")

    print("\nAll scenarios completed successfully.")


if __name__ == "__main__":
    main()
