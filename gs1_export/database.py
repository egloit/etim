"""
PostgreSQL access for the GS1 export pipeline.
Connection parameters are read from environment variables (see .env.example).
"""
import logging
import os
from typing import Optional

import psycopg
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
