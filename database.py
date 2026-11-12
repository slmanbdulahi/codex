import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import InvalidOperation

from money import MAX_AMOUNT_MINOR, legacy_minor
from passwords import hash_password, valid_new_password
from settings import BASE_DIR

ROLES = ("staff", "finance", "admin")
EMAIL_PATTERN = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def valid_email(value: str) -> bool:
    return len(value) <= 254 and EMAIL_PATTERN.fullmatch(value) is not None


@contextmanager
def database(path, *, write=False):
    connection = sqlite3.connect(path, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            if write:
                connection.execute("BEGIN IMMEDIATE")
            yield connection
    finally:
        connection.close()


def audit(conn, user_id, action, entity_type="", entity_id=None, details=""):
    conn.execute(
        "INSERT INTO audit_logs (user_id,action,entity_type,entity_id,details,created_at) VALUES (?,?,?,?,?,?)",
        (user_id, action, entity_type, entity_id, details, now()),
    )


def bootstrap_admin(conn):
    if conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
        if not conn.execute("SELECT 1 FROM users WHERE role='admin' AND active=1 LIMIT 1").fetchone():
            raise RuntimeError("The existing database requires an active administrator")
        return
    email = os.getenv("ADMIN_EMAIL", "").strip().lower()
    name = os.getenv("ADMIN_NAME", "Administrator").strip()
    password = os.getenv("ADMIN_PASSWORD", "")
    if not valid_email(email) or not valid_new_password(password) or not 0 < len(name) <= 120:
        raise RuntimeError("First startup requires ADMIN_EMAIL and an ADMIN_PASSWORD of 15 to 256 characters")
    conn.execute(
        "INSERT INTO users (name,email,password_hash,role,active,created_at) VALUES (?,?,?,'admin',1,?)",
        (name, email, hash_password(password), now()),
    )


def init_db(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with database(path) as conn:
        conn.executescript((BASE_DIR / "schema.sql").read_text(encoding="utf-8"))
    with database(path, write=True) as conn:
        bootstrap_admin(conn)
        migrate_ledger(conn)


def migrate_ledger(conn):
    if conn.execute("PRAGMA user_version").fetchone()[0] == 1:
        return
    if conn.execute("PRAGMA user_version").fetchone()[0] != 0:
        raise RuntimeError("Unsupported database schema version")
    if conn.execute("PRAGMA foreign_key_check").fetchone():
        raise RuntimeError("Legacy database has invalid references. Reconcile a backup before upgrading")

    for table in ("invoices", "payments"):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN amount_minor INTEGER")
        conn.execute(f"ALTER TABLE {table} ADD COLUMN request_key TEXT")
        try:
            conn.executemany(
                f"UPDATE {table} SET amount_minor=? WHERE id=?",
                (
                    (legacy_minor(row["amount"]), row["id"])
                    for row in conn.execute(f"SELECT id,amount FROM {table}")
                ),
            )
        except (InvalidOperation, ValueError):
            raise RuntimeError("Legacy ledger amounts require reconciliation before upgrading") from None
        conn.execute(f"CREATE UNIQUE INDEX {table}_request_key ON {table}(request_key)")

    conn.execute("CREATE INDEX payments_invoice_id ON payments(invoice_id)")
    conn.execute("CREATE INDEX payments_method_reference ON payments(method,reference)")
    invalid = conn.execute("""
        SELECT 1 FROM payments WHERE amount_minor <= 0
        UNION ALL
        SELECT 1 FROM invoices i JOIN payments p ON p.invoice_id=i.id
        GROUP BY i.id HAVING SUM(p.amount_minor)>i.amount_minor LIMIT 1
    """).fetchone()
    if invalid:
        raise RuntimeError("Legacy ledger balances require reconciliation before upgrading")
    conn.execute("""
        UPDATE invoices SET status = CASE
            WHEN NOT EXISTS (SELECT 1 FROM payments p WHERE p.invoice_id=invoices.id) THEN 'unpaid'
            WHEN amount_minor = (SELECT SUM(amount_minor) FROM payments p WHERE p.invoice_id=invoices.id) THEN 'paid'
            ELSE 'partial'
        END
    """)

    # Seed once from existing numbers, including custom numbers in the generated format.
    previous = conn.execute("""
        SELECT COALESCE(MAX(CAST(SUBSTR(invoice_no,10) AS INTEGER)),0)
        FROM invoices WHERE invoice_no GLOB 'ZUB-[0-9][0-9][0-9][0-9]-[0-9]*'
        AND length(invoice_no) <= 25 AND SUBSTR(invoice_no,10) NOT GLOB '*[^0-9]*'
    """).fetchone()[0]
    conn.execute("INSERT INTO invoice_sequence (id,last_number) VALUES (1,?)", (previous,))
    for table in ("invoices", "payments"):
        conn.execute(f"""
            CREATE TRIGGER {table}_valid_insert BEFORE INSERT ON {table}
            WHEN typeof(NEW.amount_minor) != 'integer'
                 OR NEW.amount_minor NOT BETWEEN 1 AND {MAX_AMOUNT_MINOR}
                 OR NEW.amount != NEW.amount_minor / 100.0
                 OR NEW.request_key IS NULL
            BEGIN SELECT RAISE(ABORT, 'invalid ledger entry'); END
        """)
        conn.execute(f"""
            CREATE TRIGGER {table}_immutable_amount BEFORE UPDATE OF amount, amount_minor, request_key ON {table}
            BEGIN SELECT RAISE(ABORT, 'ledger amount and request key are immutable'); END
        """)
    conn.execute("""
        CREATE TRIGGER payments_available_balance BEFORE INSERT ON payments
        WHEN NEW.amount_minor > (
            SELECT i.amount_minor - COALESCE((SELECT SUM(p.amount_minor) FROM payments p WHERE p.invoice_id=i.id),0)
            FROM invoices i WHERE i.id=NEW.invoice_id
        )
        BEGIN SELECT RAISE(ABORT, 'payment exceeds balance'); END
    """)
    conn.execute("PRAGMA user_version = 1")
