import os
import re
import secrets
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Barrier

import pytest
from fastapi.testclient import TestClient

from app import create_app
from database import database, init_db
from manage import backup, set_access
from money import parse_minor
from settings import Settings

ADMIN = "owner@example.invalid"
CLIENT_URL = "http://localhost"


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", ADMIN)
    monkeypatch.setenv("ADMIN_PASSWORD", secrets.token_urlsafe(24))
    return Settings(tmp_path / "business.db", False, secrets.token_urlsafe(48), ("localhost",), "")


@pytest.fixture
def browser(settings):
    with TestClient(create_app(settings), base_url=CLIENT_URL, follow_redirects=False) as client:
        yield client


def field(response, name):
    assert response.status_code == 200, response.text
    return re.search(rf'name="{name}" value="([^"]+)"', response.text)[1]


def login(client, email=ADMIN, password=None):
    return client.post(
        "/login",
        data={
            "_csrf": field(client.get("/login"), "_csrf"),
            "email": email,
            "password": password or os.environ["ADMIN_PASSWORD"],
        },
    )


@pytest.fixture
def admin(browser):
    assert login(browser).headers["location"] == "/"
    return browser


def new_account(client, role):
    email = f"{role}@example.invalid"
    password = secrets.token_urlsafe(24)
    page = client.get("/users/new")
    response = client.post(
        "/users/new",
        data={
            "_csrf": field(page, "_csrf"),
            "email": email,
            "password": password,
            "name": role,
            "role": role,
        },
    )
    assert response.headers["location"].startswith("/users?ok=")
    return email, password


def new_invoice_form(client, amount):
    page = client.get("/invoices/new")
    return {
        "_csrf": field(page, "_csrf"),
        "request_token": field(page, "request_token"),
        "client_id": "1",
        "amount": amount,
    }


def new_payment_form(client, invoice_id=1, amount="0.10", reference=""):
    page = client.get(f"/invoices/{invoice_id}/payment")
    return {
        "_csrf": field(page, "_csrf"),
        "request_token": field(page, "request_token"),
        "amount": amount,
        "method": "Bank transfer",
        "reference": reference,
        "paid_at": "2026-01-02",
    }


def create_client(client):
    response = client.post(
        "/clients/new", data={"_csrf": field(client.get("/clients/new"), "_csrf"), "name": "Example customer"}
    )
    assert response.headers["location"].startswith("/clients?ok=")


def test_bootstrap_rejects_missing_credentials(settings, monkeypatch):
    monkeypatch.delenv("ADMIN_PASSWORD")
    with pytest.raises(RuntimeError, match="First startup"):
        init_db(settings.database)
    with database(settings.database) as conn:
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


def test_production_requires_secret_hosts_and_secure_cookies(settings, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DATABASE_PATH", str(settings.database))
    monkeypatch.delenv("SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        Settings.from_env()
    monkeypatch.setenv("SECRET_KEY", settings.secret_key)
    monkeypatch.setenv("ALLOWED_HOSTS", "*")
    with pytest.raises(RuntimeError, match="exact hostnames"):
        Settings.from_env()
    monkeypatch.setenv("ALLOWED_HOSTS", "localhost")
    monkeypatch.setenv("COOKIE_SECURE", "0")
    with pytest.raises(RuntimeError, match="secure cookies"):
        Settings.from_env()
    monkeypatch.setenv("COOKIE_SECURE", "1")
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "*")
    with pytest.raises(RuntimeError, match="explicit IP"):
        Settings.from_env()


def test_secure_cookie_and_host_validation(settings):
    with TestClient(create_app(replace(settings, production=True)), base_url="https://localhost") as client:
        cookie = client.get("/login").headers["set-cookie"].lower()
        assert all(flag in cookie for flag in ("httponly", "samesite=lax", "secure", "max-age=28800"))
        assert client.get("/login", headers={"Host": "untrusted.example"}).status_code == 400
        assert client.get("/openapi.json").status_code == 404


def test_csrf_and_logout_revoke_cookie(admin):
    cookie = admin.cookies.get("zubrify_session")
    assert admin.post("/logout", data={"_csrf": "wrong"}).status_code == 403
    assert admin.get("/logout").status_code == 405
    assert admin.post("/logout", data={"_csrf": field(admin.get("/"), "_csrf")}).status_code == 303
    admin.cookies.set("zubrify_session", cookie)
    assert admin.get("/").headers["location"] == "/login"


@pytest.mark.parametrize("change", ["active=0", "role='finance'", "password_hash='changed'"])
def test_database_account_changes_revoke_sessions(admin, settings, change):
    with database(settings.database, write=True) as conn:
        conn.execute(f"UPDATE users SET {change} WHERE email=?", (ADMIN,))
        assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    assert admin.get("/users").headers["location"] == "/login"


def test_absolute_expiry(admin, settings):
    with database(settings.database, write=True) as conn:
        conn.execute("UPDATE sessions SET expires_at=?", (int(time.time()) - 1,))
    assert admin.get("/").headers["location"] == "/login"


def test_password_change_revokes_other_sessions(admin, settings):
    with TestClient(admin.app, base_url=CLIENT_URL, follow_redirects=False) as other:
        assert login(other).headers["location"] == "/"
        current = os.environ["ADMIN_PASSWORD"]
        new = secrets.token_urlsafe(24)
        response = admin.post(
            "/account/password",
            data={
                "_csrf": field(admin.get("/account/password"), "_csrf"),
                "current_password": current,
                "new_password": new,
                "confirm_password": new,
            },
        )
        assert response.headers["location"].startswith("/login?ok=")
        assert other.get("/").headers["location"] == "/login"
        assert login(other, password=new).headers["location"] == "/"
    with database(settings.database) as conn:
        assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1


def test_login_rate_limit_and_redacted_audit(browser, settings):
    submitted = {
        "_csrf": field(browser.get("/login"), "_csrf"),
        "email": ADMIN,
        "password": "synthetic wrong password",
    }
    for _ in range(5):
        assert browser.post("/login", data=submitted).headers["location"].startswith("/login?error=")
    blocked = browser.post("/login", data=submitted)
    assert blocked.status_code == 429 and int(blocked.headers["retry-after"]) > 0
    with database(settings.database) as conn:
        rows = conn.execute("SELECT action,details,user_id FROM audit_logs").fetchall()
    assert [tuple(row) for row in rows] == [("login_failed", "", None)] * 5 + [("login_throttled", "", None)]


@pytest.mark.parametrize(
    ("role", "allowed", "denied"),
    [
        (
            "staff",
            ("/clients", "/projects", "/tasks"),
            ("/users", "/audit", "/invoices", "/invoices/1/payment"),
        ),
        (
            "finance",
            ("/clients", "/invoices", "/invoices/new"),
            ("/users", "/audit", "/projects", "/tasks", "/clients/new"),
        ),
    ],
)
def test_least_privilege(admin, role, allowed, denied):
    email, password = new_account(admin, role)
    with TestClient(admin.app, base_url=CLIENT_URL, follow_redirects=False) as client:
        assert login(client, email, password).headers["location"] == "/"
        assert all(client.get(path).status_code == 200 for path in allowed)
        assert all(client.get(path).status_code == 403 for path in denied)
        token = field(client.get("/account/password"), "_csrf")
        endpoint = "/invoices/1/payment" if role == "staff" else "/clients/new"
        assert client.post(endpoint, data={"_csrf": token}).status_code == 403


def test_projects_and_tasks_round_trip(admin):
    create_client(admin)
    for page, fields in (
        ("/projects/new", {"name": "Example project", "budget": "12.34", "client_id": "1"}),
        ("/tasks/new", {"title": "Example task", "project_id": "1"}),
        ("/tasks/1/edit", {"title": "Updated task", "status": "done", "project_id": "1"}),
    ):
        response = admin.post(page, data={"_csrf": field(admin.get(page), "_csrf"), **fields})
        assert "?ok=" in response.headers["location"]
    assert "Updated task" in admin.get("/").text


@pytest.mark.parametrize("value", ["nan", "inf", "-1", "0", "0.001", "1e3", "1000000000.01", "１２"])
def test_money_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        parse_minor(value)


def test_invoice_and_payment_retries(admin, settings):
    create_client(admin)
    invoice = new_invoice_form(admin, "0.30")
    assert "created" in admin.post("/invoices/new", data=invoice).headers["location"]
    assert "already" in admin.post("/invoices/new", data=invoice).headers["location"]
    assert admin.post("/invoices/new", data=invoice | {"amount": "0.31"}).status_code == 409
    path = "/invoices/1/payment"
    payment = new_payment_form(admin, reference="bank-example")
    assert admin.post(path, data=payment | {"_csrf": ""}).status_code == 403
    assert "recorded" in admin.post(path, data=payment).headers["location"]
    assert "already" in admin.post(path, data=payment).headers["location"]
    assert admin.post(path, data=payment | {"amount": "0.11"}).status_code == 409
    assert admin.post(path, data=new_payment_form(admin, reference="bank-example")).status_code == 409
    assert "recorded" in admin.post(path, data=new_payment_form(admin, amount="0.20")).headers["location"]
    with database(settings.database) as conn:
        assert tuple(conn.execute("SELECT amount_minor,status FROM invoices").fetchone()) == (30, "paid")
        assert conn.execute("SELECT SUM(amount_minor),COUNT(*) FROM payments").fetchone()[:] == (30, 2)
        assert conn.execute("SELECT COUNT(*) FROM audit_logs WHERE entity_type='payment'").fetchone()[0] == 2
    assert "KSh 0.30" in admin.get("/invoices").text
    assert "Save payment" not in admin.get(path).text


def parallel_submissions(admin, path, payloads):
    cookie = admin.cookies.get("zubrify_session")
    gate = Barrier(2)

    def submit(payload):
        with TestClient(admin.app, base_url=CLIENT_URL, follow_redirects=False) as client:
            client.cookies.set("zubrify_session", cookie)
            gate.wait(timeout=5)
            return client.post(path, data=payload).headers["location"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        return list(pool.map(submit, payloads))


def test_concurrent_invoice_numbers_and_payments(admin, settings):
    create_client(admin)
    assert all(
        "created" in location
        for location in parallel_submissions(
            admin, "/invoices/new", [new_invoice_form(admin, "1.00"), new_invoice_form(admin, "1.00")]
        )
    )
    path = "/invoices/1/payment"
    results = parallel_submissions(
        admin, path, [new_payment_form(admin, amount="0.75"), new_payment_form(admin, amount="0.75")]
    )
    assert sum("recorded" in result for result in results) == 1
    assert sum("exceeds" in result for result in results) == 1
    with database(settings.database) as conn:
        assert conn.execute("SELECT COUNT(DISTINCT invoice_no) FROM invoices").fetchone()[0] == 2
        assert conn.execute("SELECT SUM(amount_minor),COUNT(*) FROM payments").fetchone()[:] == (75, 1)
        assert conn.execute("SELECT status FROM invoices WHERE id=1").fetchone()[0] == "partial"


def test_failed_audit_rolls_back_ledger_write(admin, settings):
    create_client(admin)
    assert "created" in admin.post("/invoices/new", data=new_invoice_form(admin, "0.10")).headers["location"]
    with database(settings.database, write=True) as conn:
        conn.execute("""
            CREATE TRIGGER fail_audit BEFORE INSERT ON audit_logs WHEN NEW.entity_type='payment'
            BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END
        """)
    with pytest.raises(sqlite3.IntegrityError):
        admin.post("/invoices/1/payment", data=new_payment_form(admin))
    with database(settings.database) as conn:
        assert conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 0
        assert conn.execute("SELECT status FROM invoices").fetchone()[0] == "unpaid"


def test_upgrade_backup_and_role_operation(settings, tmp_path):
    schema = Path("schema.sql").read_text(encoding="utf-8").split("CREATE TABLE IF NOT EXISTS sessions")[0]
    with database(settings.database) as conn:
        conn.executescript(schema)
        conn.execute("INSERT INTO users VALUES (1,'Owner',?,'legacy','admin',1,'2026-01-01')", (ADMIN,))
        conn.execute("INSERT INTO invoices VALUES (4,NULL,'ZUB-2026-0010','',12.34,'','unpaid','2026-01-01')")
        conn.execute("INSERT INTO payments VALUES (3,4,2.23,'Cash','','2026-01-01','2026-01-01')")
    old_snapshot = tmp_path / "before.db"
    backup(settings.database, old_snapshot)
    init_db(settings.database)
    init_db(settings.database)
    with database(settings.database) as conn:
        assert conn.execute("SELECT id,amount,amount_minor,status FROM invoices").fetchone()[:] == (
            4,
            12.34,
            1234,
            "partial",
        )
        assert conn.execute("SELECT amount_minor FROM payments").fetchone()[0] == 223
        assert conn.execute("SELECT last_number FROM invoice_sequence").fetchone()[0] == 10
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    with database(old_snapshot) as conn:
        assert conn.execute("SELECT status FROM invoices").fetchone()[0] == "unpaid"
    with pytest.raises(RuntimeError, match="active administrator"):
        set_access(settings.database, ADMIN, "finance", True)
    with database(settings.database) as conn:
        assert conn.execute("SELECT role FROM users").fetchone()[0] == "admin"
