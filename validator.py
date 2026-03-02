"""
Validation logic for form fields and file rows.
Hard errors block the POST; warnings are allowed through.
"""
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class ValidationIssue:
    field: str
    message: str
    row: Optional[int] = None
    is_warning: bool = False

    def to_dict(self) -> dict:
        return {"field": self.field, "message": self.message, "row": self.row}


# ---------------------------------------------------------------------------
# Form (header) validation
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def validate_form_data(
    kunnr: str,
    vkorg: str,
    spart: str,
    vtweg: str,
    werks: str,
    languages: list[str],
    email: str = "",
) -> list[ValidationIssue]:
    errors: list[ValidationIssue] = []

    # Languages
    if not languages:
        errors.append(ValidationIssue("Sprachen", "Mindestens eine Sprache muss ausgewählt sein."))

    # KUNNR
    kunnr_c = kunnr.strip()
    if not kunnr_c:
        errors.append(ValidationIssue("KUNNR", "KUNNR ist ein Pflichtfeld."))
    elif not kunnr_c.isdigit():
        errors.append(ValidationIssue("KUNNR", "KUNNR darf nur Ziffern enthalten."))
    elif len(kunnr_c) > 10:
        errors.append(
            ValidationIssue("KUNNR", f"KUNNR darf maximal 10 Ziffern haben (aktuell: {len(kunnr_c)}).")
        )

    # VKORG
    if len(vkorg.strip()) != 4:
        errors.append(ValidationIssue("VKORG", "VKORG muss genau 4 Zeichen haben."))

    # SPART
    if len(spart.strip()) != 2:
        errors.append(ValidationIssue("SPART", "SPART muss genau 2 Zeichen haben."))

    # VTWEG
    if len(vtweg.strip()) != 2:
        errors.append(ValidationIssue("VTWEG", "VTWEG muss genau 2 Zeichen haben."))

    # WERKS
    if werks.strip() not in ("0090", "0030"):
        errors.append(ValidationIssue("WERKS", "WERKS muss 0090 oder 0030 sein."))

    # E-Mail
    email_c = email.strip()
    if not email_c:
        errors.append(ValidationIssue("E-Mail", "E-Mail ist ein Pflichtfeld."))
    elif not _EMAIL_RE.match(email_c):
        errors.append(ValidationIssue("E-Mail", f"Ungültige E-Mail-Adresse: '{email_c}'."))

    return errors


# ---------------------------------------------------------------------------
# Row (file content) validation
# ---------------------------------------------------------------------------

def validate_rows(rows: list[dict]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    seen: dict[str, int] = {}  # matnr → first row index

    for idx, row in enumerate(rows, start=1):

        # Materialnummer – hard error
        matnr = row.get("Materialnummer", "").strip()
        if not matnr:
            issues.append(
                ValidationIssue("Materialnummer", "Materialnummer ist ein Pflichtfeld.", row=idx)
            )
        else:
            # Duplicate – warning only
            if matnr in seen:
                issues.append(
                    ValidationIssue(
                        "Materialnummer",
                        f"Doppelte Materialnummer '{matnr}' (zuerst in Zeile {seen[matnr]}).",
                        row=idx,
                        is_warning=True,
                    )
                )
            else:
                seen[matnr] = idx

        # Preis – hard error if present but unparseable
        preis = row.get("Preis", "").strip()
        if preis and not _is_valid_decimal(preis):
            issues.append(
                ValidationIssue("Preis", f"Ungültiges Dezimalformat: '{preis}'.", row=idx)
            )

        # Preis_2 – hard error if present but unparseable
        preis2 = row.get("Preis_2", "").strip()
        if preis2 and not _is_valid_decimal(preis2):
            issues.append(
                ValidationIssue("Preis_2", f"Ungültiges Dezimalformat: '{preis2}'.", row=idx)
            )

        # Preis_ab – hard error if present but unparseable
        preis_ab = row.get("Preis_ab", "").strip()
        if preis_ab and _parse_date(preis_ab) is None:
            issues.append(
                ValidationIssue(
                    "Preis_ab",
                    f"Ungültiges Datum: '{preis_ab}' (erwartet: TT.MM.JJJJ).",
                    row=idx,
                )
            )

    return issues


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_valid_decimal(value: str) -> bool:
    try:
        float(value.replace(",", "."))
        return True
    except ValueError:
        return False


def _parse_date(value: str) -> Optional[datetime]:
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue
    return None