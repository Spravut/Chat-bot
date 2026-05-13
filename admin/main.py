"""
Admin panel — FastAPI web UI on port 8000.

Independent process (separate container) that reads/writes the same Postgres
database as the bot. Uses async SQLAlchemy for consistency with the bot code,
HTTP Basic auth for access control, and Jinja2 templates for the UI.

Auth credentials come from env vars:
  ADMIN_USER     (default: admin)
  ADMIN_PASSWORD (default: admin — CHANGE IN PRODUCTION)

The bot and the admin are intentionally decoupled: admin only touches the
DB (no Telegram API calls, no Celery). Bans/dismissals take effect on the
bot's next read because there's no cache in front of `users.is_banned`.
"""
from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bot.config import DATABASE_URL
from bot.db.models import (
    Block, Like, Match, Photo, Rating, Referral, Report, User, UserProfile,
)

ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin")

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app = FastAPI(title="Dating Bot Admin", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

engine = create_async_engine(DATABASE_URL, pool_pre_ping=True, future=True)
SessionFactory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

security = HTTPBasic()


def require_admin(creds: HTTPBasicCredentials = Depends(security)) -> str:
    """Constant-time comparison of admin credentials (defends against timing)."""
    u_ok = secrets.compare_digest(creds.username, ADMIN_USER)
    p_ok = secrets.compare_digest(creds.password, ADMIN_PASSWORD)
    if not (u_ok and p_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return creds.username


async def get_session() -> AsyncSession:
    async with SessionFactory() as session:
        yield session


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse(url="/admin/users", status_code=302)


@app.get("/admin", include_in_schema=False)
async def admin_root() -> RedirectResponse:
    return RedirectResponse(url="/admin/users", status_code=302)


@app.get("/admin/users", response_class=HTMLResponse)
async def users_list(
    request: Request,
    _: str = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    q: str = "",
    show_banned: int = 0,
) -> HTMLResponse:
    """List users with their profile + rating snapshot. Filter by name/city."""
    stmt = (
        select(
            User.id, User.telegram_id, User.username, User.is_banned, User.created_at,
            UserProfile.name, UserProfile.age, UserProfile.gender, UserProfile.city,
            Rating.level3_score,
        )
        .outerjoin(UserProfile, UserProfile.user_id == User.id)
        .outerjoin(Rating, Rating.user_id == User.id)
        .order_by(User.created_at.desc())
        .limit(200)
    )
    if q:
        like = f"%{q}%"
        stmt = stmt.where((UserProfile.name.ilike(like)) | (UserProfile.city.ilike(like)))
    if not show_banned:
        stmt = stmt.where(User.is_banned.is_(False))

    rows = (await session.execute(stmt)).all()
    return templates.TemplateResponse(
        request,
        "users.html",
        {"rows": rows, "q": q, "show_banned": show_banned},
    )


@app.post("/admin/users/{user_id}/ban", include_in_schema=False)
async def ban_user(
    user_id: int,
    _: str = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    await session.execute(
        update(User).where(User.id == user_id).values(is_banned=True)
    )
    await session.commit()
    return RedirectResponse(url="/admin/users", status_code=303)


@app.post("/admin/users/{user_id}/unban", include_in_schema=False)
async def unban_user(
    user_id: int,
    _: str = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    await session.execute(
        update(User).where(User.id == user_id).values(is_banned=False)
    )
    await session.commit()
    return RedirectResponse(url="/admin/users?show_banned=1", status_code=303)


@app.get("/admin/reports", response_class=HTMLResponse)
async def reports_list(
    request: Request,
    _: str = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    status_filter: str = "pending",
) -> HTMLResponse:
    """Show abuse reports. Default to pending only."""
    Reporter = User.__table__.alias("reporter")
    Reported = User.__table__.alias("reported")
    stmt = (
        select(
            Report.id, Report.reason, Report.comment, Report.status,
            Report.created_at, Report.reviewed_at,
            Report.reporter_id, Report.reported_id,
            Reporter.c.telegram_id.label("reporter_tg"),
            Reported.c.telegram_id.label("reported_tg"),
            Reported.c.is_banned.label("reported_banned"),
        )
        .join(Reporter, Reporter.c.id == Report.reporter_id)
        .join(Reported, Reported.c.id == Report.reported_id)
        .order_by(Report.created_at.desc())
        .limit(200)
    )
    if status_filter and status_filter != "all":
        stmt = stmt.where(Report.status == status_filter)

    rows = (await session.execute(stmt)).all()
    pending_count = await session.scalar(
        select(func.count()).select_from(Report).where(Report.status == "pending")
    )
    return templates.TemplateResponse(
        request,
        "reports.html",
        {
            "rows": rows,
            "status_filter": status_filter,
            "pending_count": pending_count,
        },
    )


@app.post("/admin/reports/{report_id}/confirm", include_in_schema=False)
async def confirm_report(
    report_id: int,
    _: str = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    """Confirm the report → ban the reported user."""
    report = await session.get(Report, report_id)
    if report and report.status == "pending":
        report.status = "confirmed"
        report.reviewed_at = datetime.now(timezone.utc)
        await session.execute(
            update(User).where(User.id == report.reported_id).values(is_banned=True)
        )
        await session.commit()
    return RedirectResponse(url="/admin/reports", status_code=303)


@app.post("/admin/reports/{report_id}/dismiss", include_in_schema=False)
async def dismiss_report(
    report_id: int,
    _: str = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    """Dismiss without action."""
    report = await session.get(Report, report_id)
    if report and report.status == "pending":
        report.status = "dismissed"
        report.reviewed_at = datetime.now(timezone.utc)
        await session.commit()
    return RedirectResponse(url="/admin/reports", status_code=303)


@app.get("/admin/stats", response_class=HTMLResponse)
async def stats(
    request: Request,
    _: str = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    days: int = 14,
) -> HTMLResponse:
    """Headline counters + per-day series for the dashboard."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    headline = {
        "users":     await session.scalar(select(func.count()).select_from(User)),
        "banned":    await session.scalar(select(func.count()).select_from(User).where(User.is_banned.is_(True))),
        "profiles":  await session.scalar(select(func.count()).select_from(UserProfile)),
        "photos":    await session.scalar(select(func.count()).select_from(Photo)),
        "likes":     await session.scalar(select(func.count()).select_from(Like)),
        "matches":   await session.scalar(select(func.count()).select_from(Match)),
        "referrals": await session.scalar(select(func.count()).select_from(Referral)),
        "blocks":    await session.scalar(select(func.count()).select_from(Block)),
        "reports":   await session.scalar(select(func.count()).select_from(Report)),
        "pending_reports": await session.scalar(
            select(func.count()).select_from(Report).where(Report.status == "pending")
        ),
    }

    # Per-day registrations / likes / matches for a simple chart.
    day = func.date_trunc("day", User.created_at).label("day")
    registrations = (await session.execute(
        select(day, func.count().label("n"))
        .where(User.created_at >= cutoff)
        .group_by(day).order_by(day)
    )).all()
    likes_day = func.date_trunc("day", Like.created_at).label("day")
    likes = (await session.execute(
        select(likes_day, func.count().label("n"))
        .where(Like.created_at >= cutoff)
        .group_by(likes_day).order_by(likes_day)
    )).all()
    matches_day = func.date_trunc("day", Match.created_at).label("day")
    matches = (await session.execute(
        select(matches_day, func.count().label("n"))
        .where(Match.created_at >= cutoff)
        .group_by(matches_day).order_by(matches_day)
    )).all()

    def _ser(rows):
        return [{"date": r[0].strftime("%Y-%m-%d"), "value": int(r[1])} for r in rows]

    return templates.TemplateResponse(
        request,
        "stats.html",
        {
            "headline": headline,
            "days": days,
            "registrations": _ser(registrations),
            "likes": _ser(likes),
            "matches": _ser(matches),
        },
    )


@app.get("/healthz", include_in_schema=False)
async def healthz() -> dict:
    return {"status": "ok"}
