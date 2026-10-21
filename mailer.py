"""
Send a generated export file by e-mail via the internal SMTP relay
(no login, no TLS). Settings are read from environment variables; with
SMTP_HOST empty, no mail is sent.
"""
import io
import os
import smtplib
import zipfile
from datetime import datetime
from email.message import EmailMessage
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

SMTP_HOST: str = os.getenv("SMTP_HOST", "")
SMTP_PORT: int = int(os.getenv("SMTP_PORT", "25"))
SMTP_TIMEOUT: float = float(os.getenv("SMTP_TIMEOUT", "10"))
MAIL_FROM: str = os.getenv("MAIL_FROM", "etim-export@eglo.com")
# Larger exports are not attached - the mail then points to "Meine Exporte".
MAIL_MAX_ATTACHMENT_BYTES: int = int(float(os.getenv("MAIL_MAX_ATTACHMENT_MB", "10")) * 1024 * 1024)
_MAX_BODY_WARNINGS = 30

_XLSX = ("application", "vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def mail_enabled() -> bool:
    return bool(SMTP_HOST)


def zip_file(filename: str, content: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(filename, content)
    return buf.getvalue()


def build_export_mail(to: str, filename: str, xml_bytes: bytes, article_count: int,
                      warnings: list[str], validation_error_count: int,
                      issues_xlsx: Optional[bytes] = None) -> EmailMessage:
    """ETIM10 export mail: BMEcat as ZIP (if not too large), problem list as Excel."""
    zipped = zip_file(filename, xml_bytes)
    attach = len(zipped) <= MAIL_MAX_ATTACHMENT_BYTES
    stem = filename.rsplit(".", 1)[0]

    lines = [
        "Hallo,",
        "",
        f"der ETIM10-Export {filename} wurde erstellt.",
        "",
        f"Artikel: {article_count}",
        f"Warnungen: {len(warnings)}",
        f"XSD-Fehler: {validation_error_count}",
        "",
    ]
    if attach:
        lines.append(f"Die BMEcat-Datei ist als {stem}.zip angehängt.")
    else:
        lines.append(f"Die BMEcat-Datei ist zu groß für den Mailversand ({len(zipped) / 1024 / 1024:.1f} MB "
                     "gezippt). Sie steht in der Anwendung unter \"Meine Exporte\" zum Download bereit.")
    if issues_xlsx:
        lines.append(f"Die Fehlerliste pro Artikel ist als {stem}_fehlerliste.xlsx angehängt.")
    if warnings:
        lines += ["", "Warnungen:"]
        lines += [f"- {w}" for w in warnings[:_MAX_BODY_WARNINGS]]
        if len(warnings) > _MAX_BODY_WARNINGS:
            lines.append(f"- ... und {len(warnings) - _MAX_BODY_WARNINGS} weitere (siehe Fehlerliste)")
    lines += ["", f"Erstellt: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
              "Diese Nachricht wurde automatisch versendet."]

    msg = EmailMessage()
    msg["From"] = MAIL_FROM
    msg["To"] = to
    msg["Subject"] = f"ETIM10-Export {filename} ({article_count} Artikel)"
    msg.set_content("\n".join(lines))
    if attach:
        msg.add_attachment(zipped, maintype="application", subtype="zip", filename=f"{stem}.zip")
    if issues_xlsx:
        msg.add_attachment(issues_xlsx, maintype=_XLSX[0], subtype=_XLSX[1],
                           filename=f"{stem}_fehlerliste.xlsx")
    return msg


def send_mail(msg: EmailMessage) -> None:
    """Raises Exception with a German error message if sending fails."""
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT) as smtp:
            smtp.send_message(msg)
    except (OSError, smtplib.SMTPException) as exc:
        raise Exception(f"E-Mail an {msg['To']} konnte nicht gesendet werden ({SMTP_HOST}:{SMTP_PORT}): {exc}")
