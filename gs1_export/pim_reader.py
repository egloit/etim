"""
Reads and normalises values from the Akeneo PIM export JSON stored in
public.pim_egloakeneo_product.json.

Every entry under product["values"][<field_code>] already carries its own
"attribute_type", so get_pim_value() reads that directly instead of guessing
the shape from the data.
"""
import re
from typing import Optional

_MULTISELECT = "pim_catalog_multiselect"
_SIMPLESELECT = "pim_catalog_simpleselect"
_METRIC = "pim_catalog_metric"

_NUMBER_RE = re.compile(r"-?\d+(?:[.,]\d+)?")


def _find_entries(product: dict, field_code: str) -> Optional[list]:
    """Look up product["values"][field_code], falling back to a case-insensitive
    match. public.gs1_mapping.pim_field isn't guaranteed to match the PIM
    attribute code's exact casing (observed e.g. "B2C_Filter_Color" in the
    mapping table vs "B2C_FILTER_COLOR" in the PIM JSON)."""
    values = product.get("values") or {}
    entries = values.get(field_code)
    if entries:
        return entries
    field_lower = field_code.lower()
    for key, val in values.items():
        if key.lower() == field_lower:
            return val
    return None


def get_pim_value(product: dict, field_code: str) -> Optional[dict]:
    """Return a normalised value dict for *field_code*, or None if the product
    has no value for that field.

    Shape:
        {
          "attribute_type": str,
          "translations": {locale_or_None: text, ...},
          "metric": {"amount":..., "unit":..., "symbol":...} | None,
        }

    For non-language-dependent fields (locale is None in the PIM data),
    "translations" has a single key: None.
    """
    entries = _find_entries(product, field_code)
    if not entries:
        return None

    attribute_type = entries[0].get("attribute_type", "")

    if attribute_type == _METRIC:
        data = entries[0].get("data") or {}
        return {
            "attribute_type": attribute_type,
            "translations": {},
            "metric": {
                "amount": data.get("amount"),
                "unit": data.get("unit"),
                "symbol": data.get("symbol"),
            },
        }

    # Language-dependent text: multiple entries, one per locale.
    if len(entries) > 1:
        translations = {e.get("locale"): e.get("data") for e in entries}
        return {"attribute_type": attribute_type, "translations": translations, "metric": None}

    entry = entries[0]

    if attribute_type == _MULTISELECT:
        codes = entry.get("data") or []
        linked = entry.get("linked_data") or {}
        by_locale: dict[str, list[str]] = {}
        for code in codes:
            labels = (linked.get(code) or {}).get("labels", {})
            for locale, text in labels.items():
                by_locale.setdefault(locale, []).append(text)
        translations = {locale: ", ".join(texts) for locale, texts in by_locale.items()}
        return {"attribute_type": attribute_type, "translations": translations, "metric": None}

    if attribute_type == _SIMPLESELECT:
        labels = (entry.get("linked_data") or {}).get("labels", {})
        return {"attribute_type": attribute_type, "translations": dict(labels), "metric": None}

    # Plain scalar: text, number, boolean, identifier, date, reference_entity(_collection), fallback.
    value = entry.get("data")
    if isinstance(value, list):
        value = ", ".join(str(v) for v in value)
    elif value is None:
        value = ""
    else:
        value = str(value)
    return {"attribute_type": attribute_type, "translations": {None: value}, "metric": None}


def resolve_text(value: Optional[dict], pim_locale: str) -> Optional[str]:
    """Pick the text for *pim_locale* out of a get_pim_value() result.
    Falls back to the locale-independent value (key None) if present.
    Returns None if nothing usable is available."""
    if value is None:
        return None

    if value.get("metric"):
        metric = value["metric"]
        amount = metric.get("amount")
        if amount is None:
            return None
        unit = metric.get("symbol") or metric.get("unit") or ""
        return f"{amount} {unit}".strip()

    translations = value.get("translations") or {}
    if pim_locale in translations and translations[pim_locale]:
        return translations[pim_locale]
    if translations.get(None):
        return translations[None]
    return None


def get_pim_raw_codes(product: dict, field_code: str) -> list[str]:
    """Return all raw select codes (e.g. ["B2C_FILTER_FUNCTION_REMOTE", ...]),
    not translated labels, for a select-type PIM field. Used to look up
    public.gs1_brick_mapping and public.gs1_pim_value_crosswalk, which are
    keyed on these raw codes rather than any particular language's label.

    For multiselect fields ALL selected codes are returned (not just the
    first) - some crosswalk rules are "true if ANY of these codes is
    selected", so every code must be checked, not just data[0]. Empty list
    if the field has no value.

    Boolean fields (pim_catalog_boolean) are also crosswalk sources (e.g.
    Pick 4.418 via PIM_remote_control_no) - their raw code is "true"/"false"
    (lowercase), matched against public.gs1_pim_value_crosswalk.pim_code."""
    entries = _find_entries(product, field_code)
    if not entries:
        return []
    data = entries[0].get("data")
    if isinstance(data, bool):
        return ["true" if data else "false"]
    if isinstance(data, str):
        return [data] if data else []
    if isinstance(data, list):
        return [code for code in data if code]
    return []


def get_pim_raw_code(product: dict, field_code: str) -> Optional[str]:
    """Convenience wrapper around get_pim_raw_codes() for callers that only
    care about a single-value (simpleselect/identifier) field."""
    codes = get_pim_raw_codes(product, field_code)
    return codes[0] if codes else None


def get_pim_number_as_int(product: dict, field_code: str) -> Optional[int]:
    """Read a numeric PIM field and return it as an int - used for
    propertyMeasurement/propertyInteger Picks, which expect a bare integer
    text content (e.g. "10"), not a decimal.

    Tolerant of real-world formatting seen in this PIM: comma decimal
    separators ("32,5") and unit-text glued onto the value ("10W" for
    PIM_MAX_POWER_INCL_TRANSFORMER) - extracts the first numeric substring
    rather than requiring the whole field to be a clean number. Truncates
    (doesn't round): "32,5" -> 32."""
    value = get_pim_value(product, field_code)
    if value is None:
        return None
    raw = (value.get("translations") or {}).get(None)
    if not raw:
        return None
    match = _NUMBER_RE.search(raw)
    if not match:
        return None
    try:
        return int(float(match.group().replace(",", ".")))
    except ValueError:
        return None


def get_pim_number_sum_as_int(product: dict, field_code: str) -> Optional[int]:
    """Like get_pim_number_as_int(), but sums every numeric substring found in
    the field instead of taking only the first - for free-text fields that
    combine several components into one value (e.g. PIM_MAX_POWER_INCL_
    TRANSFORMER on ceiling fans: "22W = FAN; 18W = LIGHT" -> 22 + 18 = 40),
    where the Pick semantically wants the total rather than one component."""
    value = get_pim_value(product, field_code)
    if value is None:
        return None
    raw = (value.get("translations") or {}).get(None)
    if not raw:
        return None
    matches = _NUMBER_RE.findall(raw)
    if not matches:
        return None
    try:
        return int(sum(float(m.replace(",", ".")) for m in matches))
    except ValueError:
        return None


def get_zztypen_code(product: dict) -> Optional[str]:
    """Convenience accessor for the ZZTYPEN attribute used to look up the BrickId."""
    return get_pim_raw_code(product, "ZZTYPEN")
