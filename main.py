"""
ETIM10 Materiallisten-Upload – FastAPI Application
"""
import json
import logging
import os
import secrets
from typing import Annotated, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from auth import TENANT_ID, exchange_code, get_auth_url, REDIRECT_URI
from json_builder import build_json
from parser import parse_file
from sender import send_payload
from validator import validate_form_data, validate_rows

load_dotenv()

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_SIZE_MB", "25")) * 1024 * 1024

GROUP_ETIM_ID   = os.getenv("GROUP_ETIM_ID", "")
GROUP_GS1_ID    = os.getenv("GROUP_GS1_ID", "")
TARGET_URL_ETIM = os.getenv("TARGET_URL_ETIM", os.getenv("TARGET_URL", "https://edi.eglo.com/dw/Request/ETIM10_Export/v1"))
TARGET_URL_GS1  = os.getenv("TARGET_URL_GS1", "https://edi.eglo.com/dw/Request/GS1_Export/v1")
_ALLOWED_GROUPS = {g for g in (GROUP_ETIM_ID, GROUP_GS1_ID) if g}

app = FastAPI(title="ETIM10 Materiallisten-Upload", version="1.0.0")
templates = Jinja2Templates(directory="templates")

# ---------------------------------------------------------------------------
# Auth middleware – protects all routes except /auth/*
# ---------------------------------------------------------------------------
_SKIP_AUTH = {"/auth/login", "/auth", "/auth/logout"}


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path in _SKIP_AUTH:
            return await call_next(request)

        user = request.session.get("user")
        if not user:
            if request.method == "GET":
                return RedirectResponse(url="/auth/login")
            return JSONResponse(
                {
                    "success": False,
                    "errors": [
                        {
                            "field": "Auth",
                            "message": "Sitzung abgelaufen. Bitte Seite neu laden.",
                            "row": None,
                        }
                    ],
                    "warnings": [],
                    "row_count": 0,
                },
                status_code=401,
            )

        # Group access check (only enforced when groups are configured)
        if _ALLOWED_GROUPS and not user.get("roles"):
            return HTMLResponse(
                "<h1>Kein Zugriff</h1><p>Ihr Konto ist keiner berechtigten Gruppe zugeordnet. "
                "Bitte wenden Sie sich an den Administrator.</p>",
                status_code=403,
            )

        return await call_next(request)


# SessionMiddleware must be added AFTER AuthMiddleware so it wraps it
# (Starlette processes middleware in LIFO order → last added = outermost = runs first)
app.add_middleware(AuthMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET_KEY", secrets.token_hex(32)),
    https_only=os.getenv("SESSION_HTTPS_ONLY", "true").lower() == "true",
    same_site="lax",
    max_age=8 * 3600,  # 8 h session lifetime
)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/auth/login")
async def auth_login(request: Request):
    state = secrets.token_urlsafe(16)
    request.session["oauth_state"] = state
    return RedirectResponse(url=get_auth_url(state))


@app.get("/auth")
async def auth_callback(
    request: Request,
    code: str = None,
    state: str = None,
    error: str = None,
    error_description: str = None,
):
    if error:
        return HTMLResponse(
            f"<h1>Login fehlgeschlagen</h1><p>{error_description or error}</p>",
            status_code=400,
        )

    if not code or state != request.session.pop("oauth_state", None):
        return HTMLResponse("<h1>Ungültiger Login-Versuch</h1>", status_code=400)

    result = exchange_code(code)
    if "error" in result:
        return HTMLResponse(
            f"<h1>Token-Fehler</h1><p>{result.get('error_description', result['error'])}</p>",
            status_code=400,
        )

    claims = result.get("id_token_claims", {})
    logger.info("DEBUG claims keys: %s", list(claims.keys()))
    logger.info("DEBUG token_groups: %s", claims.get("groups", []))
    token_groups = claims.get("groups", [])
    roles = []
    if GROUP_ETIM_ID and GROUP_ETIM_ID in token_groups:
        roles.append("etim")
    if GROUP_GS1_ID and GROUP_GS1_ID in token_groups:
        roles.append("gs1")

    request.session["user"] = {
        "name": claims.get("name", ""),
        "email": claims.get("preferred_username", claims.get("upn", "")),
        "roles": roles,
    }
    return RedirectResponse(url="/")


@app.get("/auth/logout")
async def auth_logout(request: Request):
    request.session.clear()
    base_url = REDIRECT_URI.rsplit("/auth", 1)[0]
    logout_url = (
        f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/logout"
        f"?post_logout_redirect_uri={base_url}/auth/login"
    )
    return RedirectResponse(url=logout_url)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    user = request.session.get("user", {})
    return templates.TemplateResponse(
        "index.html", {
            "request": request,
            "user": user,
            "roles": user.get("roles", []),
        }
    )


def _get_target_url(mode: str) -> str:
    return TARGET_URL_GS1 if mode == "gs1" else TARGET_URL_ETIM

@app.post("/validate")
async def validate_endpoint(
    languages: Annotated[Optional[List[str]], Form()] = None,
    kunnr: str = Form(default=""),
    vkorg: str = Form(default=""),
    spart: str = Form(default=""),
    vtweg: str = Form(default=""),
    werks: str = Form(default=""),
    email: str = Form(default=""),
    mode: str = Form(default="etim"),
    file: UploadFile = File(...),
):
    lang_list = languages or []
    content = await file.read()

    if len(content) > MAX_UPLOAD_BYTES:
        return JSONResponse(
            {
                "success": False,
                "errors": [
                    {
                        "field": "Datei",
                        "message": (
                            f"Datei zu groß ({len(content) // 1024 // 1024} MB). "
                            f"Maximum: {MAX_UPLOAD_BYTES // 1024 // 1024} MB."
                        ),
                        "row": None,
                    }
                ],
                "warnings": [],
                "row_count": 0,
                "preview": [],
                "kunnr_padded": "",
            }
        )

    # Validate form fields
    form_issues = validate_form_data(kunnr, vkorg, spart, vtweg, werks, lang_list, email)

    # Parse file
    try:
        rows = parse_file(content, file.filename or "")
    except Exception as exc:
        return JSONResponse(
            {
                "success": False,
                "errors": [{"field": "Datei", "message": str(exc), "row": None}],
                "warnings": [],
                "row_count": 0,
                "preview": [],
                "kunnr_padded": "",
            }
        )

    # Validate rows
    row_issues = validate_rows(rows)

    hard_errors = [i for i in form_issues + row_issues if not i.is_warning]
    warnings = [i for i in row_issues if i.is_warning]

    kunnr_padded = kunnr.strip().zfill(10) if kunnr.strip().isdigit() else kunnr.strip()

    return JSONResponse(
        {
            "success": len(hard_errors) == 0,
            "errors": [e.to_dict() for e in hard_errors],
            "warnings": [w.to_dict() for w in warnings],
            "row_count": len(rows),
            "preview": rows[:5],
            "kunnr_padded": kunnr_padded,
        }
    )


@app.post("/json-preview")
async def json_preview_endpoint(
    languages: Annotated[Optional[List[str]], Form()] = None,
    kunnr: str = Form(default=""),
    vkorg: str = Form(default=""),
    spart: str = Form(default=""),
    vtweg: str = Form(default=""),
    werks: str = Form(default=""),
    email: str = Form(default=""),
    mode: str = Form(default="etim"),
    file: UploadFile = File(...),
):
    """Validate + build JSON payload – without sending it."""
    lang_list = languages or []
    content = await file.read()

    if len(content) > MAX_UPLOAD_BYTES:
        return JSONResponse(
            {"success": False, "errors": [{"field": "Datei", "message": "Datei zu groß.", "row": None}], "warnings": [], "payload": None}
        )

    form_issues = validate_form_data(kunnr, vkorg, spart, vtweg, werks, lang_list, email)

    try:
        rows = parse_file(content, file.filename or "")
    except Exception as exc:
        return JSONResponse(
            {"success": False, "errors": [{"field": "Datei", "message": str(exc), "row": None}], "warnings": [], "payload": None}
        )

    row_issues = validate_rows(rows)
    hard_errors = [i for i in form_issues + row_issues if not i.is_warning]
    warnings    = [i for i in row_issues if i.is_warning]

    if hard_errors:
        return JSONResponse(
            {
                "success": False,
                "errors":   [e.to_dict() for e in hard_errors],
                "warnings": [w.to_dict() for w in warnings],
                "payload":  None,
            }
        )

    payload = build_json(rows, lang_list, kunnr, vkorg, spart, vtweg, werks, email)
    return JSONResponse(
        {
            "success":   True,
            "errors":    [],
            "warnings":  [w.to_dict() for w in warnings],
            "row_count": len(rows),
            "payload":   json.dumps(payload, ensure_ascii=False, indent=2),
        }
    )


@app.post("/submit")
async def submit_endpoint(
    languages: Annotated[Optional[List[str]], Form()] = None,
    kunnr: str = Form(default=""),
    vkorg: str = Form(default=""),
    spart: str = Form(default=""),
    vtweg: str = Form(default=""),
    werks: str = Form(default=""),
    email: str = Form(default=""),
    mode: str = Form(default="etim"),
    file: UploadFile = File(...),
):
    lang_list = languages or []
    content = await file.read()
    target_url = _get_target_url(mode)

    if len(content) > MAX_UPLOAD_BYTES:
        return JSONResponse(
            {
                "success": False,
                "errors": [
                    {
                        "field": "Datei",
                        "message": f"Datei zu groß. Maximum: {MAX_UPLOAD_BYTES // 1024 // 1024} MB.",
                        "row": None,
                    }
                ],
                "warnings": [],
                "row_count": 0,
            }
        )

    # Validate form fields
    form_issues = validate_form_data(kunnr, vkorg, spart, vtweg, werks, lang_list, email)

    # Parse file
    try:
        rows = parse_file(content, file.filename or "")
    except Exception as exc:
        return JSONResponse(
            {
                "success": False,
                "errors": [{"field": "Datei", "message": str(exc), "row": None}],
                "warnings": [],
                "row_count": 0,
            }
        )

    # Validate rows
    row_issues = validate_rows(rows)
    hard_errors = [i for i in form_issues + row_issues if not i.is_warning]
    warnings = [i for i in row_issues if i.is_warning]

    if hard_errors:
        logger.warning(
            "SUBMIT ABORTED | file=%s | rows=%d | hard_errors=%d",
            file.filename,
            len(rows),
            len(hard_errors),
        )
        return JSONResponse(
            {
                "success": False,
                "errors": [e.to_dict() for e in hard_errors],
                "warnings": [w.to_dict() for w in warnings],
                "row_count": len(rows),
            }
        )

    # Build JSON payload
    payload = build_json(rows, lang_list, kunnr, vkorg, spart, vtweg, werks, email)

    logger.info(
        "SUBMIT | mode=%s | file=%s | rows=%d | kunnr=%s | vkorg=%s | spart=%s | vtweg=%s | werks=%s | langs=%s | warnings=%d",
        mode,
        file.filename,
        len(rows),
        kunnr.strip().zfill(10),
        vkorg.strip(),
        spart.strip(),
        vtweg.strip(),
        werks.strip(),
        ",".join(lang_list),
        len(warnings),
    )

    # Send
    try:
        result = await send_payload(payload, target_url=target_url)
    except Exception as exc:
        logger.error("SEND ERROR | %s", exc)
        return JSONResponse(
            {
                "success": False,
                "errors": [{"field": "HTTP", "message": str(exc), "row": None}],
                "warnings": [w.to_dict() for w in warnings],
                "row_count": len(rows),
            }
        )

    logger.info(
        "RESPONSE | status=%d | success=%s | url=%s",
        result["status_code"],
        result["success"],
        target_url,
    )

    # Include a truncated payload preview (first 5 matnr-tab entries)
    preview_payload = {
        "Materialliste": [
            {
                "matnr-tab": payload["Materialliste"][0]["matnr-tab"][:5],
                "Eingabe": payload["Materialliste"][0]["Eingabe"],
            }
        ]
    }

    return JSONResponse(
        {
            "success": result["success"],
            "errors": [],
            "warnings": [w.to_dict() for w in warnings],
            "row_count": len(rows),
            "http_status": result["status_code"],
            "http_success": result["success"],
            "response_body": result["body"],
            "payload_preview": json.dumps(preview_payload, ensure_ascii=False, indent=2),
        }
    )