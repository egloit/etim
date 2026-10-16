"""
Load the nominal useful luminous flux (Pick 8.353 visibleLight) and the light
beam type (8.779 lightBeamTypeCode) from the public EPREL API into
public.gs1_manual_property_values (source 'eprel').

GS1 Benelux wants 8.353 "as indicated on the packaging", which is the EPREL
value of the light source (checked for 32048: packaging 1900 lm / 360 deg =
EPREL 1111970). Once 8.353 is sent, rule 500.471 requires 8.779 as well.

Registration number per article (public.eprel_view):
  - light sources: eprel_view.matnr = our matnr
  - luminaires with integrated LED: eprel_view.matnr = the LED module model
    identifier from PIM_LED_NR_1 (only if it is the article's single module)
Only PUBLISHED registrations are readable without an API key.

Rows with source 'manual' are never overwritten; 'webcenter' rows are.

    python -m gs1_export.load_eprel_values [--dry-run]
"""
import argparse
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import httpx

from etim10_export.sources import get_products
from gs1_export import database

logger = logging.getLogger(__name__)

EPREL_API = "https://eprel.ec.europa.eu/api/products/lightsources/{}"
BEAM_CODES = {"SPHERE_360": "SPHERE", "WIDE_CONE_120": "WIDE_CONE", "NARROW_CONE_90": "NARROW_CONE"}
_LED_FIELDS = ("PIM_LED_NR_1", "PIM_LED_NR_2", "PIM_LED_NR_3")
_CHUNK = 500


def _key(model: str) -> str:
    # eprel_view shows some separators as '?'
    return model.replace("?", "-").replace(" ", "").upper()


def _fetch(client: httpx.Client, reg: str) -> Optional[dict]:
    try:
        r = client.get(EPREL_API.format(reg))
        return r.json() if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("EPREL %s: %s", reg, exc)
        return None


def values_from_eprel(data: dict) -> dict[str, str]:
    """{pick: value} for 8.353/8.779 from one EPREL light source record."""
    out = {}
    flux, beam = data.get("luminousFlux"), BEAM_CODES.get(data.get("beamAngleCorrespondence") or "")
    if flux and beam:
        out["8.353"] = str(flux)
        out["8.779"] = beam
    return out


def main(dry_run: bool = False) -> None:
    conn = database.get_connection()
    with conn.cursor() as cur:
        cur.execute('SELECT matnr, "EPREL_RegistrationNumber", "EPREL_Status" FROM public.eprel_view')
        eprel = {_key(k): (str(reg), st) for k, reg, st in cur.fetchall() if k and reg}
        cur.execute("SELECT DISTINCT matnr FROM public.pim_egloakeneo_product WHERE enabled = TRUE")
        matnrs = sorted(r[0] for r in cur.fetchall() if r[0])

    reg_by_matnr: dict[str, str] = {}
    for start in range(0, len(matnrs), _CHUNK):
        chunk = matnrs[start:start + _CHUNK]
        products = get_products(conn, chunk, set(_LED_FIELDS))
        for matnr in chunk:
            hit = eprel.get(_key(matnr))
            if hit is None:
                values = (products.get(matnr) or {}).get("values", {})
                modules = [e["data"] for f in _LED_FIELDS for e in values.get(f, []) if e.get("data")]
                hit = eprel.get(_key(modules[0])) if len(modules) == 1 else None
            if hit and hit[1] == "PUBLISHED":
                reg_by_matnr[matnr] = hit[0]

    regs = sorted(set(reg_by_matnr.values()))
    with httpx.Client(timeout=20) as client, ThreadPoolExecutor(max_workers=4) as pool:
        records = dict(zip(regs, pool.map(lambda r: _fetch(client, r), regs)))

    rows = [(m, pick, v) for m, reg in reg_by_matnr.items() if records.get(reg)
            for pick, v in values_from_eprel(records[reg]).items()]
    print(f"Artikel mit veröffentlichter EPREL-Registrierung: {len(reg_by_matnr)}, Registrierungen: {len(regs)}, "
          f"abgerufen: {sum(1 for r in records.values() if r)}, Werte: {len(rows)}")
    if dry_run:
        conn.close()
        return
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO public.gs1_manual_property_values (matnr, pick_id, value, source)
            VALUES (%s, %s, %s, 'eprel')
            ON CONFLICT (matnr, pick_id) DO UPDATE
               SET value = EXCLUDED.value, source = 'eprel', active = TRUE, updated_at = now()
             WHERE public.gs1_manual_property_values.source IN ('eprel', 'webcenter')
            """,
            rows,
        )
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true")
    main(parser.parse_args().dry_run)
