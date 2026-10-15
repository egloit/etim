"""
Data access for the ETIM10 BMEcat export - the same sources the Lobster
profile "BME_CAT_ETIM10_Export - XML Generierung" (version 17) reads:

  PostgreSQL (Lobster_WorkData): PIM product JSON, DAM assets, EPREL, prices
  SAP HANA: MARA (packaging/weights/EAN), MARC (customs/origin),
            KNA1 (buyer), TVKO/T001 (currency, company name, VAT id)
"""
import logging
from typing import Optional

import psycopg

logger = logging.getLogger(__name__)

# DAM asset_code -> BMEcat MIME code, in the order Lobster writes them.
DAM_MIME_CODES = [
    ("101", "MD01"),  # main image (mandatory)
    ("110", "MD12"),  # technical drawing
    ("BDA", "MD14"),  # manual
    ("120", "MD20"),
    ("102", "MD23"),
    ("106", "MD24"),
    ("108", "MD25"),
    ("112", "MD26"),
    ("113", "MD27"),
    ("115", "MD28"),
    ("407", "MD37"),
    ("307", "MD46"),
]

PRICE_COLUMNS = ("currency", "VatRatePercentage", "SuggestedRetailPriceIncludingVat",
                 "PurchasePriceExcludingVat", "SalesPriceExcludingVat")


# ---------- PostgreSQL ----------

def get_products(conn: psycopg.Connection, matnrs: list[str], fields: Optional[set[str]] = None) -> dict[str, dict]:
    """PIM JSON per matnr (newest enabled row, same rule as gs1_export.database).
    With *fields*, only those attributes of "values" (case-insensitive) are
    transferred - the full JSON is ~290 KB per article."""
    with conn.cursor() as cur:
        if fields is None:
            cur.execute(
                """
                SELECT DISTINCT ON (matnr) matnr, "json" FROM public.pim_egloakeneo_product
                WHERE matnr = ANY(%s) AND enabled = TRUE ORDER BY matnr, updated DESC
                """,
                (matnrs,),
            )
        else:
            cur.execute(
                """
                SELECT DISTINCT ON (p.matnr) p.matnr,
                       CASE WHEN p."json" IS NULL THEN NULL ELSE jsonb_build_object(
                           'updated', p."json"->'updated',
                           'values', COALESCE((SELECT jsonb_object_agg(k, v) FROM jsonb_each(p."json"->'values') AS e(k, v)
                                               WHERE lower(k) = ANY(%s)), '{}'::jsonb)) END
                FROM public.pim_egloakeneo_product p
                WHERE p.matnr = ANY(%s) AND p.enabled = TRUE ORDER BY p.matnr, p.updated DESC
                """,
                (sorted(f.lower() for f in fields), matnrs),
            )
        return dict(cur.fetchall())


def get_dam_assets(conn: psycopg.Connection, matnrs: list[str]) -> dict[str, dict[str, dict]]:
    """First variant per matnr and asset code: {matnr: {asset_code: {url, filename, filename_uuid}}}."""
    out: dict[str, dict[str, dict]] = {}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT ON (matnr, asset_code) matnr, asset_code, url_default_resolution, filename_for_url,
                   filename_for_url_uuid
            FROM public.dam_cdh_product_assets_view
            WHERE matnr = ANY(%s) AND url_default_resolution IS NOT NULL
            ORDER BY matnr, asset_code, variant_no ASC NULLS LAST
            """,
            (matnrs,),
        )
        for matnr, code, url, fn, fn_uuid in cur.fetchall():
            out.setdefault(matnr, {})[code] = {"url": url, "filename": fn, "filename_uuid": fn_uuid}
    return out


def get_eprel_links(conn: psycopg.Connection, matnrs: list[str]) -> dict[str, dict[str, str]]:
    """EPREL energy label (SVG) and datasheet (DE) URL per matnr."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT ON (matnr) matnr, "EPREL_EnergyLabelBigColorSvgURL", "EPREL_Datasheet_DE_URL"
            FROM public.eprel_view WHERE matnr = ANY(%s) ORDER BY matnr
            """,
            (matnrs,),
        )
        return {m: {k: v for k, v in (("energy_label", svg), ("datasheet", ds)) if v}
                for m, svg, ds in cur.fetchall()}


def get_prices(conn: psycopg.Connection, matnrs: list[str], vkorg: str, vtweg: str, spart: str,
               kunnr: str, datasource: str) -> dict[str, dict]:
    """Prices from c_PriceData per matnr (datasource 'CSV' or 'SAP')."""
    columns = ", ".join(f'"{c}"' for c in PRICE_COLUMNS)
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT matnr, {columns} FROM public.c_pricedata
            WHERE vkorg = %s AND vtweg = %s AND spart = %s AND kunnr = %s
              AND upper(datasource) = upper(%s) AND matnr = ANY(%s)
            """,
            (vkorg, vtweg, spart, kunnr, datasource, matnrs),
        )
        return {row[0]: dict(zip(PRICE_COLUMNS, row[1:])) for row in cur.fetchall()}


# ---------- SAP HANA ----------

def _in_clause(values: list[str]) -> str:
    return ", ".join("?" for _ in values)


def get_mara_packaging(conn, matnrs: list[str]) -> dict[str, dict]:
    """EAN and packaging data from MARA per matnr. ZZEV* = single unit packaging
    (mm), ZZMB* = master box (mm), ZZMBSTU = pieces per master box, BRGEW/NTGEW/
    ZZMBGEW in GEWEI (kg), VOLUM in VOLEH (usually CDM = dm3)."""
    keys = ("ean", "ev_length", "ev_width", "ev_depth", "gross_weight", "net_weight", "weight_unit",
            "volume", "volume_unit", "mb_quantity", "mb_length", "mb_width", "mb_depth", "mb_weight")
    cur = conn.cursor()
    cur.execute(
        "SELECT MATNR, EAN11, ZZEVLAE, ZZEVBRE, ZZEVTIE, BRGEW, NTGEW, GEWEI, VOLUM, VOLEH, ZZMBSTU, "
        f"ZZMBLAE, ZZMBBRE, ZZMBTIE, ZZMBGEW FROM MARA WHERE MATNR IN ({_in_clause(matnrs)})",
        tuple(matnrs),
    )
    return {row[0]: dict(zip(keys, row[1:])) for row in cur.fetchall()}


def get_marc_origin(conn, matnrs: list[str], werks: str) -> dict[str, dict]:
    cur = conn.cursor()
    cur.execute(
        f"SELECT MATNR, HERKL, STAWN FROM MARC WHERE WERKS = ? AND MATNR IN ({_in_clause(matnrs)})",
        (werks, *matnrs),
    )
    return {m: {"country_of_origin": herkl, "customs_number": stawn} for m, herkl, stawn in cur.fetchall()}


def get_buyer(conn, kunnr: str) -> Optional[dict]:
    """Customer name and GLN (KNA1.BBBNR + BBSNR + BUBKZ, all zeros = no GLN)."""
    cur = conn.cursor()
    cur.execute("SELECT NAME1, BBBNR, BBSNR, BUBKZ FROM KNA1 WHERE KUNNR = ?", (kunnr.zfill(10),))
    row = cur.fetchone()
    if row is None:
        return None
    gln = "".join((p or "").strip() for p in row[1:4])
    return {"name": row[0], "gln": gln if gln.strip("0") and len(gln) == 13 else None}


def get_sales_org(conn, vkorg: str) -> Optional[dict]:
    """Currency (TVKO) and company name / VAT id (T001) of a sales organisation."""
    cur = conn.cursor()
    cur.execute(
        "SELECT v.WAERS, t.BUTXT, t.STCEG FROM TVKO v JOIN T001 t ON t.BUKRS = v.BUKRS WHERE v.VKORG = ?",
        (vkorg,),
    )
    row = cur.fetchone()
    return {"currency": row[0], "company_name": row[1], "vat_id": row[2]} if row else None
