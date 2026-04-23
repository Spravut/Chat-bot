"""Add username column to users table

Revision ID: 002
Revises: 001
Create Date: 2026-04-23
"""
from alembic import op
import sqlalchemy as sa

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("username", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "username")
