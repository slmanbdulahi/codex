import re
import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from accounts import FINANCE_ROLES, db_for, require_user
from database import audit, now
from money import as_money, parse_minor
from web_helpers import Form, date_field, id_field, redirect, render, text_field

router = APIRouter()
METHODS = ("M-Pesa", "Bank transfer", "Cash", "Card", "Other")
TOKEN_PATTERN = re.compile(r"[a-zA-Z0-9_-]{32}")
INVOICE_NO_PATTERN = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_./-]{0,63}")
INVOICE_DETAILS = """
    SELECT i.*,c.name client_name,
        COALESCE((SELECT SUM(p.amount_minor) FROM payments p WHERE p.invoice_id=i.id),0) paid_minor
    FROM invoices i LEFT JOIN clients c ON c.id=i.client_id
"""


def request_token(form):
    token = form.get("request_token", "")
    if not TOKEN_PATTERN.fullmatch(token):
        raise ValueError("Refresh the form before submitting")
    return token


def invoice_number(conn):
    year = datetime.now(UTC).year
    while True:
        number = conn.execute(
            "UPDATE invoice_sequence SET last_number=last_number+1 WHERE id=1 RETURNING last_number"
        ).fetchone()[0]
        identifier = f"ZUB-{year}-{number:04d}"
        if not conn.execute("SELECT 1 FROM invoices WHERE invoice_no=?", (identifier,)).fetchone():
            return identifier


def invoice_view(row):
    return dict(row) | {"amount": as_money(row["amount_minor"]), "paid_amount": as_money(row["paid_minor"])}


@router.get("/invoices", response_class=HTMLResponse)
def invoices(request: Request):
    with db_for(request) as conn:
        require_user(request, conn, FINANCE_ROLES)
        rows = conn.execute(INVOICE_DETAILS + " ORDER BY i.id DESC").fetchall()
    return render(request, "invoices.html", rows=[invoice_view(row) for row in rows])


@router.get("/invoices/new", response_class=HTMLResponse)
def invoice_new_page(request: Request):
    with db_for(request) as conn:
        require_user(request, conn, FINANCE_ROLES)
        clients = conn.execute("SELECT id,name FROM clients ORDER BY name").fetchall()
    return render(request, "invoice_form.html", clients=clients, request_token=secrets.token_urlsafe(24))


@router.post("/invoices/new")
def invoice_new(request: Request, form: Form):
    with db_for(request, write=True) as conn:
        actor = require_user(request, conn, FINANCE_ROLES)
        try:
            token = request_token(form)
            client = id_field(form, "client_id", required=True)
            description = text_field(form, "description", 4_000)
            due_date = date_field(form, "due_date")
            amount = parse_minor(form.get("amount", ""))
            custom_number = form.get("invoice_no", "").strip()
            if custom_number and not INVOICE_NO_PATTERN.fullmatch(custom_number):
                raise ValueError("Enter a valid invoice number or leave it blank")
        except ValueError as exc:
            return redirect("/invoices/new", error=str(exc))
        key = f"{actor['id']}:{token}"
        existing = conn.execute("SELECT * FROM invoices WHERE request_key=?", (key,)).fetchone()
        if existing:
            same = (
                existing["client_id"],
                existing["description"],
                existing["due_date"],
                existing["amount_minor"],
            ) == (
                client,
                description,
                due_date,
                amount,
            )
            if not same or (custom_number and custom_number != existing["invoice_no"]):
                raise HTTPException(409, "This form was already submitted with different invoice details")
            return redirect("/invoices", ok="Invoice already recorded")
        if not conn.execute("SELECT 1 FROM clients WHERE id=?", (client,)).fetchone():
            return redirect("/invoices/new", error="Select an existing client")
        if (
            custom_number
            and conn.execute("SELECT 1 FROM invoices WHERE invoice_no=?", (custom_number,)).fetchone()
        ):
            return redirect("/invoices/new", error="Invoice number already exists")

        number = custom_number or invoice_number(conn)
        cursor = conn.execute(
            """
            INSERT INTO invoices (client_id,invoice_no,description,amount,amount_minor,due_date,status,created_at,request_key)
            VALUES (?,?,?,?,?,?,'unpaid',?,?)
        """,
            (client, number, description, amount / 100, amount, due_date, now(), key),
        )
        audit(conn, actor["id"], "create", "invoice", cursor.lastrowid)
    return redirect("/invoices", ok="Invoice created")


def get_invoice(conn, invoice_id):
    row = conn.execute(INVOICE_DETAILS + " WHERE i.id=?", (invoice_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Invoice not found")
    return row


@router.get("/invoices/{id}/payment", response_class=HTMLResponse)
def payment_page(id: int, request: Request):
    if not 0 < id < 2**63:
        raise HTTPException(404, "Invoice not found")
    with db_for(request) as conn:
        require_user(request, conn, FINANCE_ROLES)
        invoice = get_invoice(conn, id)
    return render(
        request,
        "payment_form.html",
        inv=invoice_view(invoice),
        balance=as_money(invoice["amount_minor"] - invoice["paid_minor"]),
        methods=METHODS,
        payment_date=datetime.now(UTC).date().isoformat(),
        request_token=secrets.token_urlsafe(24),
    )


@router.post("/invoices/{id}/payment")
def payment_create(id: int, request: Request, form: Form):
    if not 0 < id < 2**63:
        raise HTTPException(404, "Invoice not found")
    path = f"/invoices/{id}/payment"
    with db_for(request, write=True) as conn:
        actor = require_user(request, conn, FINANCE_ROLES)
        try:
            token = request_token(form)
            amount = parse_minor(form.get("amount", ""))
            method = form.get("method", "")
            reference = text_field(form, "reference", 128)
            paid_at = date_field(form, "paid_at")
            if method not in METHODS or not paid_at:
                raise ValueError("Select a payment method and date")
        except ValueError as exc:
            return redirect(path, error=str(exc))
        key = f"{actor['id']}:{token}"
        existing = conn.execute("SELECT * FROM payments WHERE request_key=?", (key,)).fetchone()
        if existing:
            if (
                existing["invoice_id"],
                existing["amount_minor"],
                existing["method"],
                existing["reference"],
                existing["paid_at"],
            ) != (id, amount, method, reference, paid_at):
                raise HTTPException(409, "This form was already submitted with different payment details")
            return redirect("/invoices", ok="Payment already recorded")

        invoice = get_invoice(conn, id)
        if (
            reference
            and conn.execute(
                "SELECT 1 FROM payments WHERE method=? AND trim(reference)=?",
                (method, reference),
            ).fetchone()
        ):
            raise HTTPException(409, "This payment reference has already been recorded")
        new_total = invoice["paid_minor"] + amount
        if new_total > invoice["amount_minor"]:
            return redirect(path, error="Payment exceeds the invoice balance")

        cursor = conn.execute(
            """
            INSERT INTO payments (invoice_id,amount,amount_minor,method,reference,paid_at,created_at,request_key)
            VALUES (?,?,?,?,?,?,?,?)
        """,
            (id, amount / 100, amount, method, reference, paid_at, now(), key),
        )
        status = "paid" if new_total == invoice["amount_minor"] else "partial"
        conn.execute("UPDATE invoices SET status=? WHERE id=?", (status, id))
        audit(conn, actor["id"], "create", "payment", cursor.lastrowid)
    return redirect("/invoices", ok="Payment recorded")
