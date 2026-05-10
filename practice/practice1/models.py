"""
SQLAlchemy ORM models for the Online Store database.

Tables:
    Customers  — store customer info
    Products   — product catalog
    Orders     — order headers
    OrderItems — individual line items per order
"""

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class Customer(Base):
    __tablename__ = "Customers"

    CustomerID = Column(Integer, primary_key=True, autoincrement=True)
    FirstName = Column(String(100), nullable=False)
    LastName = Column(String(100), nullable=False)
    Email = Column(String(255), nullable=False, unique=True)

    orders = relationship("Order", back_populates="customer")

    def __repr__(self) -> str:
        return (
            f"<Customer id={self.CustomerID} "
            f"name='{self.FirstName} {self.LastName}' "
            f"email='{self.Email}'>"
        )


class Product(Base):
    __tablename__ = "Products"

    ProductID = Column(Integer, primary_key=True, autoincrement=True)
    ProductName = Column(String(255), nullable=False)
    Price = Column(Numeric(10, 2), nullable=False)

    order_items = relationship("OrderItem", back_populates="product")

    def __repr__(self) -> str:
        return (
            f"<Product id={self.ProductID} "
            f"name='{self.ProductName}' "
            f"price={self.Price}>"
        )


class Order(Base):
    __tablename__ = "Orders"

    OrderID = Column(Integer, primary_key=True, autoincrement=True)
    CustomerID = Column(Integer, ForeignKey("Customers.CustomerID"), nullable=False)
    OrderDate = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    TotalAmount = Column(Numeric(10, 2), nullable=False, default=Decimal("0.00"))

    customer = relationship("Customer", back_populates="orders")
    items = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return (
            f"<Order id={self.OrderID} "
            f"customer_id={self.CustomerID} "
            f"total={self.TotalAmount}>"
        )


class OrderItem(Base):
    __tablename__ = "OrderItems"

    OrderItemID = Column(Integer, primary_key=True, autoincrement=True)
    OrderID = Column(Integer, ForeignKey("Orders.OrderID"), nullable=False)
    ProductID = Column(Integer, ForeignKey("Products.ProductID"), nullable=False)
    Quantity = Column(Integer, nullable=False)
    Subtotal = Column(Numeric(10, 2), nullable=False)

    order = relationship("Order", back_populates="items")
    product = relationship("Product", back_populates="order_items")

    def __repr__(self) -> str:
        return (
            f"<OrderItem id={self.OrderItemID} "
            f"order_id={self.OrderID} "
            f"product_id={self.ProductID} "
            f"qty={self.Quantity} "
            f"subtotal={self.Subtotal}>"
        )
