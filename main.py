"""
ETIM10 Materiallisten-Upload – FastAPI Application
"""
import json
import logging
import os
from typing import Annotated, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

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

app = FastAPI(title="ETIM10 Materiallisten-Upload", version="1.0.0")
templates = Jinja2Templates(directory="templates")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/validate")
async def validate_endpoint(
    languages: Annotated[Optional[List[str]], Form()] = None,
    kunnr: str = Form(default=""),
    vkorg: str = Form(default=""),
    spart: str = Form(default=""),
    vtweg: str = Form(default=""),
    werks: str = Form(default=""),
    email: str = Form(default=""),
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
        "SUBMIT | file=%s | rows=%d | kunnr=%s | vkorg=%s | spart=%s | vtweg=%s | werks=%s | langs=%s | warnings=%d",
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
        result = await send_payload(payload)
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
        os.getenv("TARGET_URL", "https://edi.eglo.com/dw/Request/ETIM10_Export/v1"),
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