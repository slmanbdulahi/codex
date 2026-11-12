# Zubrify Business Manager

This branch contains a FastAPI and Jinja2 application backed by SQLite. It manages clients, projects, tasks, invoices, and records of payments received elsewhere. The standalone `index.html` is a Zubrify Pay demonstration. Provider charges, settlements, refunds, and card data collection are outside the Business Manager's scope.

## Run locally

Use Python 3.12, create a virtual environment, and run `python -m pip install --require-hashes -r requirements.txt`. Supply the bootstrap environment variables listed below on an empty database, run `python app.py`, and open `http://127.0.0.1:8000`. The default development database lives in `data/`. Development binds to loopback and uses a process-local signing key unless one is supplied.

| Variable | Use |
| --- | --- |
| `APP_ENV` | Selects development or production behavior. The Docker image uses production mode. |
| `DATABASE_PATH` | Absolute SQLite path in production. Its parent must be writable by the app account. |
| `SECRET_KEY` | Persistent random session signing key in production. Generate with a password manager or the Python `secrets` module. |
| `ALLOWED_HOSTS` | Comma-separated exact HTTP hostnames in production, including the container's loopback healthcheck host. |
| `FORWARDED_ALLOW_IPS` | Explicit trusted immediate proxy IPs or networks. Leave unset for direct connections. |
| `ADMIN_EMAIL` | Initial administrator's email on an empty database. |
| `ADMIN_PASSWORD` | Initial administrator's unique generated password, 15–256 characters. |
| `ADMIN_NAME` | Optional display name for the initial administrator. |
| `PORT` | HTTP listen port. |

The app separates account and session logic (`accounts.py`), business records (`operations.py`), invoices and payments (`finance.py`), and database startup and migrations (`database.py`, `schema.sql`). Signed cookies carry a session token and CSRF token. The database stores a digest of the session token and checks the current account on protected requests. Sessions expire after eight hours and are revoked on logout or changes to a user's password, role, or active state.

| Role | Permissions |
| --- | --- |
| Staff | View and maintain clients, projects, and tasks. |
| Finance | View clients, create invoices, and record payments. |
| Admin | Finance and staff capabilities plus account creation and audit access. |

This is a single-company application. A future multi-company deployment needs company IDs, membership and role checks, scoped queries and foreign keys, company-scoped invoice numbers and payment references, a migration assigning existing rows, and cross-company access tests.

## Upgrade and deployment

1. Keep a single writable SQLite instance on a persistent volume. Mount `/data` with permissions for container UID/GID `10001`. Preserve the configured database path if it differs from the image default.
2. Configure HTTPS at the ingress, set the required production variables, and keep the signing key in the platform secret store across restarts. Include the public hostname and `127.0.0.1` in the host allowlist. On Railway also include `healthcheck.railway.app`. Configure forwarded headers only for verified ingress peers that replace untrusted forwarding headers. If the app cannot identify individual client IPs safely, configure per-client throttling at the ingress before rollout. [Railway healthcheck configuration](https://docs.railway.com/deployments/healthchecks#healthcheck-hostname)
3. Take a verified backup and rehearse the upgrade on a copy. Startup adds integer KSh minor-unit amounts and reconciles invoice statuses from stored payment records. Existing identifiers, relationships, dates, and original amounts are preserved. A legacy ledger with fractional minor units, out-of-range amounts, invalid references, or overpayments requires reconciliation before startup can proceed.
4. Confirm access assignments. Staff accounts now use the operations screens. Use the operator commands below for approved finance assignments and legacy administrator password resets. Existing users keep compatible password hashes. Administrator passwords shorter than 15 characters require an operator reset. The upgrade invalidates earlier cookies, so users sign in again. Fresh passwords must be 15–256 characters.
5. Confirm the health endpoint and sign in to exercise each role in staging before production. Remove the bootstrap password from the environment after a successful first startup. Configure branch protection to require review and passing Business Manager checks before updates to the deployment branch.

The app stores amounts in minor units and enforces a KSh 0.01–1,000,000,000.00 range for new invoices and payments. An identical retry of a submitted form records one entry. A nonblank payment reference is unique per method for new entries, including against existing records. Use the same pending form when retrying a payment with no reference.

## Backup, recovery, and rollback

- Run `python manage.py backup DATABASE DESTINATION` as the database operator. Use a new file path on restricted storage. The command uses SQLite's online backup API and checks integrity and foreign keys before publishing the snapshot. Copy encrypted backups off the database volume and rehearse restoring them.
- Restore to a new path using the backup command with the snapshot as input. Stop application writes before switching paths and preserve the previous database file. Treat the restored ledger as a point-in-time snapshot and reconcile any payments recorded after that point. Rotate the signing key and clear restored session rows before resuming public traffic.
- For code rollback after a schema upgrade, pair the previous release with its verified pre-upgrade database snapshot. Preserve the newer database for payment reconciliation and replay any authorized records added since the backup before reopening writes.
- Use `python manage.py reset-password DATABASE EMAIL` for an operator password reset. Enter the password at the interactive prompts. Use `python manage.py set-access DATABASE EMAIL ROLE STATE` to change role or activation. These commands write audit records and revoke the affected sessions. The last active admin remains required.

## Verify changes

Install `requirements-dev.txt` with `--require-hashes`. Run `python -m pytest -q`, `python -m ruff check .`, `python -m ruff format --check .`, and `python -m pip_audit --require-hashes --disable-pip -r requirements-dev.txt`. GitHub Actions also builds and smoke-tests the non-root container and scans the current source for secrets.

To refresh the locks, use `uv pip compile --universal --python-version 3.12 --generate-hashes requirements.in -o requirements.txt` and repeat with `requirements-dev.in` and `requirements-dev.txt`.
