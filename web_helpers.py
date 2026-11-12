import hmac
import secrets
from datetime import date
from typing import Annotated
from urllib.parse import parse_qsl, urlencode

from fastapi import Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from jinja2 import pass_context

from settings import BASE_DIR

templates = Jinja2Templates(directory=BASE_DIR / "templates")


@pass_context
def relative_url_for(context, name, **params):
    request = context["request"]
    return request.scope.get("root_path", "").rstrip("/") + str(request.app.url_path_for(name, **params))


templates.env.globals["url_for"] = relative_url_for


async def read_form(request: Request) -> dict[str, str]:
    content_type = request.headers.get("content-type", "").partition(";")[0].strip().lower()
    if content_type != "application/x-www-form-urlencoded":
        raise HTTPException(415, "Expected an HTML form")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > 32_768:
            raise HTTPException(413, "Form is too large")
        body.extend(chunk)
    try:
        pairs = parse_qsl(body.decode("utf-8"), keep_blank_values=True, max_num_fields=32, errors="strict")
    except ValueError:
        raise HTTPException(400, "Invalid form") from None
    form = dict(pairs)
    if len(form) != len(pairs):
        raise HTTPException(400, "Form fields must occur once")
    expected = request.session.get("_csrf")
    supplied = form.get("_csrf", "")
    if not expected or not hmac.compare_digest(expected.encode(), supplied.encode()):
        raise HTTPException(403, "Invalid or expired form token. Refresh the page and try again.")
    return form


Form = Annotated[dict[str, str], Depends(read_form)]


def render(request, name, **context):
    if "_csrf" not in request.session:
        request.session["_csrf"] = secrets.token_urlsafe(32)
    messages = [
        (category, request.query_params[key])
        for key, category in (("ok", "success"), ("error", "error"))
        if request.query_params.get(key)
    ]
    return templates.TemplateResponse(
        request=request,
        name=name,
        context={
            "user": getattr(request.state, "user", None),
            "csrf_token": request.session["_csrf"],
            "messages": messages,
            **context,
        },
    )


def redirect(path, **messages):
    location = f"{path}?{urlencode(messages)}" if messages else path
    return RedirectResponse(location, status_code=303)


def text_field(form, field, limit, *, required=False):
    value = form.get(field, "").strip()
    if len(value) > limit or (required and not value):
        raise ValueError(f"Enter a valid {field.replace('_', ' ')}")
    return value


def date_field(form, field):
    value = form.get(field, "").strip()
    if not value:
        return ""
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError
    except ValueError:
        raise ValueError(f"Enter a valid {field.replace('_', ' ')}") from None
    return value


def id_field(form, field, *, required=False):
    value = text_field(form, field, 18, required=required)
    if not value:
        return None
    if not value.isascii() or not value.isdecimal() or int(value) <= 0:
        raise ValueError(f"Select a valid {field.removesuffix('_id').replace('_', ' ')}")
    return int(value)
