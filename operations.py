import sqlite3

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from accounts import FINANCE_ROLES, OPERATIONS_ROLES, db_for, require_user
from database import audit, now
from finance import INVOICE_DETAILS, invoice_view
from money import as_money, parse_minor
from web_helpers import Form, date_field, id_field, redirect, render, text_field

router = APIRouter()
CLIENT_READ_ROLES = (*OPERATIONS_ROLES, "finance")
TASKS = """
    SELECT t.*,p.name project_name,u.name assignee FROM tasks t
    LEFT JOIN projects p ON p.id=t.project_id LEFT JOIN users u ON u.id=t.assigned_to
"""


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    with db_for(request) as conn:
        user = require_user(request, conn)
        stats = {}
        recent_tasks = recent_invoices = []
        if user["role"] in OPERATIONS_ROLES:
            stats.update(
                clients=conn.execute("SELECT COUNT(*) FROM clients WHERE status='active'").fetchone()[0],
                projects=conn.execute(
                    "SELECT COUNT(*) FROM projects WHERE status NOT IN ('completed','cancelled')"
                ).fetchone()[0],
                tasks=conn.execute("SELECT COUNT(*) FROM tasks WHERE status!='done'").fetchone()[0],
            )
            recent_tasks = conn.execute(TASKS + " ORDER BY t.id DESC LIMIT 6").fetchall()
        if user["role"] in FINANCE_ROLES:
            invoiced, paid = conn.execute("""
                SELECT (SELECT COALESCE(SUM(amount_minor),0) FROM invoices),
                       (SELECT COALESCE(SUM(amount_minor),0) FROM payments)
            """).fetchone()
            stats.update(unpaid=as_money(invoiced - paid), revenue=as_money(paid))
            recent_invoices = [
                invoice_view(row) for row in conn.execute(INVOICE_DETAILS + " ORDER BY i.id DESC LIMIT 6")
            ]
    return render(
        request, "dashboard.html", stats=stats, recent_tasks=recent_tasks, recent_invoices=recent_invoices
    )


@router.get("/clients", response_class=HTMLResponse)
def clients(request: Request, q: str = ""):
    with db_for(request) as conn:
        require_user(request, conn, CLIENT_READ_ROLES)
        like = f"%{q.strip()[:200]}%"
        rows = conn.execute(
            """
            SELECT * FROM clients WHERE name LIKE ? OR company LIKE ? OR email LIKE ? ORDER BY id DESC
        """,
            (like, like, like),
        ).fetchall()
    return render(request, "clients.html", rows=rows, q=q[:200])


@router.get("/projects", response_class=HTMLResponse)
def projects(request: Request):
    with db_for(request) as conn:
        require_user(request, conn, OPERATIONS_ROLES)
        rows = conn.execute("""
            SELECT p.*,c.name client_name FROM projects p LEFT JOIN clients c ON c.id=p.client_id ORDER BY p.id DESC
        """).fetchall()
    return render(request, "projects.html", rows=rows)


@router.get("/tasks", response_class=HTMLResponse)
def tasks(request: Request):
    with db_for(request) as conn:
        require_user(request, conn, OPERATIONS_ROLES)
        rows = conn.execute(
            TASKS
            + """
            ORDER BY CASE t.status WHEN 'todo' THEN 1 WHEN 'in_progress' THEN 2 ELSE 3 END,t.id DESC
        """
        ).fetchall()
    return render(request, "tasks.html", rows=rows)


def record(conn, table, row_id):
    if not 0 < row_id < 2**63:
        raise HTTPException(404, "Record not found")
    row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (row_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Record not found")
    return row


def edit_form(request, table, row_id=None):
    with db_for(request) as conn:
        require_user(request, conn, OPERATIONS_ROLES)
        row = record(conn, table, row_id) if row_id is not None else None
        choices = {}
        if table == "projects":
            choices["clients"] = conn.execute("SELECT id,name FROM clients ORDER BY name").fetchall()
        if table == "tasks":
            choices["projects"] = conn.execute("SELECT id,name FROM projects ORDER BY name").fetchall()
            choices["users"] = conn.execute(
                "SELECT id,name FROM users WHERE active=1 AND role IN ('staff','admin') ORDER BY name"
            ).fetchall()
    return render(request, f"{table[:-1]}_form.html", row=row, **choices)


def select(form, name, values, default):
    value = form.get(name) or default
    if value not in values:
        raise ValueError(f"Select a valid {name}")
    return value


def client_fields(conn, form):
    return {
        "name": text_field(form, "name", 160, required=True),
        "company": text_field(form, "company", 160),
        "email": text_field(form, "email", 254),
        "phone": text_field(form, "phone", 64),
        "status": select(form, "status", ("active", "inactive", "lead"), "active"),
        "notes": text_field(form, "notes", 4_000),
    }


def project_fields(conn, form):
    budget = form.get("budget", "").strip()
    amount = 0 if budget in {"", "0", "0.0", "0.00"} else parse_minor(budget)
    start_date = date_field(form, "start_date")
    due_date = date_field(form, "due_date")
    if start_date and due_date and due_date < start_date:
        raise ValueError("Due date must be on or after the start date")
    return {
        "client_id": id_field(form, "client_id"),
        "name": text_field(form, "name", 160, required=True),
        "description": text_field(form, "description", 4_000),
        "status": select(
            form, "status", ("planning", "active", "on_hold", "completed", "cancelled"), "planning"
        ),
        "budget": amount / 100,
        "start_date": start_date,
        "due_date": due_date,
    }


def task_fields(conn, form):
    assignee = id_field(form, "assigned_to")
    if (
        assignee
        and not conn.execute(
            "SELECT 1 FROM users WHERE id=? AND active=1 AND role IN ('staff','admin')",
            (assignee,),
        ).fetchone()
    ):
        raise ValueError("Select an active project team member")
    return {
        "project_id": id_field(form, "project_id"),
        "title": text_field(form, "title", 160, required=True),
        "description": text_field(form, "description", 4_000),
        "assigned_to": assignee,
        "priority": select(form, "priority", ("low", "medium", "high", "urgent"), "medium"),
        "status": select(form, "status", ("todo", "in_progress", "blocked", "done"), "todo"),
        "due_date": date_field(form, "due_date"),
    }


def save_record(request, form, table, fields, row_id=None):
    form_path = f"/{table}/new" if row_id is None else f"/{table}/{row_id}/edit"
    label = table[:-1]
    try:
        with db_for(request, write=True) as conn:
            actor = require_user(request, conn, OPERATIONS_ROLES)
            values = fields(conn, form)
            if row_id is None:
                values["created_at"] = now()
                columns = ",".join(values)
                placeholders = ",".join("?" for _ in values)
                cursor = conn.execute(
                    f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", tuple(values.values())
                )
                audit(conn, actor["id"], "create", label, cursor.lastrowid)
            else:
                record(conn, table, row_id)
                assignments = ",".join(f"{column}=?" for column in values)
                conn.execute(f"UPDATE {table} SET {assignments} WHERE id=?", (*values.values(), row_id))
                audit(conn, actor["id"], "update", label, row_id)
    except ValueError as exc:
        return redirect(form_path, error=str(exc))
    except sqlite3.IntegrityError:
        return redirect(form_path, error="Select existing records and try again")
    return redirect(f"/{table}", ok=f"{label.title()} {'created' if row_id is None else 'updated'}")


@router.get("/clients/new", response_class=HTMLResponse)
def client_new_page(request: Request):
    return edit_form(request, "clients")


@router.post("/clients/new")
def client_new(request: Request, form: Form):
    return save_record(request, form, "clients", client_fields)


@router.get("/clients/{id}/edit", response_class=HTMLResponse)
def client_edit_page(id: int, request: Request):
    return edit_form(request, "clients", id)


@router.post("/clients/{id}/edit")
def client_edit(id: int, request: Request, form: Form):
    return save_record(request, form, "clients", client_fields, id)


@router.get("/projects/new", response_class=HTMLResponse)
def project_new_page(request: Request):
    return edit_form(request, "projects")


@router.post("/projects/new")
def project_new(request: Request, form: Form):
    return save_record(request, form, "projects", project_fields)


@router.get("/projects/{id}/edit", response_class=HTMLResponse)
def project_edit_page(id: int, request: Request):
    return edit_form(request, "projects", id)


@router.post("/projects/{id}/edit")
def project_edit(id: int, request: Request, form: Form):
    return save_record(request, form, "projects", project_fields, id)


@router.get("/tasks/new", response_class=HTMLResponse)
def task_new_page(request: Request):
    return edit_form(request, "tasks")


@router.post("/tasks/new")
def task_new(request: Request, form: Form):
    return save_record(request, form, "tasks", task_fields)


@router.get("/tasks/{id}/edit", response_class=HTMLResponse)
def task_edit_page(id: int, request: Request):
    return edit_form(request, "tasks", id)


@router.post("/tasks/{id}/edit")
def task_edit(id: int, request: Request, form: Form):
    return save_record(request, form, "tasks", task_fields, id)
