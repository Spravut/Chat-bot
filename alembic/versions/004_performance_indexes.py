"""performance indexes for hot read paths

Revision ID: 004
Revises: 003
Create Date: 2026-05-13

Adds indexes targeted at the queries that run on every browse / like / skip:

  1. `idx_ratings_level3_desc` — ORDER BY in `get_ranked_candidates`.
  2. `idx_user_profiles_gender_age` — candidate filter (gender + age range).
  3. `idx_user_profiles_seeking_gender` — reciprocal gender filter.
  4. `idx_rating_events_user_event_created` — Level 2 aggregation AND the
      24h skip cooldown share this exact lookup pattern.
  5. `idx_rating_events_skip_recent` — partial index that targets ONLY the
      hot path used in the feed (`skipped` events in the last 24h). Massively
      smaller than the full index above and lets PG skip the seq scan when
      `rating_events` grows large.
  6. `idx_user_photos_user_sort` — covers the photo display query.

`down_revision` is 003 to chain after the MinIO column addition.
"""
from __future__ import annotations

from alembic import op

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Ranking ORDER BY — descending matches the query plan.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_ratings_level3_desc "
        "ON ratings (level3_score DESC NULLS LAST)"
    )

    # Candidate filter: gender + age.
    op.create_index(
        "idx_user_profiles_gender_age",
        "user_profiles",
        ["gender", "age"],
    )

    # Reciprocal seeking_gender filter.
    op.create_index(
        "idx_user_profiles_seeking_gender",
        "user_profiles",
        ["seeking_gender"],
    )

    # Level 2 aggregation + 24h skip cooldown both filter by
    # (user_id, event_type) and (in cooldown's case) created_at.
    op.create_index(
        "idx_rating_events_user_event_created",
        "rating_events",
        ["user_id", "event_type", "created_at"],
    )

    # Partial index — hot path: viewer's recent skips, used by the candidate
    # exclusion subquery on every refill. Tiny compared to the full table.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_rating_events_skip_recent "
        "ON rating_events (user_id, target_user_id, created_at) "
        "WHERE event_type = 'skipped'"
    )

    # Photo display ordering (already unique, but explicit index helps planner).
    op.create_index(
        "idx_user_photos_user_sort",
        "user_photos",
        ["user_id", "sort_order"],
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_user_photos_user_sort")
    op.execute("DROP INDEX IF EXISTS idx_rating_events_skip_recent")
    op.execute("DROP INDEX IF EXISTS idx_rating_events_user_event_created")
    op.execute("DROP INDEX IF EXISTS idx_user_profiles_seeking_gender")
    op.execute("DROP INDEX IF EXISTS idx_user_profiles_gender_age")
    op.execute("DROP INDEX IF EXISTS idx_ratings_level3_desc")
