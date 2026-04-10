"""initial schema

Revision ID: 001
Revises:
Create Date: 2026-04-10
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # users
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("telegram_id"),
    )

    # user_profiles
    op.create_table(
        "user_profiles",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(128), nullable=True),
        sa.Column("bio", sa.Text(), nullable=True),
        sa.Column("age", sa.Integer(), nullable=True),
        sa.Column("gender", sa.String(16), nullable=True),
        sa.Column("seeking_gender", sa.String(16), nullable=True),
        sa.Column("city", sa.String(128), nullable=True),
        sa.Column("country", sa.String(128), nullable=True),
        sa.Column("age_min", sa.Integer(), nullable=True),
        sa.Column("age_max", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index("idx_user_profiles_city", "user_profiles", ["city"])
    op.create_index("idx_user_profiles_user_id", "user_profiles", ["user_id"])

    # interests
    op.create_table(
        "interests",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    # user_interests
    op.create_table(
        "user_interests",
        sa.Column("user_profile_id", sa.BigInteger(), nullable=False),
        sa.Column("interest_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["user_profile_id"], ["user_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["interest_id"], ["interests.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_profile_id", "interest_id"),
    )

    # user_photos
    op.create_table(
        "user_photos",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("photo_url", sa.Text(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "sort_order"),
    )
    op.create_index("idx_user_photos_user_id", "user_photos", ["user_id"])

    # likes
    op.create_table(
        "likes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("from_user_id", sa.BigInteger(), nullable=False),
        sa.Column("to_user_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("from_user_id <> to_user_id", name="likes_no_self_like"),
        sa.ForeignKeyConstraint(["from_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["to_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("from_user_id", "to_user_id"),
    )
    op.create_index("idx_likes_from_user_id", "likes", ["from_user_id"])
    op.create_index("idx_likes_to_user_id", "likes", ["to_user_id"])

    # matches
    op.create_table(
        "matches",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_a_id", sa.BigInteger(), nullable=False),
        sa.Column("user_b_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("user_a_id < user_b_id", name="matches_canonical_order"),
        sa.ForeignKeyConstraint(["user_a_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_b_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_a_id", "user_b_id"),
    )
    op.create_index("idx_matches_user_a_id", "matches", ["user_a_id"])
    op.create_index("idx_matches_user_b_id", "matches", ["user_b_id"])

    # dialogs
    op.create_table(
        "dialogs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_a_id", sa.BigInteger(), nullable=False),
        sa.Column("user_b_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("user_a_id < user_b_id", name="dialogs_canonical_order"),
        sa.ForeignKeyConstraint(["user_a_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_b_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_a_id", "user_b_id"),
    )
    op.create_index("idx_dialogs_user_a_id", "dialogs", ["user_a_id"])
    op.create_index("idx_dialogs_user_b_id", "dialogs", ["user_b_id"])

    # messages
    op.create_table(
        "messages",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("dialog_id", sa.BigInteger(), nullable=False),
        sa.Column("sender_id", sa.BigInteger(), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["dialog_id"], ["dialogs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sender_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_messages_dialog_id_created_at", "messages", ["dialog_id", "created_at"])

    # ratings
    op.create_table(
        "ratings",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("level1_score", sa.Numeric(), nullable=False, server_default="0"),
        sa.Column("level2_score", sa.Numeric(), nullable=False, server_default="0"),
        sa.Column("level3_score", sa.Numeric(), nullable=False, server_default="0"),
        sa.Column("computed_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )

    # rating_events
    op.create_table(
        "rating_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("target_user_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("payload", JSONB(), nullable=False, server_default="{}"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_rating_events_user_id_created_at", "rating_events", ["user_id", "created_at"])
    op.create_index("idx_rating_events_target_user_id", "rating_events", ["target_user_id"])

    # referrals
    op.create_table(
        "referrals",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("inviter_user_id", sa.BigInteger(), nullable=False),
        sa.Column("referred_user_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["inviter_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["referred_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("referred_user_id"),
    )
    op.create_index("idx_referrals_inviter_user_id", "referrals", ["inviter_user_id"])


def downgrade() -> None:
    op.drop_table("referrals")
    op.drop_table("rating_events")
    op.drop_table("ratings")
    op.drop_table("messages")
    op.drop_table("dialogs")
    op.drop_table("matches")
    op.drop_table("likes")
    op.drop_table("user_photos")
    op.drop_table("user_interests")
    op.drop_table("interests")
    op.drop_table("user_profiles")
    op.drop_table("users")
