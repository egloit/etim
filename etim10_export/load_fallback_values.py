"""
Load ETIM10 fallback values into public.etim10_fallback_values - used by the
export only where the PIM rule of a feature yields nothing (see
feature_engine.compute_raw).

Sources, EPREL first (per article and feature):
  eprel      public EPREL API, PUBLISHED registrations only (no key needed).
             Registration per article from public.eprel_view: matnr = our
             matnr (light sources) or = the LED module model identifier from
             PIM_LED_NR_1..3 (luminaires, only if it is the single module).
  webcenter  packaging layouts, Webcenter output/produkte.csv (only rows whose
             EAN matches the PIM EAN, or without EAN).

Not loaded on purpose: luminous flux / power - EPREL and packaging give the
light source (LED module) value, ETIM wants the luminaire's.

Rows with source 'manual' are never overwritten.

    python -m etim10_export.load_fallback_values [--webcenter PATH] [--dry-run]
"""
import argparse
import csv
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import httpx

from etim10_export.sources import get_products
from gs1_export import database, pim_reader

logger = logging.getLogger(__name__)

EPREL_API = "https://eprel.ec.europa.eu/api/products/lightsources/{}"
WEBCENTER_CSV = r"C:\Users\stephan.kandlhofer\PycharmProjects\Webcenter\output\produkte.csv"
_LED_FIELDS = ("PIM_LED_NR_1", "PIM_LED_NR_2", "PIM_LED_NR_3")
_CHUNK = 500

# EF000442 colour rendering index: lower bound of the ETIM range -> value
_CRI = [(90, "EV003580"), (80, "EV003581"), (70, "EV003582"), (60, "EV003583"), (40, "EV003584"), (20, "EV003585")]
# EF008157 beam angle: upper bound in degrees -> value (> 180 = omnidirectional)
_BEAM = [(4.99, "EV011540"), (10, "EV011537"), (20, "EV011535"), (40, "EV011536"), (80, "EV011534"),
         (180, "EV011538")]
_BEAM_DIFFUSE = "EV011539"  # open beam/diffuse
_BEAM_CORRESPONDENCE = {"SPHERE_360": 360, "WIDE_CONE_120": 120, "NARROW_CONE_90": 90}
# EF011946 colour consistency: MacAdam steps 1..7 -> SDCM1..SDCM7
_SDCM = {n: f"EV0140{13 + n}" for n in range(1, 8)}


def _key(model: str) -> str:
    # eprel_view shows some separators as '?'
    return model.replace("?", "-").replace(" ", "").upper()


def _number(value) -> Optional[float]:
    if value in (None, ""):
        return None
    match = re.search(r"\d+(?:[.,]\d+)?", str(value))
    return float(match.group().replace(",", ".")) if match else None


def _fmt(number: float) -> str:
    return f"{number:.4f}".rstrip("0").rstrip(".")


def cri_value(cri) -> Optional[str]:
    n = _number(cri)
    return next((code for low, code in _CRI if n is not None and n >= low), None)


def beam_value(angle) -> Optional[str]:
    n = _number(angle)
    if n is None:
        return None
    if n > 180:
        return _BEAM_DIFFUSE
    return next(code for high, code in _BEAM if n <= high)


def values_from_eprel(d: dict) -> dict[str, tuple[str, Optional[str]]]:
    """{feature_id: (value, value2)} from one EPREL light source record."""
    out = {}
    if (code := cri_value(d.get("colourRenderingIndex") or d.get("minColourRenderingIndex"))):
        out["EF000442"] = (code, None)
    low, high = _number(d.get("correlatedColourTempMin")), _number(d.get("correlatedColourTempMax"))
    if low or high:
        out["EF009346"] = (_fmt(low or high), _fmt(high or low))
    angle = d.get("beamAngle") or _BEAM_CORRESPONDENCE.get(d.get("beamAngleCorrespondence") or "")
    if (code := beam_value(angle)):
        out["EF008157"] = (code, None)
    dimmable = {"NO": "false", "YES": "true", "SPECIFIC": "true"}.get(d.get("dimmable") or "")
    if dimmable:
        out["EF000137"] = (dimmable, None)
    if (pf := _number(d.get("displacementFactor"))):  # 0 = not declared
        out["EF025740"] = (_fmt(pf), None)
    for feature, field in (("EF015827", "flickerMetric"), ("EF015828", "stroboscopicEffectMetric")):
        if (n := _number(d.get(field))) is not None:
            out[feature] = (_fmt(n), None)
    if (steps := _number(d.get("colourConsistency"))) and int(steps) in _SDCM:
        out["EF011946"] = (_SDCM[int(steps)], None)
    return out


def values_from_webcenter(row: dict) -> dict[str, tuple[str, Optional[str]]]:
    """{feature_id: (value, value2)} from one produkte.csv row."""
    out = {}
    if (code := cri_value(row.get("cri_ra"))):
        out["EF000442"] = (code, None)
    temps = [n for part in (row.get("farbtemperatur_k") or "").split("|") if (n := _number(part))]
    if temps:
        out["EF009346"] = (_fmt(min(temps)), _fmt(max(temps)))
    if (code := beam_value(row.get("abstrahlwinkel_grad"))):
        out["EF008157"] = (code, None)
    if (count := _number(row.get("anzahl_leuchtmittel"))):
        out["EF006436"] = (_fmt(count), None)
    return out


def _fetch(client: httpx.Client, reg: str) -> Optional[dict]:
    try:
        r = client.get(EPREL_API.format(reg))
        return r.json() if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("EPREL %s: %s", reg, exc)
        return None


def _ean(product: dict) -> str:
    value = pim_reader.get_pim_value(product, "EAN11")
    return str(((value or {}).get("translations") or {}).get(None) or "").lstrip("0")


def main(webcenter_csv: str = WEBCENTER_CSV, dry_run: bool = False) -> None:
    conn = database.get_connection()
    with conn.cursor() as cur:
        cur.execute('SELECT matnr, "EPREL_RegistrationNumber", "EPREL_Status" FROM public.eprel_view')
        eprel = {_key(k): (str(reg), st) for k, reg, st in cur.fetchall() if k and reg}
        cur.execute("SELECT DISTINCT matnr FROM public.pim_egloakeneo_product WHERE enabled = TRUE")
        matnrs = sorted(r[0] for r in cur.fetchall() if r[0])

    try:
        with open(webcenter_csv, encoding="utf-8-sig", newline="") as f:
            webcenter = {r["artikelnummer"]: r for r in csv.DictReader(f, delimiter=";")}
    except OSError as exc:
        print(f"Webcenter-Datei nicht lesbar ({exc}) - nur EPREL.")
        webcenter = {}

    reg_by_matnr: dict[str, str] = {}
    wc_values: dict[str, dict] = {}
    for start in range(0, len(matnrs), _CHUNK):
        chunk = matnrs[start:start + _CHUNK]
        products = get_products(conn, chunk, set(_LED_FIELDS) | {"EAN11"})
        for matnr in chunk:
            product = products.get(matnr) or {"values": {}}
            hit = eprel.get(_key(matnr))
            if hit is None:
                modules = [e["data"] for f in _LED_FIELDS for e in product["values"].get(f, []) if e.get("data")]
                hit = eprel.get(_key(modules[0])) if len(modules) == 1 else None
            if hit and hit[1] == "PUBLISHED":
                reg_by_matnr[matnr] = hit[0]
            row = webcenter.get(matnr)
            if row and (not row.get("ean") or row["ean"].lstrip("0") == _ean(product)):
                if (values := values_from_webcenter(row)):
                    wc_values[matnr] = values

    regs = sorted(set(reg_by_matnr.values()))
    with httpx.Client(timeout=20) as client, ThreadPoolExecutor(max_workers=4) as pool:
        records = dict(zip(regs, pool.map(lambda r: _fetch(client, r), regs)))

    rows = {}
    for matnr, values in wc_values.items():
        for feature, (v, v2) in values.items():
            rows[(matnr, feature)] = (v, v2, "webcenter", None)
    for matnr, reg in reg_by_matnr.items():
        for feature, (v, v2) in values_from_eprel(records.get(reg) or {}).items():
            rows[(matnr, feature)] = (v, v2, "eprel", f"EPREL {reg}")  # EPREL beats packaging

    print(f"Artikel mit EPREL: {len(reg_by_matnr)} (Registrierungen {len(regs)}, abgerufen "
          f"{sum(1 for r in records.values() if r)}), mit Verpackungsdaten: {len(wc_values)}, Werte: {len(rows)}")
    if dry_run:
        conn.close()
        return
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO public.etim10_fallback_values (matnr, feature_id, value, value2, source, note)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (matnr, feature_id) DO UPDATE
               SET value = EXCLUDED.value, value2 = EXCLUDED.value2, source = EXCLUDED.source,
                   note = EXCLUDED.note, active = TRUE, updated_at = now()
             WHERE public.etim10_fallback_values.source <> 'manual'
            """,
            [(m, f, v, v2, src, note) for (m, f), (v, v2, src, note) in sorted(rows.items())],
        )
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--webcenter", default=WEBCENTER_CSV, help="Webcenter output/produkte.csv")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    main(args.webcenter, args.dry_run)
