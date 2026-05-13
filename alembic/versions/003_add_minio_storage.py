"""add MinIO storage support to photos

Revision ID: 003
Revises: 002
Create Date: 2026-05-13

Adds `telegram_file_id` to the photos table. The existing `photo_url` column
now holds the MinIO object key (or, for legacy rows, the raw Telegram file_id);
`telegram_file_id` holds the file_id explicitly so Telegram re-sends remain
fast even when the source of truth lives in S3.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_photos",
        sa.Column("telegram_file_id", sa.Text(), nullable=True),
    )
    # Backfill: existing photo_url values ARE file_ids (legacy behaviour).
    op.execute("UPDATE user_photos SET telegram_file_id = photo_url WHERE telegram_file_id IS NULL")


def downgrade() -> None:
    op.drop_column("user_photos", "telegram_file_id")
