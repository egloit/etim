"""
PostgreSQL access for the GS1 export pipeline.
Connection parameters are read from environment variables (see .env.example).
"""
import logging
import os
from typing import Optional

import psycopg
from psycopg.types.json import Jsonb
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

PGHOST = os.getenv("PGHOST", "")
PGPORT = os.getenv("PGPORT", "5432")
PGDATABASE = os.getenv("PGDATABASE", "")
PGUSER = os.getenv("PGUSER", "")
PGPASSWORD = os.getenv("PGPASSWORD", "")


def get_connection() -> psycopg.Connection:
    # autocommit: every call here is a read-only SELECT, and it lets
    # get_gdsn_attribute_type() tolerate a not-yet-created reference table
    # without leaving the connection stuck in an aborted transaction.
    return psycopg.connect(
        host=PGHOST,
        port=PGPORT,
        dbname=PGDATABASE,
        user=PGUSER,
        password=PGPASSWORD,
        autocommit=True,
    )


def get_product_by_matnr(conn: psycopg.Connection, matnr: str) -> Optional[dict]:
    """Return the PIM 'json' column (already a dict via psycopg's jsonb adapter)
    for the newest enabled row matching *matnr*, or None if not found.

    Ordered by "updated" (an ISO-8601 string, sorts correctly lexicographically),
    not "row_timestamp" - that column is bytea and observed to be NULL on at
    least some duplicate-matnr rows, so it can't reliably pick the latest one."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT "json"
            FROM public.pim_egloakeneo_product
            WHERE matnr = %s AND enabled = TRUE
            ORDER BY updated DESC
            LIMIT 1
            """,
            (matnr,),
        )
        row = cur.fetchone()
        return row[0] if row else None


def get_product_updated_at(conn: psycopg.Connection, matnr: str) -> Optional[str]:
    """Return the "updated" timestamp string of the newest enabled PIM row for
    *matnr* - used to snapshot "PIM state at export time" in
    gs1_export_history, see upsert_export_history()/get_changed_articles()."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT updated
            FROM public.pim_egloakeneo_product
            WHERE matnr = %s AND enabled = TRUE
            ORDER BY updated DESC
            LIMIT 1
            """,
            (matnr,),
        )
        row = cur.fetchone()
        return row[0] if row else None


def get_brick_id(conn: psycopg.Connection, zztypen_code: str) -> Optional[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT brick_id
            FROM public.gs1_brick_mapping
            WHERE zztyp = %s AND active = TRUE
            LIMIT 1
            """,
            (zztypen_code,),
        )
        row = cur.fetchone()
        return row[0] if row else None


def get_active_mappings(conn: psycopg.Connection, brick_id: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT pick_id, pim_field
            FROM public.gs1_mapping
            WHERE brick_id = %s AND active = TRUE
            ORDER BY pick_id
            """,
            (brick_id,),
        )
        return [{"pickid": r[0], "pimfeld": r[1]} for r in cur.fetchall()]


def get_gdsn_attribute_type(conn: psycopg.Connection, brick_id: str, pick_id: str) -> Optional[str]:
    """Look up the GDSN element type (propertyDescription/propertyCode/
    propertyMeasurement/propertyInteger/...) for a Brick+Pick from GS1's GPC
    attribute reference data (public.gs1_gpc_attribute_types). Returns None if
    that Brick+Pick combination hasn't been loaded into the table yet, or if
    the table itself doesn't exist yet (tolerated so the export keeps working
    with the old propertyDescription-for-everything fallback until this
    reference data is populated)."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT gdsn_attribute_name
                FROM public.gs1_gpc_attribute_types
                WHERE brick_id = %s AND pick_id = %s AND active = TRUE
                LIMIT 1
                """,
                (brick_id, pick_id),
            )
            row = cur.fetchone()
            return row[0] if row else None
    except psycopg.errors.UndefinedTable:
        logger.warning("gs1_gpc_attribute_types existiert noch nicht - GDSN-Typ nicht bestimmbar.")
        return None


def get_measurement_unit_code(conn: psycopg.Connection, brick_id: str, pick_id: str) -> Optional[str]:
    """Look up the fixed GS1 UN/CEFACT measurementUnitCode (e.g. "WTT" for
    watt) for a propertyMeasurement Pick, from
    public.gs1_gpc_attribute_types.measurement_unit_code. Tolerant of the
    column not existing yet, same reasoning as get_gdsn_attribute_type()."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT measurement_unit_code
                FROM public.gs1_gpc_attribute_types
                WHERE brick_id = %s AND pick_id = %s AND active = TRUE
                LIMIT 1
                """,
                (brick_id, pick_id),
            )
            row = cur.fetchone()
            return row[0] if row else None
    except (psycopg.errors.UndefinedTable, psycopg.errors.UndefinedColumn):
        logger.warning(
            "gs1_gpc_attribute_types.measurement_unit_code existiert noch nicht - "
            "measurementUnitCode nicht bestimmbar."
        )
        return None


_CROSSWALK_WILDCARD = "*"


def get_pim_value_crosswalk_exact(conn: psycopg.Connection, pick_id: str, pim_code: str) -> Optional[str]:
    """Exact (pick_id, pim_code) lookup in public.gs1_pim_value_crosswalk, no
    wildcard fallback. Used to check several candidate codes (multiselect
    fields) before falling back to the wildcard once for all of them - see
    get_pim_value_crosswalk_wildcard()."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT code_value
                FROM public.gs1_pim_value_crosswalk
                WHERE pick_id = %s AND pim_code = %s AND active = TRUE
                LIMIT 1
                """,
                (pick_id, pim_code),
            )
            row = cur.fetchone()
            return row[0] if row else None
    except psycopg.errors.UndefinedTable:
        logger.warning("gs1_pim_value_crosswalk existiert noch nicht - PIM-Code kann nicht uebersetzt werden.")
        return None


def get_pim_value_crosswalk_wildcard(conn: psycopg.Connection, pick_id: str) -> Optional[str]:
    """Wildcard (pim_code = "*") row for a Pick - the "else <default>" business
    rule (e.g. Pick 4.364: three specific ZZTYPEN codes -> "buiten", every
    other code -> "binnen") without having to enumerate every possible PIM
    code by hand."""
    return get_pim_value_crosswalk_exact(conn, pick_id, _CROSSWALK_WILDCARD)


def get_pim_catalog_text_value(
    conn: psycopg.Connection, matnr: str, attribute_code: str, locale: str
) -> Optional[str]:
    """Look up a per-locale free-text PIM value from
    public.pim_egloakeneo_product_values_pim_catalog_text (a separate table
    from pim_egloakeneo_product's json blob - one row per matnr+attribute_code
    +locale). Used for e.g. MAKTX (SAP material short text), the placeholder
    source for tradeItemMarketingMessage until a real marketing-copy field is
    confirmed (2026-09-03, see gs1_mapping notes).

    Missing/untranslated values are stored as the literal string '[]' (both
    scope and data columns), not NULL - treated as "no value" here. Present
    values have escaped forward slashes (e.g. "WL\\/1 E27...") that get
    unescaped before returning. Tolerant of the table not existing, same
    pattern as the other lookups."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT data
                FROM public.pim_egloakeneo_product_values_pim_catalog_text
                WHERE matnr = %s AND attribute_code = %s AND locale = %s
                LIMIT 1
                """,
                (matnr, attribute_code, locale),
            )
            row = cur.fetchone()
            if not row or not row[0] or row[0] in ("[]", "null"):
                return None
            return row[0].replace("\\/", "/")
    except psycopg.errors.UndefinedTable:
        logger.warning(
            "pim_egloakeneo_product_values_pim_catalog_text existiert noch nicht - "
            "Text nicht bestimmbar."
        )
        return None


def get_pim_catalog_textarea_value(
    conn: psycopg.Connection, matnr: str, attribute_code: str, locale: str
) -> Optional[str]:
    """Same shape as get_pim_catalog_text_value(), but against
    public.pim_egloakeneo_product_values_pim_catalog_textarea - PIM's
    textarea (multi-line) fields live in a separate table from its single-
    line pim_catalog_text fields. Used for PIM_ARTIKELTEXT, confirmed
    2026-09-23 as the real tradeItemMarketingMessage source, replacing the
    MAKTX placeholder (see gs1_exporter.build_marketing_information_module_
    element's docstring)."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT data
                FROM public.pim_egloakeneo_product_values_pim_catalog_textarea
                WHERE matnr = %s AND attribute_code = %s AND locale = %s
                LIMIT 1
                """,
                (matnr, attribute_code, locale),
            )
            row = cur.fetchone()
            if not row or not row[0] or row[0] in ("[]", "null"):
                return None
            return row[0].replace("\\/", "/")
    except psycopg.errors.UndefinedTable:
        logger.warning(
            "pim_egloakeneo_product_values_pim_catalog_textarea existiert noch nicht - "
            "Text nicht bestimmbar."
        )
        return None


def get_primary_product_image(conn: psycopg.Connection, matnr: str) -> Optional[dict]:
    """Look up the main product photo from public.dam_cdh_product_assets_view
    for referencedFileDetailInformationModule. Scoped to asset_code='101'
    (asset_code_name 'VIEW') and asset_type='IMAGE' only - the user decided
    2026-09-23 to ship just the primary product shot for now, not the other
    asset types seen on the view (DETAIL/DIMENSION/AMBIENT images, 360 VIDEO)
    - those need their own referencedFileTypeCode decisions, parked for
    later. url_default_resolution is a real, directly usable URL (unlike the
    "NB" placeholder seen in the real GS1-portal reference export). Tolerant
    of the view not existing yet, same pattern as the other lookups."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT url_default_resolution, filename_for_url
                FROM public.dam_cdh_product_assets_view
                WHERE matnr = %s AND asset_code = '101' AND asset_type = 'IMAGE'
                LIMIT 1
                """,
                (matnr,),
            )
            row = cur.fetchone()
            if not row or not row[0] or not row[1]:
                return None
            return {"url": row[0], "filename": row[1]}
    except psycopg.errors.UndefinedTable:
        logger.warning("dam_cdh_product_assets_view existiert noch nicht - Produktbild nicht bestimmbar.")
        return None


def get_manual_property_value(conn: psycopg.Connection, matnr: str, pick_id: str) -> Optional[str]:
    """Look up a manually-maintained property value for a matnr+Pick from
    public.gs1_manual_property_values - used for GDSN attributes GS1 itself
    documents as "NOT IN PIM" (e.g. Recupel/Bebat/Stibat/Wecycle recycling
    codes), which have no source PIM field to translate via
    gs1_pim_value_crosswalk at all. Tolerant of the table not existing yet,
    same pattern as the other lookups."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT value
                FROM public.gs1_manual_property_values
                WHERE matnr = %s AND pick_id = %s AND active = TRUE
                LIMIT 1
                """,
                (matnr, pick_id),
            )
            row = cur.fetchone()
            return row[0] if row else None
    except psycopg.errors.UndefinedTable:
        logger.warning("gs1_manual_property_values existiert noch nicht - manueller Wert nicht bestimmbar.")
        return None


def get_pim_value_crosswalk(conn: psycopg.Connection, pick_id: str, pim_code: str) -> Optional[str]:
    """Translate a raw PIM select code (e.g. "ZZTYPEN_WAL") into the GS1
    propertyCode value for a specific Pick, via public.gs1_pim_value_crosswalk.
    The same pim_code can map to a different code_value for different Picks
    (e.g. ZZTYPEN feeds several unrelated Picks), so both are part of the key.

    Exact match first, then the wildcard fallback. For multiselect PIM fields
    with several candidate codes, use get_pim_value_crosswalk_exact() per
    code first and only call get_pim_value_crosswalk_wildcard() once none of
    them matched - see pipeline.py.

    Returns None if untranslated (no exact match and no wildcard row either),
    or if the table doesn't exist yet."""
    value = get_pim_value_crosswalk_exact(conn, pick_id, pim_code)
    if value is not None:
        return value
    return get_pim_value_crosswalk_wildcard(conn, pick_id)


def upsert_export_history(
    conn: psycopg.Connection,
    matnr: str,
    brick_id: Optional[str],
    pim_updated_at_export: Optional[str],
    exported_by: Optional[str],
    value_snapshot: Optional[dict] = None,
) -> None:
    """Record/refresh "this matnr was GS1-exported, PIM's updated timestamp was
    X at that time" in public.gs1_export_history - one row per matnr, latest
    export wins. Used later by get_changed_articles() to flag articles whose
    PIM data has changed since. Tolerant of the table not existing yet, same
    pattern as the other lookups (a missing table here must never abort an
    otherwise-successful export)."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.gs1_export_history
                    (matnr, brick_id, exported_at, pim_updated_at_export, exported_by, value_snapshot)
                VALUES (%s, %s, now(), %s, %s, %s)
                ON CONFLICT (matnr) DO UPDATE SET
                    brick_id = EXCLUDED.brick_id,
                    exported_at = EXCLUDED.exported_at,
                    pim_updated_at_export = EXCLUDED.pim_updated_at_export,
                    exported_by = EXCLUDED.exported_by,
                    value_snapshot = EXCLUDED.value_snapshot
                """,
                (matnr, brick_id, pim_updated_at_export, exported_by,
                 Jsonb(value_snapshot) if value_snapshot is not None else None),
            )
    except psycopg.errors.UndefinedTable:
        logger.warning("gs1_export_history existiert noch nicht - Export-Historie wird nicht gespeichert.")


def get_changed_articles(conn: psycopg.Connection) -> list[dict]:
    """Articles that were GS1-exported before but whose PIM data has since
    changed (current "updated" timestamp differs from the one snapshotted at
    export time in gs1_export_history). Coarse/timestamp-only check - flags
    "something changed", not which field. A LATERAL join keyed on the already
    indexed matnr column keeps this fast even though pim_egloakeneo_product
    has ~110k rows, since it only ever looks at the (small) set of matnrs
    that are actually in the history table.

    Tolerant of gs1_export_history not existing yet (returns [])."""
    try:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = '5s'")
            cur.execute(
                """
                SELECT h.matnr, h.exported_at, h.pim_updated_at_export, latest.updated,
                       h.value_snapshot IS NOT NULL
                FROM public.gs1_export_history h
                JOIN LATERAL (
                    SELECT updated
                    FROM public.pim_egloakeneo_product p
                    WHERE p.matnr = h.matnr AND p.enabled = TRUE
                    ORDER BY p.updated DESC
                    LIMIT 1
                ) latest ON TRUE
                WHERE latest.updated IS DISTINCT FROM h.pim_updated_at_export
                ORDER BY latest.updated DESC
                """
            )
            return [
                {
                    "matnr": r[0],
                    "exported_at": r[1].isoformat() if r[1] else None,
                    "pim_updated_at_export": r[2],
                    "pim_updated_now": r[3],
                    "has_details": r[4],
                }
                for r in cur.fetchall()
            ]
    except psycopg.errors.UndefinedTable:
        logger.warning("gs1_export_history existiert noch nicht - keine Aenderungspruefung moeglich.")
        return []


def get_export_snapshot(conn: psycopg.Connection, matnr: str) -> Optional[dict]:
    """value_snapshot of the last GS1 export of *matnr* ({"params", "values"}), or None."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT brick_id, value_snapshot FROM public.gs1_export_history WHERE matnr = %s", (matnr,)
        )
        row = cur.fetchone()
    return {"brick_id": row[0], **row[1]} if row and row[1] else None


def get_pick_names(conn: psycopg.Connection, brick_id: str) -> dict[str, str]:
    """{"pick:4.015": "Colour Family", ...} for readable change lists."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT pick_id, attribute_name FROM public.gs1_gpc_attribute_types WHERE brick_id = %s",
            (brick_id,),
        )
        return {f"pick:{pick}": f"{pick} {name}" for pick, name in cur.fetchall() if name}


def save_export_file(
    conn: psycopg.Connection,
    filename: str,
    exported_by: Optional[str],
    matnrs: list[str],
    xml_bytes: bytes,
) -> None:
    """Persist a generated GS1 XML file in public.gs1_export_files so the user
    who generated it can find/re-download it later (see list_export_files()/
    get_export_file()) - the browser download itself is one-shot, nothing
    keeps the file around otherwise. Tolerant of the table not existing yet,
    same pattern as the other lookups - a missing table must never break the
    actual export/download."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.gs1_export_files
                    (filename, exported_by, matnrs, article_count, xml_content)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (filename, exported_by, matnrs, len(matnrs), xml_bytes),
            )
    except psycopg.errors.UndefinedTable:
        logger.warning("gs1_export_files existiert noch nicht - Datei wird nicht archiviert.")


def list_export_files(conn: psycopg.Connection, exported_by: str) -> list[dict]:
    """Files previously generated by *exported_by*, newest first. Scoped to
    one user - see get_export_file() for the matching ownership check on
    download. Tolerant of the table not existing yet (returns [])."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, filename, exported_at, article_count, matnrs
                FROM public.gs1_export_files
                WHERE exported_by = %s
                ORDER BY exported_at DESC
                """,
                (exported_by,),
            )
            return [
                {
                    "id": r[0],
                    "filename": r[1],
                    "exported_at": r[2].isoformat() if r[2] else None,
                    "article_count": r[3],
                    "matnrs": r[4],
                }
                for r in cur.fetchall()
            ]
    except psycopg.errors.UndefinedTable:
        logger.warning("gs1_export_files existiert noch nicht - keine Exportliste verfuegbar.")
        return []


def get_export_file(conn: psycopg.Connection, file_id: int, exported_by: str) -> Optional[dict]:
    """A single stored export file, scoped to *exported_by* so one user can't
    download another user's file by guessing/incrementing the id. Returns
    None if not found, not owned by this user, or the table doesn't exist."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT filename, xml_content
                FROM public.gs1_export_files
                WHERE id = %s AND exported_by = %s
                """,
                (file_id, exported_by),
            )
            row = cur.fetchone()
            return {"filename": row[0], "xml_content": row[1]} if row else None
    except psycopg.errors.UndefinedTable:
        logger.warning("gs1_export_files existiert noch nicht - Datei nicht abrufbar.")
        return None
