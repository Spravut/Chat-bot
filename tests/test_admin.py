"""
Integration tests for the admin panel.

Uses FastAPI's TestClient with httpx to drive the app in-process. Hits a
real database via the shared engine — gated behind INTEGRATION_PG_URL like
the isolation tests, since the admin needs a stable schema (it relies on
Postgres-specific functions like `date_trunc`).
"""
from __future__ import annotations

import os

import pytest

PG_URL = os.environ.get("INTEGRATION_PG_URL")

pytestmark = pytest.mark.skipif(
    not PG_URL,
    reason="Set INTEGRATION_PG_URL to a Postgres async URL to run admin tests",
)


@pytest.fixture(scope="module")
def client():
    """Create a TestClient that points the admin app at the integration DB."""
    os.environ.setdefault("DATABASE_URL", PG_URL)
    os.environ.setdefault("ADMIN_USER", "admin")
    os.environ.setdefault("ADMIN_PASSWORD", "secret")
    from fastapi.testclient import TestClient
    from admin.main import app
    with TestClient(app) as c:
        yield c


def test_root_redirects_to_users(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (302, 303, 307)
    assert "/admin/users" in r.headers["location"]


def test_unauthenticated_request_rejected(client):
    r = client.get("/admin/users")
    assert r.status_code == 401
    assert r.headers.get("www-authenticate", "").lower().startswith("basic")


def test_wrong_password_rejected(client):
    r = client.get("/admin/users", auth=("admin", "wrong"))
    assert r.status_code == 401


def test_users_page_loads_with_auth(client):
    r = client.get("/admin/users", auth=("admin", "secret"))
    assert r.status_code == 200
    assert "Users" in r.text


def test_reports_page_loads(client):
    r = client.get("/admin/reports", auth=("admin", "secret"))
    assert r.status_code == 200


def test_stats_page_loads(client):
    r = client.get("/admin/stats", auth=("admin", "secret"))
    assert r.status_code == 200
    assert "Stats" in r.text


def test_healthz_is_public(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
