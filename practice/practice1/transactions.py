"""
Three transaction scenarios for the Online Store.

Scenario 1 — place_order
    Creates a new Order, adds OrderItems, then recalculates and updates
    Orders.TotalAmount from the sum of OrderItems.Subtotal.
    The whole operation is wrapped in a single transaction so a failure
    at any step leaves no partial data in the database.

Scenario 2 — update_customer_email
    Updates a customer's Email atomically.  If the new address already
    belongs to another customer an IntegrityError is raised and the
    transaction is rolled back automatically.

Scenario 3 — add_product
    Inserts a new product into Products atomically.  Negative prices are
    rejected before the INSERT so the table never ends up in an
    inconsistent state.
"""

from decimal import Decimal
from typing import List, Tuple

from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models import Customer, Order, OrderItem, Product


# ---------------------------------------------------------------------------
# Scenario 1 — place an order
# ---------------------------------------------------------------------------

def place_order(
    session: Session,
    customer_id: int,
    items: List[Tuple[int, int]],   # [(product_id, quantity), ...]
) -> Order:
    """
    Place an order for the given customer.

    Steps (all inside one transaction):
      1. Verify the customer exists.
      2. Create an Order row with TotalAmount = 0.
      3. For every (product_id, quantity) pair:
           - load the product price
           - compute subtotal = price * quantity
           - insert an OrderItem row
      4. Recalculate Orders.TotalAmount = SUM(OrderItems.Subtotal)
         where OrderItems.OrderID = new order id.
      5. COMMIT — or ROLLBACK if anything fails.
    """
    # -- Step 1: verify customer
    customer = session.get(Customer, customer_id)
    if customer is None:
        raise ValueError(f"Customer with id={customer_id} not found.")

    if not items:
        raise ValueError("Order must contain at least one item.")

    # -- Step 2: create the order header (TotalAmount will be set in step 4)
    new_order = Order(CustomerID=customer_id, TotalAmount=Decimal("0.00"))
    session.add(new_order)
    session.flush()  # populate new_order.OrderID without committing

    # -- Step 3: add order items
    for product_id, quantity in items:
        if quantity <= 0:
            raise ValueError(
                f"Quantity must be positive (got {quantity} for product {product_id})."
            )

        product = session.get(Product, product_id)
        if product is None:
            raise ValueError(f"Product with id={product_id} not found.")

        subtotal = Decimal(str(product.Price)) * quantity
        order_item = OrderItem(
            OrderID=new_order.OrderID,
            ProductID=product_id,
            Quantity=quantity,
            Subtotal=subtotal,
        )
        session.add(order_item)

    session.flush()  # write items so the aggregate query sees them

    # -- Step 4: recalculate TotalAmount from the actual inserted subtotals
    total = session.execute(
        select(func.sum(OrderItem.Subtotal)).where(
            OrderItem.OrderID == new_order.OrderID
        )
    ).scalar()

    new_order.TotalAmount = total or Decimal("0.00")

    # -- Step 5: commit
    session.commit()
    session.refresh(new_order)

    print(f"[Scenario 1] Order placed successfully: {new_order}")
    for item in new_order.items:
        print(f"             {item}")

    return new_order


# ---------------------------------------------------------------------------
# Scenario 2 — update customer email
# ---------------------------------------------------------------------------

def update_customer_email(
    session: Session,
    customer_id: int,
    new_email: str,
) -> Customer:
    """
    Atomically update the email address of a customer.

    - Raises ValueError  if the customer does not exist.
    - Raises IntegrityError (re-raised as ValueError) if the email is
      already used by another customer (UNIQUE constraint).
    """
    customer = session.get(Customer, customer_id)
    if customer is None:
        raise ValueError(f"Customer with id={customer_id} not found.")

    old_email = customer.Email
    customer.Email = new_email

    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise ValueError(
            f"Email '{new_email}' is already registered to another customer."
        )

    session.refresh(customer)
    print(
        f"[Scenario 2] Email updated: '{old_email}' -> '{new_email}' "
        f"for {customer}"
    )
    return customer


# ---------------------------------------------------------------------------
# Scenario 3 — add a new product
# ---------------------------------------------------------------------------

def add_product(
    session: Session,
    product_name: str,
    price: Decimal,
) -> Product:
    """
    Atomically insert a new product into the Products table.

    - Raises ValueError if the product name is empty or the price is negative,
      so the database is never left with invalid data.
    """
    if not product_name or not product_name.strip():
        raise ValueError("Product name must not be empty.")

    if price < Decimal("0.00"):
        raise ValueError(f"Product price must be non-negative (got {price}).")

    new_product = Product(ProductName=product_name.strip(), Price=price)
    session.add(new_product)

    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise

    session.refresh(new_product)
    print(f"[Scenario 3] Product added successfully: {new_product}")
    return new_product
