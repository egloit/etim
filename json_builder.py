"""
Build the JSON payload that is sent to the target endpoint.

Output structure:
{
  "Materialliste": [
    {
      "matnr-tab": [ { row }, ... ],
      "Eingabe": [
        {
          "Preise": "<none|csv|sap>",
          "Bestand": "false",
          "Sprache_list": [ {"Sprache": "<code aus language_mapping.json>"}, ... ],
          "Verkaeufer": [ {"VKORG": ..., "VTWEG": ..., "SPART": ..., "WERKS": ...} ],
          "Kunde": [ {"KUNNR": "0000311804"} ]
        }
      ]
    }
  ]
}
"""
import json
from datetime import datetime
from pathlib import Path

_LANGUAGE_CONFIG_PATH = Path(__file__).with_name("language_mapping.json")

_ALLOWED_PREISE = {"none", "csv", "sap"}


def _load_language_config() -> dict:
    """Read language_mapping.json fresh on every call so edits take effect
    without an app restart. Falls back to an empty mapping (→ codes pass
    through unchanged) if the file is missing or invalid."""
    try:
        with open(_LANGUAGE_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _map_lang(lang: str, mode: str, config: dict) -> str:
    overrides = config.get("_mode_overrides", {}).get(mode, {})
    if lang in overrides:
        return overrides[lang]
    return config.get(lang, lang)


def build_json(
    rows: list[dict],
    languages: list[str],
    kunnr: str,
    vkorg: str,
    spart: str,
    vtweg: str,
    werks: str,
    email: str = "",
    mode: str = "etim",
    preise: str = "none",
) -> dict:
    kunnr_padded = kunnr.strip().zfill(10)

    matnr_tab = [_build_material_entry(row) for row in rows]

    lang_config = _load_language_config()
    sprache_list = [{"Sprache": _map_lang(lang, mode, lang_config)} for lang in languages]

    preise_value = preise.strip().lower()
    if preise_value not in _ALLOWED_PREISE:
        preise_value = "none"

    return {
        "Materialliste": [
            {
                "matnr-tab": matnr_tab,
                "Eingabe": [
                    {
                        "Preise": preise_value,
                        "Bestand": "false",
                        "Sprache_list": sprache_list,
                        "Verkaeufer": [
                            {
                                "VKORG": vkorg.strip(),
                                "VTWEG": vtweg.strip(),
                                "SPART": spart.strip(),
                                "WERKS": werks.strip(),
                            }
                        ],
                        "Kunde": [{"KUNNR": kunnr_padded}],
                        "Email": email.strip(),
                    }
                ],
            }
        ]
    }


# ---------------------------------------------------------------------------
# Per-row mapping
# ---------------------------------------------------------------------------

def _build_material_entry(row: dict) -> dict:
    return {
        "Materialnummer": row.get("Materialnummer", "").strip(),
        "Preis": _normalize_decimal(row.get("Preis", "")),
        "Preis_ab": _normalize_date(row.get("Preis_ab", "")),
        "MWST_in_": _normalize_mwst(row.get("MWST_in_", "")),
        "Preis_2": _normalize_decimal(row.get("Preis_2", "")),
        "EUR": row.get("EUR", "").strip() or "EUR",
        "Territory": row.get("Territory", "").strip(),
    }


# ---------------------------------------------------------------------------
# Field normalizers
# ---------------------------------------------------------------------------

def _normalize_decimal(value: str) -> str:
    """Normalise decimal separator to comma. Empty → ''."""
    v = value.strip()
    if not v:
        return ""
    # Replace dot with comma (19.13 → 19,13); already comma stays as-is
    return v.replace(".", ",")


def _normalize_date(value: str) -> str:
    """Parse date and output as dd.MM.yyyy. Empty → ''."""
    v = value.strip()
    if not v:
        return ""
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(v, fmt).strftime("%d.%m.%Y")
        except ValueError:
            continue
    return v  # fallback: return as-is (should not happen after validation)


def _normalize_mwst(value: str) -> str:
    """Strip '%', normalise to integer string where possible. Empty → ''."""
    v = value.strip().rstrip("%").strip()
    if not v:
        return ""
    try:
        num = float(v.replace(",", "."))
        # Return as integer if no fractional part, otherwise with comma
        if num == int(num):
            return str(int(num))
        return str(num).replace(".", ",")
    except ValueError:
        return v