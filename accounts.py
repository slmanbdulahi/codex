import hashlib
import hmac
import logging
import secrets
import sqlite3
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from database import ROLES, audit, database, now, valid_email
from passwords import hash_password, valid_new_password, verify_password
from web_helpers import Form, redirect, render, text_field

router = APIRouter()
LOGGER = logging.getLogger(__name__)
OPERATIONS_ROLES = ("admin", "staff")
FINANCE_ROLES = ("admin", "finance")
WINDOW_SECONDS = 15 * 60
DUMMY_HASH = hash_password(secrets.token_urlsafe())


def db_for(request, *, write=False):
    return database(request.app.state.settings.database, write=write)


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def current_user(request, conn):
    token = request.session.get("sid")
    if not token:
        return None
    user = conn.execute(
        """
        SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id
        WHERE s.token_hash=? AND s.expires_at>? AND u.active=1
    """,
        (token_hash(token), int(time.time())),
    ).fetchone()
    if user is None or user["role"] not in ROLES:
        request.session.clear()
        return None
    return user


def require_user(request, conn, roles=ROLES):
    user = current_user(request, conn)
    if user is None:
        raise HTTPException(303, headers={"Location": "/login"})
    if user["role"] not in roles:
        raise HTTPException(403, "Your role does not allow this action")
    request.state.user = {key: user[key] for key in ("id", "name", "role")}
    return user


def reserve_login_attempt(request, email):
    timestamp = int(time.time())
    address = request.client.host if request.client else ""
    key = request.app.state.settings.secret_key.encode()
    buckets = [
        (hmac.digest(key, f"pair:{address}:{email}".encode(), "sha256").hex(), 5),
        (hmac.digest(key, f"address:{address}".encode(), "sha256").hex(), 25),
    ]
    with db_for(request, write=True) as conn:
        conn.execute("DELETE FROM login_limits WHERE window_start<=?", (timestamp - WINDOW_SECONDS,))
        for bucket, limit in buckets:
            row = conn.execute("SELECT * FROM login_limits WHERE bucket=?", (bucket,)).fetchone()
            if row and row["attempts"] >= limit:
                if row["attempts"] == limit:
                    conn.execute("UPDATE login_limits SET attempts=attempts+1 WHERE bucket=?", (bucket,))
                    audit(conn, None, "login_throttled")
                return row["window_start"] + WINDOW_SECONDS - timestamp
        for bucket, _ in buckets:
            conn.execute(
                """
                INSERT INTO login_limits (bucket,window_start,attempts) VALUES (?,?,1)
                ON CONFLICT(bucket) DO UPDATE SET attempts=attempts+1
            """,
                (bucket, timestamp),
            )
    return None


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    with db_for(request) as conn:
        if current_user(request, conn):
            return redirect("/")
    return render(request, "login.html")


@router.post("/login")
def login(request: Request, form: Form):
    email = form.get("email", "").strip().lower()[:255]
    password = form.get("password", "")
    if retry_after := reserve_login_attempt(request, email):
        raise HTTPException(
            429, "Too many sign-in attempts. Try again later.", headers={"Retry-After": str(retry_after)}
        )

    with db_for(request) as conn:
        user = conn.execute("SELECT * FROM users WHERE email=? AND active=1", (email,)).fetchone()
    verified = verify_password(password, user["password_hash"] if user else DUMMY_HASH)
    # Apply the current password policy to administrators, including accounts from the legacy bootstrap.
    accepted = (
        user
        and verified
        and user["role"] in ROLES
        and (user["role"] != "admin" or valid_new_password(password))
    )
    with db_for(request, write=True) as conn:
        if accepted:
            latest = conn.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
            accepted = latest and all(latest[key] == user[key] for key in ("active", "role", "password_hash"))
        if not accepted:
            audit(conn, None, "login_failed")
            return redirect("/login", error="Invalid email or password")
        token = secrets.token_urlsafe(32)
        timestamp = int(time.time())
        if previous := request.session.get("sid"):
            conn.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash(previous),))
        conn.execute("DELETE FROM sessions WHERE expires_at<=?", (timestamp,))
        conn.execute(
            "INSERT INTO sessions VALUES (?,?,?)",
            (token_hash(token), user["id"], timestamp + request.app.state.settings.session_seconds),
        )
        audit(conn, user["id"], "login", "user", user["id"])
    request.session.clear()
    request.session["sid"] = token
    return redirect("/")


@router.post("/logout")
def logout(request: Request, form: Form):
    with db_for(request, write=True) as conn:
        user = current_user(request, conn)
        if user:
            conn.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash(request.session["sid"]),))
            audit(conn, user["id"], "logout", "user", user["id"])
    request.session.clear()
    return redirect("/login")


@router.get("/users", response_class=HTMLResponse)
def users(request: Request):
    with db_for(request) as conn:
        require_user(request, conn, ("admin",))
        rows = conn.execute(
            "SELECT id,name,email,role,active,created_at FROM users ORDER BY id DESC"
        ).fetchall()
    return render(request, "users.html", rows=rows)


@router.get("/users/new", response_class=HTMLResponse)
def user_new_page(request: Request):
    with db_for(request) as conn:
        require_user(request, conn, ("admin",))
    return render(request, "user_form.html")


@router.post("/users/new")
def user_new(request: Request, form: Form):
    with db_for(request) as conn:
        require_user(request, conn, ("admin",))
    try:
        name = text_field(form, "name", 120, required=True)
        email = text_field(form, "email", 254, required=True).lower()
        role = form.get("role", "staff")
        password = form.get("password", "")
        if not valid_email(email) or role not in ROLES or not valid_new_password(password):
            raise ValueError("Enter a valid email, role, and password of 15 to 256 characters")
        encoded = hash_password(password)
        with db_for(request, write=True) as conn:
            actor = require_user(request, conn, ("admin",))
            cursor = conn.execute(
                "INSERT INTO users (name,email,password_hash,role,active,created_at) VALUES (?,?,?,?,1,?)",
                (name, email, encoded, role, now()),
            )
            audit(conn, actor["id"], "create", "user", cursor.lastrowid)
    except ValueError as exc:
        return redirect("/users/new", error=str(exc))
    except sqlite3.IntegrityError:
        return redirect("/users/new", error="This email is already registered")
    return redirect("/users", ok="User created")


@router.get("/audit", response_class=HTMLResponse)
def audit_logs(request: Request):
    with db_for(request) as conn:
        require_user(request, conn, ("admin",))
        rows = conn.execute("""
            SELECT audit_logs.*,users.name user_name FROM audit_logs
            LEFT JOIN users ON users.id=audit_logs.user_id ORDER BY audit_logs.id DESC LIMIT 200
        """).fetchall()
    return render(request, "audit.html", rows=rows)


@router.get("/account/password", response_class=HTMLResponse)
def password_page(request: Request):
    with db_for(request) as conn:
        require_user(request, conn)
    return render(request, "password_form.html")


@router.post("/account/password")
def password_change(request: Request, form: Form):
    with db_for(request) as conn:
        user = require_user(request, conn)
    current, new, confirmation = (
        form.get(field, "") for field in ("current_password", "new_password", "confirm_password")
    )
    if not valid_new_password(new) or new != confirmation or new == current:
        return redirect(
            "/account/password", error="Enter and confirm a different password of 15 to 256 characters"
        )
    if not verify_password(current, user["password_hash"]):
        with db_for(request, write=True) as conn:
            actor = require_user(request, conn)
            audit(conn, actor["id"], "password_change_failed", "user", actor["id"])
        return redirect("/account/password", error="Current password is incorrect")
    encoded = hash_password(new)
    with db_for(request, write=True) as conn:
        actor = require_user(request, conn)
        conn.execute("UPDATE users SET password_hash=? WHERE id=?", (encoded, actor["id"]))
        audit(conn, actor["id"], "password_changed", "user", actor["id"])
    request.session.clear()
    return redirect("/login", ok="Password changed. Sign in again.")


@router.get("/api/health")
def health(request: Request):
    try:
        with db_for(request) as conn:
            conn.execute("SELECT 1 FROM users LIMIT 1").fetchone()
    except sqlite3.Error:
        LOGGER.warning("Database readiness check failed")
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return {"status": "ok"}
