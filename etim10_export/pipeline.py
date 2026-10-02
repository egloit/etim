"""
ETIM10 BMEcat export - replaces the Lobster profiles "PIM_DWH_ETIM10
Features vorberechnen V2 Step 2" (feature computation, see feature_engine)
and "BME_CAT_ETIM10_Export - XML Generierung" (this file + bmecat_builder).

Same contract as gs1_export.pipeline.export_batch(): a missing article or
value never aborts the batch, it becomes a warning; hard failures (DB
unreachable) raise PipelineError.

Deliberate deviations from the Lobster output (Lobster bugs):
  - volume in m3 from MARA.VOLUM/VOLEH (Lobster divided dm3 by 1e6)
  - master box gets the ZZMB* box dimensions/weight (Lobster used ZZEV*
    and an out-of-range map column -> no weight)
  - net weight = NTGEW (Lobster wrote the gross weight BRGEW)
  - no empty PRODUCT_STATUS (XSD-invalid)
Kept like Lobster on purpose: price block with only a start date when there
is no price (XSD-invalid, reported as one summary warning).
"""
import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Optional

import httpx
import psycopg
from psycopg.types.json import Jsonb
from dotenv import load_dotenv

from gs1_export import database, pim_reader, sap_database

from . import bmecat_builder, sources, validator
from .feature_engine import RuleSet, compute_features

load_dotenv()

logger = logging.getLogger(__name__)

_LANGUAGE_CONFIG_PATH = Path(__file__).with_name("language_mapping.json")
_SUPPLIER_GLN_CONFIG_PATH = Path(__file__).with_name("vkorg_supplier_gln.json")

# Header values shared with the GS1 export (same EGLO-wide data).
SUPPORT_CONTACT_EMAIL = os.getenv("GS1_SUPPORT_CONTACT_EMAIL", "sap.support@eglo.com")
ETIM_CONTACT_NAME = os.getenv("ETIM10_CONTACT_NAME", "ETIM Support Team")
SUPPLIER_URL = "https://www.eglo.com"
SUPPLIER_LOGO_URL = ("https://www.eglo.com/static/version1741784053/frontend/Eglo/egloshop/de_AT/"
                     "images/EGLO-Logo.svg")
DATASHEET_URL = "https://tools.eglo.com/tds/{tds}/{matnr}"
# Off by default: tools.eglo.com renders the PDF even for HEAD (~5 s per article under load).
CHECK_DATASHEET_LINKS = os.getenv("ETIM10_CHECK_DATASHEET_LINKS", "false").lower() == "true"

# KEYWORD sources, in Lobster order; labels are written without the "[xx] " prefix.
_KEYWORD_FIELDS = ["ZZPRDSG", "ZZTYPEN", "ZZKOLLE", "ZZFUN01", "B2C_FILTER_FUNCTION", "ZZLMSCH", "ZZSCHAA"]
_KEYWORD_SKIP_CODES = {"ZZPRDSG_KER", "ZZPRDSG_LUX", "ZZPRDSG_STR", "ZZPRDSG_TLI", "ZZPRDSG_ZZZ", "ZZKOLLE_ZZZ",
                       "ZZFUN01_000"}  # ZZFUN01_000 = "keine"
_LABEL_PREFIX = re.compile(r"^[^\]]*\] ")

# BMEcat price_type -> c_PriceData column, only written when > 0.
_PRICE_TYPES = [("net_list", "PurchasePriceExcludingVat"), ("nrp", "SuggestedRetailPriceIncludingVat"),
                ("net_customer", "SalesPriceExcludingVat")]

_CHUNK = 200  # articles per batch query

_VOLUME_TO_M3 = {"CDM": 0.001, "DMQ": 0.001, "CCM": 0.000001, "CMQ": 0.000001, "M3": 1, "MTQ": 1}


class PipelineError(Exception):
    """Raised for hard failures that abort the whole export (e.g. DB unreachable)."""


def _load_languages(lang_codes: list[str]) -> list[dict]:
    try:
        config = json.loads(_LANGUAGE_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PipelineError(f"language_mapping.json nicht lesbar: {exc}") from exc
    languages = [config[c] for c in lang_codes if c in config and not c.startswith("_")]
    return languages or [config["ger"]]


def _supplier_gln(vkorg: str) -> Optional[str]:
    try:
        config = json.loads(_SUPPLIER_GLN_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return config.get(vkorg) or None


def _mm_to_m(value) -> Optional[str]:
    return bmecat_builder.format_decimal(float(value) / 1000, 3) if value else None


def _volume_m3(value, unit: str) -> Optional[str]:
    factor = _VOLUME_TO_M3.get((unit or "").upper())
    if not value or factor is None:
        return None
    return bmecat_builder.format_decimal(float(value) * factor, 6)


def _locales(language: dict) -> list[str]:
    return [language["pim_locale"], *language.get("pim_fallback", [])]


def _labels(product: dict, field: str, locales: list[str]) -> list[str]:
    entries = pim_reader._find_entries(product, field)
    if not entries:
        return []
    data = entries[0].get("data")
    codes = data if isinstance(data, list) else [data] if data else []
    linked = entries[0].get("linked_data") or {}
    out = []
    for code in codes:
        if code in _KEYWORD_SKIP_CODES:
            continue
        labels = (linked.get(code) or {}).get("labels", {}) if isinstance(data, list) else linked.get("labels", {})
        label = _LABEL_PREFIX.sub("", next((labels[l] for l in locales if labels.get(l)), "").strip())
        if label and label != "-":
            out.append(label)
    return out


def _text(product: dict, field: str, locales: list[str]) -> Optional[str]:
    """First non-empty translation in *locales* order."""
    translations = (pim_reader.get_pim_value(product, field) or {}).get("translations", {})
    return next((t.strip() for l in locales if (t := translations.get(l)) and t.strip()), None)


def _datasheet_ok(url: str) -> bool:
    try:
        return httpx.head(url, timeout=15, follow_redirects=True).status_code == 200
    except httpx.HTTPError:
        return False


def _packaging(mara: Optional[dict]) -> tuple[int, Optional[dict], Optional[dict]]:
    """(pieces per order unit, packing unit, net logistic details) from MARA."""
    if not mara:
        return 1, None, None
    quantity = int(mara["mb_quantity"] or 0) or 1
    volume_unit = mara.get("volume_unit")
    if quantity >= 2:
        box_volume = None
        if mara["mb_length"] and mara["mb_width"] and mara["mb_depth"]:
            box_volume = bmecat_builder.format_decimal(
                float(mara["mb_length"]) * float(mara["mb_width"]) * float(mara["mb_depth"]) / 1e9, 6)
        packing_unit = {"code": "CT", "quantity": quantity, "volume": box_volume,
                        "weight": bmecat_builder.format_decimal(mara["mb_weight"]) if mara["mb_weight"] else None,
                        "length": _mm_to_m(mara["mb_length"]), "width": _mm_to_m(mara["mb_width"]),
                        "depth": _mm_to_m(mara["mb_depth"])}
    else:
        packing_unit = {"code": "PA", "quantity": 1, "volume": _volume_m3(mara["volume"], volume_unit),
                        "weight": bmecat_builder.format_decimal(mara["gross_weight"]) if mara["gross_weight"] else None,
                        "length": _mm_to_m(mara["ev_length"]), "width": _mm_to_m(mara["ev_width"]),
                        "depth": _mm_to_m(mara["ev_depth"]), "gtin": mara.get("ean")}
    net_weight = mara["net_weight"] or mara["gross_weight"]
    logistic = {"volume": _volume_m3(mara["volume"], volume_unit),
                "weight": bmecat_builder.format_decimal(net_weight) if net_weight else None,
                "length": _mm_to_m(mara["ev_length"]), "width": _mm_to_m(mara["ev_width"]),
                "depth": _mm_to_m(mara["ev_depth"])}
    return quantity, packing_unit, logistic


def _prices(row: Optional[dict]) -> Optional[dict]:
    if not row:
        return None
    amounts = [(ptype, bmecat_builder.format_decimal(row[col], 2)) for ptype, col in _PRICE_TYPES
               if row.get(col) is not None and row[col] > 0]
    if not amounts:
        return None
    vat = row.get("VatRatePercentage")
    return {"amounts": amounts, "currency": row.get("currency"),
            "tax": bmecat_builder.format_decimal(vat / 100) if vat is not None else None}


def _build_product(matnr: str, product: dict, languages: list[dict], rules: RuleSet, data: dict,
                   warnings: list[str]) -> dict:
    """*data*: prefetched {mara, origin, assets, eprel, price_row, datasheet_ok, hana}."""
    first = languages[0]
    p: dict = {"matnr": matnr, "price_date": date.today().isoformat()}

    p["descriptions_short"] = {l["bmecat"]: t for l in languages if (t := _text(product, "MAKTX", _locales(l)))}
    if not p["descriptions_short"]:
        fallback = _text(product, "MAKTX", ["de_DE"]) or matnr
        p["descriptions_short"] = {first["bmecat"]: fallback}
        warnings.append(f"{matnr}: kein Materialkurztext (MAKTX) in den gewählten Sprachen – Fallback verwendet.")
    p["descriptions_long"] = {l["bmecat"]: t for l in languages
                              if (t := _text(product, "PIM_ARTIKELTEXT", _locales(l)))}
    p["keywords"] = {l["bmecat"]: words for l in languages
                     if (words := [w for f in _KEYWORD_FIELDS for w in _labels(product, f, _locales(l))])}

    p["class_id"] = rules.class_by_zztyp[pim_reader.get_zztypen_code(product) or ""]
    p["features"], feature_warnings = compute_features(product, p["class_id"], rules, matnr)
    warnings.extend(feature_warnings)

    series = pim_reader.get_pim_raw_code(product, "ZZSER")
    if series:
        p["series"], p["series_lang"] = series, first["bmecat"]
    battery = pim_reader.get_pim_raw_code(product, "ZZAKKJN")
    p["battery_contained"] = {"ZZAKKJN_J": True, "ZZAKKJN_N": False}.get(battery or "")
    p["ce_marking"] = "qc_symbol_CE" in pim_reader.get_pim_raw_codes(product, "qc_symbol")

    hana, mara = data["hana"], data["mara"]
    if hana and mara is None:
        warnings.append(f"{matnr}: kein Datensatz im SAP-Materialstamm (MARA).")
    p["gtin"] = (mara or {}).get("ean") or None
    if hana and mara and not p["gtin"]:
        warnings.append(f"{matnr}: EAN11 im SAP-Materialstamm ist leer.")
    p["pieces_per_box"], p["packing_unit"], p["logistic"] = _packaging(mara)

    origin = data["origin"] or {}
    p["customs_number"] = origin.get("customs_number") or None
    p["country_of_origin"] = origin.get("country_of_origin") or None
    if hana and data["werks"]:
        missing = [name for key, name in (("customs_number", "Zolltarifnummer"), ("country_of_origin", "Ursprungsland"))
                   if not p[key]]
        if missing:
            warnings.append(f"{matnr}: {' und '.join(missing)} in SAP (MARC, Werk {data['werks']}) leer.")

    p["prices"] = _prices(data["price_row"])

    assets, eprel = data["assets"], data["eprel"]
    mimes = []
    for asset_code, mime_code in sources.DAM_MIME_CODES:
        asset = assets.get(asset_code)
        if asset:
            mimes.append({"source": asset["url"], "code": mime_code, "filename": asset["filename"]})
        if mime_code == "MD01":
            if not asset:
                warnings.append(f"{matnr}: kein Produktbild (DAM AssetCode 101).")
            if eprel.get("energy_label"):
                mimes.append({"source": eprel["energy_label"], "code": "MD06", "filename": f"{matnr}_EEI.svg"})
    if data["datasheet_ok"] is not False:
        mimes.append({"source": DATASHEET_URL.format(tds=first["tds"], matnr=matnr), "code": "MD22",
                      "filename": f"datasheet_{matnr}_{first['pim_locale']}.pdf"})
    else:
        warnings.append(f"{matnr}: Datenblatt existiert nicht auf tools.eglo.com.")
    if eprel.get("datasheet"):
        mimes.append({"source": eprel["datasheet"], "code": "MD07", "filename": f"{matnr}_EPREL_datasheet.pdf"})
    mimes.sort(key=lambda m: m["code"])
    p["mimes"] = mimes
    return p


def export_batch(matnrs: list[str], lang_codes: list[str], kunnr: str, vkorg: str, vtweg: str, spart: str,
                 werks: str = "", preise: str = "none", exported_by: Optional[str] = None,
                 warning_details: Optional[list[str]] = None,
                 ) -> tuple[bytes, list[str], list[dict]]:
    """Build one BMEcat T_NEW_CATALOG document for *matnrs*.

    Returns (xml_bytes, warnings grouped by cause, validation_errors); the
    ungrouped per-article warnings go into *warning_details* if given (problem
    list stored with the file)."""
    languages = _load_languages(lang_codes)
    unique_matnrs = list(dict.fromkeys(m.strip() for m in matnrs if m.strip()))
    warnings: list[str] = []

    try:
        pg = database.get_connection()
    except Exception as exc:
        raise PipelineError(f"Datenbankverbindung fehlgeschlagen: {exc}") from exc
    try:
        hana = sap_database.get_connection()
    except Exception as exc:
        hana = None
        logger.error("ETIM10 EXPORT | SAP-HANA-Verbindung fehlgeschlagen: %s", exc)
        warnings.append("SAP-HANA-Verbindung fehlgeschlagen – EAN, Verpackung, Kunde, Zoll und Ursprung fehlen.")

    try:
        rules = RuleSet.load(pg)
        sales_org = (sources.get_sales_org(hana, vkorg) if hana and vkorg else None) or {}
        buyer = (sources.get_buyer(hana, kunnr) if hana and kunnr else None) or {}
        if hana and kunnr and not buyer:
            warnings.append(f"Kunde {kunnr} nicht im SAP-Kundenstamm (KNA1) gefunden.")
        header = {
            "generator_info": "etim10_formular | ETIM10 BMEcat Export",
            "languages": [l["bmecat"] for l in languages],
            "generation_date": date.today().isoformat(),
            "currency": sales_org.get("currency"),
            "buyer": {"kunnr": kunnr.zfill(10) if kunnr else "", "name": buyer.get("name"), "gln": buyer.get("gln")},
            "supplier": {"name": sales_org.get("company_name") or "EGLO Leuchten GmbH", "gln": _supplier_gln(vkorg),
                         "vat_id": sales_org.get("vat_id"), "contact": ETIM_CONTACT_NAME,
                         "email": SUPPORT_CONTACT_EMAIL, "url": SUPPLIER_URL, "logo_url": SUPPLIER_LOGO_URL},
        }

        price_rows = {}
        if preise in ("csv", "sap"):
            price_rows = sources.get_prices(pg, unique_matnrs, vkorg, vtweg, spart, kunnr.zfill(10), preise)

        datasheet_ok: dict[str, Optional[bool]] = {}
        if CHECK_DATASHEET_LINKS:
            urls = {m: DATASHEET_URL.format(tds=languages[0]["tds"], matnr=m) for m in unique_matnrs}
            with ThreadPoolExecutor(max_workers=16) as pool:
                datasheet_ok = dict(zip(urls, pool.map(_datasheet_ok, urls.values())))

        # Only the attributes the export reads (rules + texts/keywords/flags).
        pim_fields = rules.pim_fields() | {"MAKTX", "PIM_ARTIKELTEXT", "ZZSER", "ZZAKKJN", "qc_symbol",
                                           *_KEYWORD_FIELDS}
        products = []
        for start in range(0, len(unique_matnrs), _CHUNK):
            chunk = unique_matnrs[start:start + _CHUNK]
            pim = sources.get_products(pg, chunk, pim_fields)
            assets = sources.get_dam_assets(pg, chunk)
            eprel = sources.get_eprel_links(pg, chunk)
            mara = sources.get_mara_packaging(hana, chunk) if hana else {}
            origin = sources.get_marc_origin(hana, chunk, werks) if hana and werks else {}
            history = []
            for matnr in chunk:
                product = pim.get(matnr)
                if product is None:
                    warnings.append(f"{matnr}: nicht im PIM gefunden – übersprungen.")
                    continue
                zztyp = pim_reader.get_zztypen_code(product) or ""
                if zztyp not in rules.class_by_zztyp:
                    # PRODUCT_FEATURES is mandatory in T_NEW_CATALOG (Lobster: "Critical").
                    warnings.append(f"{matnr}: keine ETIM-Klasse für ZZTYPEN '{zztyp}' – nicht in der Datei.")
                    continue
                if preise in ("csv", "sap") and not _prices(price_rows.get(matnr)):
                    warnings.append(f"{matnr}: kein Preis in Datenquelle {preise.upper()} gefunden.")
                data = {"hana": hana, "werks": werks, "mara": mara.get(matnr), "origin": origin.get(matnr),
                        "assets": assets.get(matnr, {}), "eprel": eprel.get(matnr, {}),
                        "price_row": price_rows.get(matnr), "datasheet_ok": datasheet_ok.get(matnr)}
                products.append(_build_product(matnr, product, languages, rules, data, warnings))
                history.append((matnr, products[-1].get("class_id"), product.get("updated"), exported_by,
                                Jsonb(_snapshot(products[-1]))))
            upsert_history(pg, history)
    except psycopg.Error as exc:
        raise PipelineError(f"Datenbankfehler: {exc}") from exc
    finally:
        pg.close()
        if hana is not None:
            hana.close()

    if not products:
        raise PipelineError("Keiner der Artikel wurde im PIM gefunden bzw. hat eine ETIM-Klasse.")
    xml_bytes = bmecat_builder.build_document(header, products)
    errors = validator.validate_xml(xml_bytes)
    # Known, accepted deviation (price block without price, see bmecat_builder):
    # one summary warning instead of one XSD error per article.
    without_price = [e for e in errors if _is_missing_price_error(e)]
    if without_price:
        warnings.insert(0, f"{len(without_price)} Artikel ohne Preis – Preisblock nur mit Datum (wie bisher "
                           f"im Lobster, laut XSD unvollständig).")
    if warning_details is not None:
        warning_details.extend(warnings)
    return xml_bytes, group_warnings(warnings), [e for e in errors if not _is_missing_price_error(e)]


def group_warnings(warnings: list[str], examples: int = 10) -> list[str]:
    """Collapse per-article warnings ("<matnr>: <text>") with the same text into
    one line per cause, most frequent first: "<text> – 1545 Artikel (z. B. 30658,
    44131, …)". Warnings without article prefix are kept as they are, on top."""
    general, by_text = [], {}
    for warning in warnings:
        matnr, sep, text = warning.partition(": ")
        if not sep or " " in matnr:
            general.append(warning)
            continue
        by_text.setdefault(text, []).append(matnr)
    grouped = []
    for text, matnrs in sorted(by_text.items(), key=lambda kv: -len(kv[1])):
        if len(matnrs) == 1:
            grouped.append(f"{matnrs[0]}: {text}")
            continue
        sample = ", ".join(matnrs[:examples]) + (", …" if len(matnrs) > examples else "")
        grouped.append(f"{text.rstrip('.')} – {len(matnrs)} Artikel (z. B. {sample})")
    return general + grouped


def _is_missing_price_error(error: dict) -> bool:
    return "PRODUCT_PRICE_DETAILS': Missing child element" in error["message"]


def _snapshot(product: dict) -> dict:
    """What the file contained for this article - compared later by get_changed_articles()."""
    return {"class_id": product.get("class_id"),
            "features": {f["feature_id"]: f["values"] for f in product.get("features", [])}}


def upsert_history(conn, rows: list[tuple]) -> None:
    """rows: (matnr, class_id, pim_updated_at, exported_by, feature_snapshot)"""
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO public.etim10_export_history
                (matnr, class_id, exported_at, pim_updated_at_export, exported_by, feature_snapshot)
            VALUES (%s, %s, now(), %s, %s, %s)
            ON CONFLICT (matnr) DO UPDATE SET class_id = EXCLUDED.class_id, exported_at = EXCLUDED.exported_at,
                pim_updated_at_export = EXCLUDED.pim_updated_at_export, exported_by = EXCLUDED.exported_by,
                feature_snapshot = EXCLUDED.feature_snapshot
            """,
            rows,
        )


def _display(values: list[str], rules: RuleSet) -> str:
    return " – ".join(rules.value_names.get(v, v) for v in values)


def diff_features(old: dict, new: dict, rules: RuleSet) -> list[dict]:
    """Feature-level changes between two snapshots ({"class_id", "features"})."""
    changes = []
    if old.get("class_id") != new.get("class_id"):
        changes.append({"kind": "class", "feature_id": None, "name": "ETIM-Klasse",
                        "old": old.get("class_id"), "new": new.get("class_id")})
    old_f, new_f = old.get("features", {}), new.get("features", {})
    for fid in sorted(set(old_f) | set(new_f)):
        before, after = old_f.get(fid), new_f.get(fid)
        if before == after:
            continue
        kind = "added" if before is None else "removed" if after is None else "changed"
        changes.append({"kind": kind, "feature_id": fid, "name": rules.feature_names.get(fid, fid),
                        "old": _display(before, rules) if before else None,
                        "new": _display(after, rules) if after else None})
    return changes


_CHANGED_LIMIT = 500


def get_changed_articles() -> list[dict]:
    """Previously exported articles whose ETIM features differ from what the
    last export contained - recomputed for articles whose PIM "updated"
    timestamp moved. Articles whose PIM change doesn't affect any ETIM feature
    are not listed. Newest PIM change first, at most _CHANGED_LIMIT checked."""
    try:
        conn = database.get_connection()
    except Exception as exc:
        raise PipelineError(f"Datenbankverbindung fehlgeschlagen: {exc}") from exc
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT h.matnr, h.exported_at, h.feature_snapshot, latest.updated
                FROM public.etim10_export_history h
                JOIN LATERAL (
                    SELECT updated FROM public.pim_egloakeneo_product p
                    WHERE p.matnr = h.matnr AND p.enabled = TRUE ORDER BY p.updated DESC LIMIT 1
                ) latest ON TRUE
                WHERE latest.updated IS DISTINCT FROM h.pim_updated_at_export AND h.feature_snapshot IS NOT NULL
                ORDER BY latest.updated DESC
                LIMIT %s
                """,
                (_CHANGED_LIMIT,),
            )
            candidates = cur.fetchall()
        if not candidates:
            return []
        rules = RuleSet.load(conn)
        products = sources.get_products(conn, [c[0] for c in candidates], rules.pim_fields())
    finally:
        conn.close()

    changed = []
    for matnr, exported_at, snapshot, pim_updated in candidates:
        product = products.get(matnr)
        if not product:
            continue
        class_id = rules.class_by_zztyp.get(pim_reader.get_zztypen_code(product) or "")
        features = compute_features(product, class_id, rules, matnr)[0] if class_id else []
        current = {"class_id": class_id, "features": {f["feature_id"]: f["values"] for f in features}}
        changes = diff_features(snapshot, current, rules)
        if changes:
            changed.append({"matnr": matnr, "exported_at": exported_at.isoformat() if exported_at else None,
                            "pim_updated_now": pim_updated, "changes": changes})
    return changed


def list_export_files(exported_by: str) -> list[dict]:
    """Files previously generated by *exported_by*, newest first (without content)."""
    try:
        with database.get_connection() as conn:
            rows = conn.execute(
                """
                SELECT id, filename, exported_at, article_count, matnrs,
                       COALESCE(jsonb_array_length(issues), 0)
                FROM public.etim10_export_files
                WHERE exported_by = %s ORDER BY exported_at DESC
                """,
                (exported_by,),
            ).fetchall()
    except Exception as exc:
        raise PipelineError(f"Datenbankfehler: {exc}") from exc
    return [{"id": r[0], "filename": r[1], "exported_at": r[2].isoformat() if r[2] else None,
             "article_count": r[3], "matnrs": r[4], "issue_count": r[5]} for r in rows]


def get_export_file(file_id: int, exported_by: str) -> Optional[dict]:
    """A stored file, only for the user who generated it (ids of other users can't be guessed)."""
    try:
        with database.get_connection() as conn:
            row = conn.execute(
                "SELECT filename, xml_content, issues FROM public.etim10_export_files "
                "WHERE id = %s AND exported_by = %s",
                (file_id, exported_by),
            ).fetchone()
    except Exception as exc:
        raise PipelineError(f"Datenbankfehler: {exc}") from exc
    return {"filename": row[0], "xml_content": bytes(row[1]), "issues": row[2] or []} if row else None


def save_export_file(filename: str, exported_by: Optional[str], matnrs: list[str], xml_bytes: bytes,
                     issues: Optional[list[dict]] = None) -> None:
    """Archive the generated file with its problem list (never fails the request, logs only)."""
    try:
        with database.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO public.etim10_export_files
                    (filename, exported_by, matnrs, article_count, xml_content, issues)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (filename, exported_by, matnrs, len(matnrs), xml_bytes, Jsonb(issues or [])),
            )
    except Exception as exc:
        logger.error("ETIM10 EXPORT | Datei konnte nicht archiviert werden: %s", exc)
