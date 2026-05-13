from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger, Boolean, Integer, String, Text, Numeric, TIMESTAMP,
    ForeignKey, CheckConstraint, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_banned: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )

    profile: Mapped[UserProfile | None] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    photos: Mapped[list[Photo]] = relationship(
        back_populates="user", cascade="all, delete-orphan",
        order_by="Photo.sort_order",
    )
    rating: Mapped[Rating | None] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )


class UserProfile(Base):
    __tablename__ = "user_profiles"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )

    name: Mapped[str | None] = mapped_column(String(128))
    bio: Mapped[str | None] = mapped_column(Text)

    age: Mapped[int | None] = mapped_column(Integer)
    gender: Mapped[str | None] = mapped_column(String(16))
    seeking_gender: Mapped[str | None] = mapped_column(String(16))

    city: Mapped[str | None] = mapped_column(String(128))
    country: Mapped[str | None] = mapped_column(String(128))

    age_min: Mapped[int | None] = mapped_column(Integer)
    age_max: Mapped[int | None] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped[User] = relationship(back_populates="profile")


class Photo(Base):
    __tablename__ = "user_photos"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # MinIO object key for the persistent copy (source of truth).
    # Legacy rows may still hold a raw Telegram file_id here.
    photo_url: Mapped[str] = mapped_column(Text, nullable=False)
    # Telegram file_id — kept alongside so re-sends stay instant.
    telegram_file_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped[User] = relationship(back_populates="photos")


class Like(Base):
    __tablename__ = "likes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    from_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    to_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("from_user_id <> to_user_id", name="likes_no_self_like"),
        UniqueConstraint("from_user_id", "to_user_id"),
    )


class Match(Base):
    __tablename__ = "matches"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_a_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    user_b_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("user_a_id < user_b_id", name="matches_canonical_order"),
        UniqueConstraint("user_a_id", "user_b_id"),
    )


class Rating(Base):
    __tablename__ = "ratings"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    level1_score: Mapped[Decimal] = mapped_column(Numeric, nullable=False, server_default="0")
    level2_score: Mapped[Decimal] = mapped_column(Numeric, nullable=False, server_default="0")
    level3_score: Mapped[Decimal] = mapped_column(Numeric, nullable=False, server_default="0")
    computed_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped[User] = relationship(back_populates="rating")


class RatingEvent(Base):
    __tablename__ = "rating_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")


class Referral(Base):
    __tablename__ = "referrals"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    inviter_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    referred_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (UniqueConstraint("referred_user_id"),)


class Block(Base):
    """Directed user-to-user block. Either side's feed excludes the other."""
    __tablename__ = "user_blocks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    blocker_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    blocked_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("blocker_id <> blocked_id", name="blocks_no_self_block"),
        UniqueConstraint("blocker_id", "blocked_id"),
    )


class Report(Base):
    """Abuse report — surfaces in the admin panel for review."""
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    reporter_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    reported_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    # pending / confirmed / dismissed
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="pending")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint("reporter_id <> reported_id", name="reports_no_self_report"),
    )


class Interest(Base):
    __tablename__ = "interests"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)


class UserInterest(Base):
    __tablename__ = "user_interests"

    user_profile_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user_profiles.id", ondelete="CASCADE"), primary_key=True
    )
    interest_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("interests.id", ondelete="CASCADE"), primary_key=True
    )
