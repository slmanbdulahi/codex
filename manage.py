import argparse
import getpass
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import NamedTemporaryFile

from database import ROLES, audit, database
from passwords import hash_password, valid_new_password


def verify(conn):
    if conn.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise RuntimeError("SQLite integrity check failed")
    if conn.execute("PRAGMA foreign_key_check").fetchone():
        raise RuntimeError("SQLite foreign key check failed")
    if not conn.execute("SELECT 1 FROM sqlite_schema WHERE name='users' AND type='table'").fetchone():
        raise RuntimeError("This file does not contain the business manager schema")


def backup(source: Path, destination: Path):
    if destination.exists():
        raise RuntimeError("Choose a new destination path")
    with NamedTemporaryFile(dir=destination.parent, delete=False) as temp:
        staging = Path(temp.name)
    try:
        with (
            closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as src,
            closing(sqlite3.connect(staging)) as dest,
        ):
            src.backup(dest)
            verify(dest)
        with staging.open("r+b") as stream:
            os.fsync(stream.fileno())
        os.link(staging, destination)
    finally:
        staging.unlink()


def reset_password(path, email):
    if not path.is_file():
        raise RuntimeError("Database does not exist")
    password = getpass.getpass("New password: ")
    confirmation = getpass.getpass("Confirm password: ")
    if not valid_new_password(password) or password != confirmation:
        raise RuntimeError("Enter matching passwords of 15 to 256 characters")
    encoded = hash_password(password)
    with database(path, write=True) as conn:
        row = conn.execute(
            "UPDATE users SET password_hash=? WHERE email=? RETURNING id",
            (encoded, email.strip().lower()),
        ).fetchone()
        if row is None:
            raise RuntimeError("Account not found")
        audit(conn, None, "operator_password_reset", "user", row["id"])


def set_access(path, email, role, active):
    if not path.is_file():
        raise RuntimeError("Database does not exist")
    with database(path, write=True) as conn:
        row = conn.execute(
            "UPDATE users SET role=?,active=? WHERE email=? RETURNING id",
            (role, active, email.strip().lower()),
        ).fetchone()
        if row is None:
            raise RuntimeError("Account not found")
        if not conn.execute("SELECT 1 FROM users WHERE active=1 AND role='admin'").fetchone():
            raise RuntimeError("Keep at least one active administrator")
        audit(conn, None, "operator_access_changed", "user", row["id"])


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description="Operate a local Business Manager database")
    commands = parser.add_subparsers(dest="command", required=True)
    copy = commands.add_parser("backup", help="Write a verified snapshot to a new path")
    copy.add_argument("database", type=Path)
    copy.add_argument("destination", type=Path)
    reset = commands.add_parser("reset-password", help="Reset a local account password")
    reset.add_argument("database", type=Path)
    reset.add_argument("email")
    access = commands.add_parser("set-access", help="Change account role and status")
    access.add_argument("database", type=Path)
    access.add_argument("email")
    access.add_argument("role", choices=ROLES)
    access.add_argument("state", choices=("active", "inactive"))
    args = parser.parse_args()
    try:
        if args.command == "backup":
            backup(args.database, args.destination)
        elif args.command == "reset-password":
            reset_password(args.database, args.email)
        else:
            set_access(args.database, args.email, args.role, args.state == "active")
    except (RuntimeError, OSError, sqlite3.Error) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
