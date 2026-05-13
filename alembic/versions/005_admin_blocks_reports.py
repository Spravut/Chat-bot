"""admin: ban flag, blocks, reports

Revision ID: 005
Revises: 004
Create Date: 2026-05-13

Adds:
  - users.is_banned — admin can ban abusive accounts; banned users are filtered
    out of the candidate feed and can't interact with the bot.
  - user_blocks — directed user-to-user blocks. Either side of the pair gets
    excluded from the other's feed (block is symmetric in effect).
  - reports — pending/reviewed abuse reports. Surfaced in the admin panel.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Ban flag on users
    op.add_column(
        "users",
        sa.Column(
            "is_banned",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_index(
        "idx_users_is_banned",
        "users",
        ["is_banned"],
        postgresql_where=sa.text("is_banned = true"),
    )

    # Blocks
    op.create_table(
        "user_blocks",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("blocker_id", sa.BigInteger(), nullable=False),
        sa.Column("blocked_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("blocker_id <> blocked_id", name="blocks_no_self_block"),
        sa.ForeignKeyConstraint(["blocker_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["blocked_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("blocker_id", "blocked_id"),
    )
    op.create_index("idx_user_blocks_blocker_id", "user_blocks", ["blocker_id"])
    op.create_index("idx_user_blocks_blocked_id", "user_blocks", ["blocked_id"])

    # Reports
    op.create_table(
        "reports",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("reporter_id", sa.BigInteger(), nullable=False),
        sa.Column("reported_id", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.String(32), nullable=False),  # spam/fake/inappropriate/other
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default="pending",
        ),  # pending / confirmed / dismissed
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("reviewed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint("reporter_id <> reported_id", name="reports_no_self_report"),
        sa.ForeignKeyConstraint(["reporter_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reported_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    # Hot path: admin panel lists pending reports.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_reports_pending "
        "ON reports (created_at DESC) WHERE status = 'pending'"
    )
    op.create_index("idx_reports_reported_id", "reports", ["reported_id"])


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_reports_pending")
    op.drop_index("idx_reports_reported_id", table_name="reports")
    op.drop_table("reports")
    op.drop_index("idx_user_blocks_blocked_id", table_name="user_blocks")
    op.drop_index("idx_user_blocks_blocker_id", table_name="user_blocks")
    op.drop_table("user_blocks")
    op.execute("DROP INDEX IF EXISTS idx_users_is_banned")
    op.drop_column("users", "is_banned")
