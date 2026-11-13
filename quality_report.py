"""
Data quality check for the technical service: for a few articles, every ETIM
feature of the article's class and every GS1 attribute (Pick) of its Brick -
also the ones we have no data for - with description, the value the export
would write, the PIM field it comes from and the raw PIM value. Output is an
Excel file the technical service forwards to data management, who correct the
PIM (nothing is written back by this app).

The values come from the same code as the exports (feature_engine.compute_raw/
compute_features for ETIM, gs1_export.pipeline.resolve_pick for GS1), so the
report shows exactly what would be exported.
"""
import io
import logging
import re
from datetime import datetime
from typing import Optional

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from etim10_export import sources as etim_sources
from etim10_export.feature_engine import EMPTY_VALUES, RuleSet, compute_features, compute_raw
from gs1_export import database as gs1_database
from gs1_export import gs1_mapping, pim_reader
from gs1_export import pipeline as gs1_pipeline

logger = logging.getLogger(__name__)

MAX_ARTICLES = 20

# VKORG -> target market for the GS1 check: the languages decide which texts
# a propertyDescription Pick shows (same languages as the GS1 exports for
# that market, see language_mapping.json / Benelux rule 500.655).
TARGET_MARKETS = {
    "0012": {"name": "Belgium", "languages": ["nl", "fr", "ger", "eng"]},
    "0025": {"name": "Netherlands", "languages": ["nl", "eng"]},
}

# Status values (column "Status") and their colours.
ST_OK = "filled"
ST_DEFAULT = "default value"
ST_EMPTY = "PIM empty"
ST_MISSING = "missing (mandatory)"
ST_DROPPED = "dropped (invalid)"
ST_NO_MAPPING = "no mapping"
ST_NOT_EXPORTED = "not exported"

STATUS_INFO = [
    (ST_OK, "C6EFCE", "Value comes from the PIM (or a maintained fallback value) and is exported."),
    (ST_DEFAULT, "FFEB9C", "Exported value is a default from the mapping configuration (crosswalk '*'), "
                           "not a real PIM value - check whether it is correct for this article."),
    (ST_EMPTY, "FCD5B4", "A mapping exists, but the PIM field is empty - nothing is exported."),
    (ST_MISSING, "FFC7CE", "GS1 mandatory attribute without value - please fill in the PIM."),
    (ST_DROPPED, "FFC7CE", "The PIM has a value, but it is not valid for this class/attribute - "
                           "nothing is exported. Check the PIM value."),
    (ST_NO_MAPPING, "EDEDED", "No mapping from the PIM exists for this feature/attribute."),
    (ST_NOT_EXPORTED, "EDEDED", "Attribute type is not (yet) supported by the export."),
]
_FILLS = {status: PatternFill("solid", start_color=color, end_color=color) for status, color, _ in STATUS_INFO}
_HEADER_FILL = PatternFill("solid", start_color="1CA1A9", end_color="1CA1A9")
_LABEL_LOCALES = ("en_GB", "en_US", "de_DE", "de_AT")
_COMMENT_HEADER = "Comment technical service"
_CHECK_HEADER = "Correct?"


class QualityCheckError(Exception):
    """Hard failure (e.g. database unreachable)."""


def parse_matnrs(text: str) -> list[str]:
    """'32048, 89534;900092' -> ['32048', '89534', '900092'] (comma, semicolon,
    whitespace or line breaks as separator, duplicates removed)."""
    return list(dict.fromkeys(m for m in re.split(r"[,;\s]+", text or "") if m))


# ---------- helpers ----------

def _label(labels: dict, code) -> str:
    text = next((labels[l] for l in _LABEL_LOCALES if labels.get(l)), "")
    return f"{code} ({text})" if text and text != code else str(code)


def pim_display(product: Optional[dict], field: Optional[str], show_locale: bool = True) -> str:
    """Raw PIM value of *field* for humans: select codes with their English
    label, numbers/metrics with unit, texts (English first)."""
    if not product or not field:
        return ""
    entries = pim_reader._find_entries(product, field)
    if not entries:
        return ""
    entry = entries[0]
    data = entry.get("data")
    attribute_type = entry.get("attribute_type", "")
    linked = entry.get("linked_data") or {}
    if attribute_type == "pim_catalog_simpleselect":
        return _label(linked.get("labels") or {}, data) if data else ""
    if attribute_type == "pim_catalog_multiselect":
        return ", ".join(_label((linked.get(c) or {}).get("labels") or {}, c) for c in data or [])
    if attribute_type == "pim_catalog_metric" and isinstance(data, dict):
        amount = data.get("amount")
        return f"{amount} {data.get('symbol') or data.get('unit') or ''}".strip() if amount not in (None, "") else ""
    if len(entries) > 1:  # localised text
        texts = {e.get("locale"): e.get("data") for e in entries if e.get("data")}
        locale = next((l for l in _LABEL_LOCALES if texts.get(l)), next(iter(texts), None))
        if not locale:
            return ""
        return f"{texts[locale]} [{locale}]" if show_locale else texts[locale]
    if isinstance(data, bool):
        return "true" if data else "false"
    if isinstance(data, list):
        return ", ".join(str(v) for v in data)
    return "" if data is None else str(data)


def _pick_sort_key(pick_id: str) -> tuple:
    return tuple(int(p) if p.isdigit() else 0 for p in pick_id.split("."))


# ---------- ETIM ----------

def _etim_feature_rows(matnr: str, product: dict, class_id: str, rules: RuleSet, model: list[dict],
                       fallback: Optional[dict]) -> tuple[list[dict], list[str]]:
    raw = compute_raw(product, class_id, rules, fallback)
    features, warnings = compute_features(product, class_id, rules, matnr, fallback)
    exported = {f["feature_id"]: f for f in features}
    fields_by_feature: dict[str, list[str]] = {}
    for rule in rules.rules_by_class.get(class_id, []):
        if rule["pim_field"] and rule["pim_field"] not in fields_by_feature.setdefault(rule["feature_id"], []):
            fields_by_feature[rule["feature_id"]].append(rule["pim_field"])
    has_rule = {r["feature_id"] for r in rules.rules_by_class.get(class_id, [])}

    def row(feature: dict, in_class: bool = True) -> dict:
        fid = feature["feature_id"]
        slots = raw.get(fid, {})
        rule = slots.get("value_rule")
        value = (slots.get("value") or "").strip()
        value2 = (slots.get("value2") or "").strip()
        exp = exported.get(fid)
        from_config = bool(slots.get("value_from_config"))

        if slots.get("source"):
            source = f"fallback value ({slots['source']})"
        elif rule is None:
            source = ""
        elif rule["source_type"] == "fix":
            source = "fixed value (rule)"
        elif rule["source_type"] == "copy":
            source = "PIM" + (f" ({rule['transform']})" if rule["transform"] else "")
        else:
            source = f"crosswalk default (*) [{rule['crosswalk_set']}]" if from_config \
                else f"PIM via crosswalk [{rule['crosswalk_set']}]"

        note = ""
        ftype = feature.get("type", "")
        if exp:
            status = ST_DEFAULT if rule and rule["source_type"] == "crosswalk" and from_config \
                and not slots.get("source") else ST_OK
            if status == ST_DEFAULT:
                note = "PIM field empty" if slots.get("pim_empty") else "PIM code not in crosswalk"
        elif value not in EMPTY_VALUES:
            # Same checks as compute_features(), worded in English for the report.
            status = ST_DROPPED
            if not in_class:
                note = f"Feature is not part of ETIM class {class_id}"
            elif ftype == "A":
                note = f"Value '{value}' is not allowed for {fid} in {class_id}" + \
                       (" (value from the mapping configuration)" if from_config else "")
            elif ftype == "L":
                note = f"'{value}' is not a logical value (true/false)"
            else:
                note = f"'{value}' is not a number"
        elif fid in has_rule:
            status = ST_EMPTY
        else:
            status = ST_NO_MAPPING

        unit = feature.get("unit_desc") or feature.get("unit_id") or ""
        if exp:
            out_values = exp["values"]
        else:
            out_values = [v for v in (value, value2) if v not in EMPTY_VALUES] if status == ST_DROPPED else []
        if ftype == "A":
            value_desc = ", ".join(rules.value_names.get(v, "") for v in out_values)
        elif ftype == "L":
            value_desc = ", ".join({"true": "Yes", "false": "No"}.get(v.lower(), v) for v in out_values)
        else:
            value_desc = " - ".join(out_values) + (f" {unit}" if out_values and unit else "")

        pim_field = rule["pim_field"] if rule and rule["pim_field"] else ", ".join(fields_by_feature.get(fid, []))
        pim_value = pim_display(product, rule["pim_field"]) if rule and rule["pim_field"] else ""
        if rule and rule["required_pim_field"] and status == ST_EMPTY:
            note = note or f"Rule only applies when {rule['required_pim_field']} is filled"
        return {
            "feature_id": fid, "feature": rules.feature_names.get(fid, feature.get("desc", "")),
            "type": {"A": "alphanumeric", "L": "logical", "N": "numeric", "R": "range"}.get(ftype, ftype),
            "unit": unit if ftype in ("N", "R") else "",
            "value": " - ".join(out_values), "value_desc": value_desc,
            "pim_field": pim_field, "pim_value": pim_value, "source": source, "status": status, "note": note,
        }

    rows = [row(f) for f in model]
    model_ids = {f["feature_id"] for f in model}
    for fid, slots in sorted(raw.items()):
        if fid not in model_ids and (slots.get("value") or "").strip() not in EMPTY_VALUES \
                and not slots.get("value_from_config") and not slots.get("source"):
            rows.append(row({"feature_id": fid, "desc": rules.feature_names.get(fid, "")}, in_class=False))
    return rows, warnings


def _load_etim_model(conn, class_ids: list[str]) -> tuple[dict[str, list[dict]], dict[str, str]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT cf.class_id, cf.feature_id, cf.feature_type, cf.unit_id, u.description, f.description
            FROM public.etim10_model_class_feature cf
            LEFT JOIN public.etim10_model_unit u ON u.unit_id = cf.unit_id
            LEFT JOIN public.etim10_model_feature f ON f.feature_id = cf.feature_id
            WHERE cf.class_id = ANY(%s)
            ORDER BY cf.class_id, cf.sort_nr NULLS LAST, cf.feature_id
            """,
            (class_ids,),
        )
        model: dict[str, list[dict]] = {}
        for class_id, fid, ftype, unit_id, unit_desc, desc in cur.fetchall():
            model.setdefault(class_id, []).append(
                {"feature_id": fid, "type": ftype, "unit_id": unit_id, "unit_desc": unit_desc, "desc": desc})
        cur.execute("SELECT class_id, description FROM public.etim10_model_class WHERE class_id = ANY(%s)",
                    (class_ids,))
        return model, dict(cur.fetchall())


def check_etim(conn, matnrs: list[str]) -> dict:
    """{'overview': [...], 'rows': [...], 'allowed': [...]} for the ETIM sheets."""
    rules = RuleSet.load(conn)
    products = etim_sources.get_products(conn, matnrs, rules.pim_fields() | {"MAKTX"})
    fallback = etim_sources.get_fallback_values(conn, matnrs)
    class_by_matnr = {m: rules.class_by_zztyp.get(pim_reader.get_zztypen_code(p) or "")
                      for m, p in products.items() if p}
    class_ids = sorted({c for c in class_by_matnr.values() if c})
    model, class_names = _load_etim_model(conn, class_ids)

    overview, rows = [], []
    for matnr in matnrs:
        product = products.get(matnr)
        zztyp = pim_reader.get_zztypen_code(product) if product else None
        base = {"matnr": matnr, "name": pim_display(product, "MAKTX", show_locale=False), "system": "ETIM",
                "zztyp": pim_display(product, "ZZTYPEN"), "class": "", "class_desc": ""}
        if not product:
            overview.append({**base, "remark": "Article not found in the PIM (or not enabled)."})
            continue
        class_id = class_by_matnr.get(matnr)
        if not class_id:
            overview.append({**base, "remark": f"No ETIM class mapped for ZZTYPEN '{zztyp or ''}' "
                                               "(etim10_class_mapping)."})
            continue
        feature_rows, _ = _etim_feature_rows(matnr, product, class_id, rules, model.get(class_id, []),
                                             fallback.get(matnr))
        base.update({"class": class_id, "class_desc": class_names.get(class_id, "")})
        for r in feature_rows:
            rows.append({**base, **r})
        overview.append({**base, **_counts(feature_rows, len(model.get(class_id, []))), "remark": ""})

    allowed = []
    for class_id in class_ids:
        for feature in model.get(class_id, []):
            if feature["type"] != "A":
                continue
            for value_id in sorted(rules.allowed_values.get((class_id, feature["feature_id"]), ())):
                allowed.append([class_id, class_names.get(class_id, ""), feature["feature_id"],
                                rules.feature_names.get(feature["feature_id"], feature["desc"] or ""),
                                value_id, rules.value_names.get(value_id, "")])
    return {"overview": overview, "rows": rows, "allowed": allowed}


# ---------- GS1 ----------

def _gs1_reference(conn, brick_id: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT pick_id, attribute_name, mandatory, gdsn_attribute_name, brick_name
            FROM public.gs1_gpc_attribute_types WHERE brick_id = %s AND active
            """,
            (brick_id,),
        )
        columns = [d.name for d in cur.description]
        return sorted((dict(zip(columns, r)) for r in cur.fetchall()), key=lambda a: _pick_sort_key(a["pick_id"]))


def _picklist(conn, pick_ids: list[str]) -> dict[str, list[tuple[str, str]]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT pick_id, code_value, label_en FROM public.gs1_gpc_picklist_values
            WHERE pick_id = ANY(%s) AND active ORDER BY pick_id, code_value
            """,
            (pick_ids,),
        )
        out: dict[str, list[tuple[str, str]]] = {}
        for pick, code, label in cur.fetchall():
            out.setdefault(pick, []).append((code, label or ""))
        return out


def _manual_source(conn, matnr: str, pick_id: str) -> str:
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT source FROM public.gs1_manual_property_values WHERE matnr = %s AND pick_id = %s AND active "
                "LIMIT 1",
                (matnr, pick_id),
            )
            row = cur.fetchone()
            return row[0] if row and row[0] else ""
    except Exception:  # column/table missing - only cosmetic
        return ""


def _gs1_value_display(value, unit_code: Optional[str], labels: dict[str, str]) -> tuple[str, str]:
    """(value, value description) for the sheet."""
    if value is None or value == "" or value == {}:
        return "", ""
    if isinstance(value, bool):
        return ("true" if value else "false"), ("Yes" if value else "No")
    if isinstance(value, dict):
        return "; ".join(f"{lang}: {text}" for lang, text in value.items()), ""
    if isinstance(value, int):
        return str(value), f"{value} {unit_code or ''}".strip()
    return str(value), labels.get(str(value), "")


def _gs1_attribute_rows(conn, matnr: str, product: dict, brick_id: str, reference: list[dict],
                        mappings: list[dict], picklist: dict, languages: list) -> list[dict]:
    mapped: dict[str, list[str]] = {}
    for m in mappings:
        mapped.setdefault(m["pickid"], []).append(m["pimfeld"])
    by_pick = {a["pick_id"]: a for a in reference}
    # Mapped Picks missing from the reference data are still exported (as description) - show them too.
    picks = [a["pick_id"] for a in reference] + sorted((p for p in mapped if p not in by_pick), key=_pick_sort_key)

    rows = []
    for pick in picks:
        attr = by_pick.get(pick, {"pick_id": pick, "attribute_name": "", "mandatory": False,
                                  "gdsn_attribute_name": None})
        labels = dict(picklist.get(pick, []))
        mandatory = bool(attr["mandatory"])
        gdsn_type = attr["gdsn_attribute_name"]
        base = {"pick_id": pick, "attribute": attr["attribute_name"] or "", "mandatory": "yes" if mandatory else "",
                "gdsn_type": gdsn_type or "", "unit": "", "value": "", "value_desc": "", "pim_field": "",
                "pim_value": "", "source": "", "status": ST_NO_MAPPING, "note": ""}

        if pick not in mapped:
            # Values the export writes without a gs1_mapping row.
            if gdsn_type == "declaredPower":
                amount = pim_reader.get_pim_number_as_int(product, "PIM_FAS_05_1")
                base.update(pim_field="PIM_FAS_05_1", pim_value=pim_display(product, "PIM_FAS_05_1"),
                            source="PIM" if amount is not None else "", unit="WTT",
                            value="" if amount is None else str(amount),
                            value_desc="" if amount is None else f"{amount} WTT",
                            status=ST_OK if amount is not None else (ST_MISSING if mandatory else ST_EMPTY))
            elif gdsn_type == "lightBeamTypeCode":
                code = gs1_mapping.get_manual_property_value(conn, matnr, pick)
                base.update(value=code or "", value_desc=labels.get(code or "", ""),
                            source=f"manual value ({_manual_source(conn, matnr, pick)})" if code else "",
                            status=ST_OK if code else (ST_MISSING if mandatory else ST_EMPTY),
                            note="Only kept in gs1_manual_property_values (EPREL)")
            elif mandatory:
                base["status"] = ST_MISSING
                base["note"] = "Mandatory attribute without mapping"
            rows.append(base)
            continue

        for pimfeld in mapped[pick]:
            pv = gs1_pipeline.resolve_pick(conn, matnr, product, brick_id, pick, pimfeld, languages)
            value, value_desc = _gs1_value_display(pv.value, pv.unit_code, labels)
            source = pv.source
            if source == gs1_pipeline.SOURCE_MANUAL:
                source = f"manual value ({_manual_source(conn, matnr, pick) or 'gs1_manual_property_values'})"
            row = {**base, "gdsn_type": pv.gdsn_type or gdsn_type or "", "unit": pv.unit_code or "",
                   "value": value, "value_desc": value_desc, "pim_field": pimfeld,
                   "pim_value": pim_display(product, pimfeld), "source": source}
            if pv.skip_reason:
                row.update(status=ST_NOT_EXPORTED, note=pv.skip_reason)
            elif value:
                row["status"] = ST_DEFAULT if pv.source == gs1_pipeline.SOURCE_CROSSWALK_DEFAULT else ST_OK
                if row["status"] == ST_DEFAULT:
                    row["note"] = "PIM code not in crosswalk" if pv.pim_codes else "PIM field empty"
                if pv.gdsn_type == "visibleLight":
                    row["note"] = "Only exported together with light beam type (8.779)"
                elif pv.gdsn_type == "areBatteriesIncluded" and pv.value is True:
                    row["note"] = "Currently only 'false' is exported (battery details missing)"
            else:
                row["status"] = ST_MISSING if mandatory else ST_EMPTY
                if pv.pim_codes and pv.gdsn_type in ("propertyCode", "propertyMeasurement"):
                    row["note"] = f"PIM code(s) {', '.join(pv.pim_codes)} not in crosswalk (gs1_pim_value_crosswalk)"
                if not pv.gdsn_type:
                    row["note"] = "Pick not in gs1_gpc_attribute_types - exported as description"
            rows.append(row)
    return rows


def check_gs1(conn, matnrs: list[str], vkorg: str) -> dict:
    market = TARGET_MARKETS.get(vkorg)
    if market is None:
        raise QualityCheckError(f"Unknown target market (VKORG) '{vkorg}'.")
    language_config = gs1_pipeline._load_json_config(gs1_pipeline._LANGUAGE_CONFIG_PATH)
    languages = [(code, language_config[code]) for code in market["languages"] if code in language_config]

    overview, rows, bricks = [], [], {}
    for matnr in matnrs:
        product = gs1_database.get_product_by_matnr(conn, matnr)
        zztyp = pim_reader.get_zztypen_code(product) if product else None
        base = {"matnr": matnr, "name": pim_display(product, "MAKTX", show_locale=False), "system": f"GS1 ({market['name']})",
                "zztyp": pim_display(product, "ZZTYPEN"), "class": "", "class_desc": ""}
        if not product:
            overview.append({**base, "remark": "Article not found in the PIM (or not enabled)."})
            continue
        brick_id = gs1_mapping.get_brick_id(conn, zztyp) if zztyp else None
        if not brick_id:
            overview.append({**base, "remark": f"No GS1 Brick mapped for ZZTYPEN '{zztyp or ''}' "
                                               "(gs1_brick_mapping)."})
            continue
        if brick_id not in bricks:
            reference = _gs1_reference(conn, brick_id)
            mappings = gs1_mapping.get_active_mappings(conn, brick_id)
            pick_ids = sorted({a["pick_id"] for a in reference} | {m["pickid"] for m in mappings})
            bricks[brick_id] = (reference, mappings, _picklist(conn, pick_ids))
        reference, mappings, picklist = bricks[brick_id]
        attr_rows = _gs1_attribute_rows(conn, matnr, product, brick_id, reference, mappings, picklist, languages)
        base.update({"class": brick_id, "class_desc": next((a["brick_name"] for a in reference if a["brick_name"]), "")})
        for r in attr_rows:
            rows.append({**base, **r})
        total = len({r["pick_id"] for r in attr_rows})
        overview.append({**base, **_counts(attr_rows, total), "remark": ""})

    allowed = []
    for brick_id, (reference, _mappings, picklist) in sorted(bricks.items()):
        for attr in reference:
            if attr["gdsn_attribute_name"] != "propertyCode":
                continue
            for code, label in picklist.get(attr["pick_id"], []):
                allowed.append([brick_id, attr["pick_id"], attr["attribute_name"], code, label])
    return {"overview": overview, "rows": rows, "allowed": allowed}


def _counts(rows: list[dict], total: int) -> dict:
    statuses = [r["status"] for r in rows]
    return {"total": total, "filled": statuses.count(ST_OK) + statuses.count(ST_DEFAULT),
            "default": statuses.count(ST_DEFAULT), "empty": statuses.count(ST_EMPTY),
            "missing": statuses.count(ST_MISSING), "dropped": statuses.count(ST_DROPPED),
            "no_mapping": statuses.count(ST_NO_MAPPING)}


# ---------- Excel ----------

def _write_sheet(ws, header: list[str], rows: list[list], widths: list[int], status_col: Optional[int] = None,
                 wrap_cols: tuple = (), check_col: Optional[int] = None) -> None:
    ws.append(header)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = _HEADER_FILL
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    for row in rows:
        ws.append(row)
    for i, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = width
    if status_col is not None:
        for (cell,) in ws.iter_rows(min_row=2, min_col=status_col, max_col=status_col):
            if cell.value in _FILLS:
                cell.fill = _FILLS[cell.value]
    for col in wrap_cols:
        for (cell,) in ws.iter_rows(min_row=2, min_col=col, max_col=col):
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    if check_col is not None and rows:
        letter = get_column_letter(check_col)
        dv = DataValidation(type="list", formula1='"OK,Not OK"', allow_blank=True)
        ws.add_data_validation(dv)
        dv.add(f"{letter}2:{letter}{len(rows) + 1}")
    ws.freeze_panes = "B2" if status_col else "A2"
    if rows:
        ws.auto_filter.ref = ws.dimensions


_DETAIL_HEADER_ETIM = ["Material", "Description", "ETIM class", "Class description", "Feature ID",
                       "Feature description", "Type", "Unit", "Exported value", "Value description", "PIM field",
                       "PIM value", "Source", "Status", "Note", _CHECK_HEADER, _COMMENT_HEADER]
_DETAIL_HEADER_GS1 = ["Material", "Description", "Brick", "Brick description", "Pick ID", "Attribute",
                      "Mandatory", "GDSN type", "Unit", "Exported value", "Value description", "PIM field",
                      "PIM value", "Source", "Status", "Note", _CHECK_HEADER, _COMMENT_HEADER]


def to_xlsx(etim: Optional[dict], gs1: Optional[dict], matnrs: list[str]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Overview"
    overview = (etim or {}).get("overview", []) + (gs1 or {}).get("overview", [])
    overview.sort(key=lambda o: (matnrs.index(o["matnr"]) if o["matnr"] in matnrs else 0, o["system"]))
    _write_sheet(
        ws,
        ["Material", "Description", "Check", "ZZTYPEN", "Class / Brick", "Class / Brick description",
         "Features / attributes", "Filled", "of which default", "PIM empty", "Mandatory missing", "Dropped (invalid)",
         "No mapping", "Remark"],
        [[o["matnr"], o["name"], o["system"], o["zztyp"], o["class"], o["class_desc"], o.get("total"),
          o.get("filled"), o.get("default"), o.get("empty"), o.get("missing") if o["system"] != "ETIM" else None,
          o.get("dropped"), o.get("no_mapping"), o["remark"]] for o in overview],
        [12, 40, 18, 28, 12, 40, 12, 9, 10, 10, 11, 10, 11, 60],
    )
    ws.append([])
    ws.append([f"Created {datetime.now():%Y-%m-%d %H:%M}. Values as they would be exported; PIM data is "
               "updated daily. Please send this file to data management for corrections in the PIM."])

    if etim is not None:
        _write_sheet(
            wb.create_sheet("ETIM"), _DETAIL_HEADER_ETIM,
            [[r["matnr"], r["name"], r["class"], r["class_desc"], r["feature_id"], r["feature"], r["type"], r["unit"],
              r["value"], r["value_desc"], r["pim_field"], r["pim_value"], r["source"], r["status"], r["note"], None,
              None] for r in etim["rows"]],
            [11, 30, 11, 28, 11, 40, 12, 12, 14, 30, 24, 36, 30, 17, 45, 10, 40],
            status_col=14, wrap_cols=(6, 10, 12, 15, 17), check_col=16,
        )
    if gs1 is not None:
        _write_sheet(
            wb.create_sheet("GS1"), _DETAIL_HEADER_GS1,
            [[r["matnr"], r["name"], r["class"], r["class_desc"], r["pick_id"], r["attribute"], r["mandatory"],
              r["gdsn_type"], r["unit"], r["value"], r["value_desc"], r["pim_field"], r["pim_value"], r["source"],
              r["status"], r["note"], None, None] for r in gs1["rows"]],
            [11, 30, 11, 28, 8, 36, 10, 20, 8, 24, 28, 24, 36, 30, 17, 45, 10, 40],
            status_col=15, wrap_cols=(6, 10, 11, 13, 16, 18), check_col=17,
        )
    if etim is not None:
        _write_sheet(wb.create_sheet("ETIM allowed values"),
                     ["ETIM class", "Class description", "Feature ID", "Feature description", "Value ID",
                      "Value description"], etim["allowed"], [11, 30, 11, 40, 11, 40])
    if gs1 is not None:
        _write_sheet(wb.create_sheet("GS1 picklist values"),
                     ["Brick", "Pick ID", "Attribute", "Code", "Label"], gs1["allowed"], [11, 8, 36, 30, 40])

    legend = wb.create_sheet("Legend")
    _write_sheet(legend, ["Status", "Meaning"], [[s, text] for s, _, text in STATUS_INFO], [20, 110],
                 status_col=1, wrap_cols=(2,))
    legend.freeze_panes = None
    legend.auto_filter.ref = None

    wb.properties.title = "Data quality check"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build_report(matnrs: list[str], systems: list[str], vkorg: str = "") -> bytes:
    """Excel report for *matnrs*; systems: subset of {'etim', 'gs1'}. Raises
    QualityCheckError on bad input or hard failures."""
    if not matnrs:
        raise QualityCheckError("Please enter at least one material number.")
    if len(matnrs) > MAX_ARTICLES:
        raise QualityCheckError(f"At most {MAX_ARTICLES} material numbers per check.")
    if not systems:
        raise QualityCheckError("Please select ETIM and/or GS1.")
    try:
        conn = gs1_database.get_connection()
    except Exception as exc:
        raise QualityCheckError(f"Database connection failed: {exc}") from exc
    try:
        etim = check_etim(conn, matnrs) if "etim" in systems else None
        gs1 = check_gs1(conn, matnrs, vkorg) if "gs1" in systems else None
    finally:
        conn.close()
    return to_xlsx(etim, gs1, matnrs)
