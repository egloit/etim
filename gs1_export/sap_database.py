"""
SAP HANA access for the GS1 export pipeline - for data that isn't in the
PIM (delivery/purchasing quantities, customs/country-of-origin, tax info,
etc.). Connection parameters are read from environment variables (see
.env.example).
"""
import logging
import os
from typing import Optional

from hdbcli import dbapi
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

HANA_HOST = os.getenv("HANA_HOST", "")
HANA_PORT = int(os.getenv("HANA_PORT", "30215"))
HANA_DBNAME = os.getenv("HANA_DBNAME", "")
HANA_SCHEMA = os.getenv("HANA_SCHEMA", "")
HANA_USER = os.getenv("HANA_USER", "")
HANA_PASSWORD = os.getenv("HANA_PASSWORD", "")


def get_connection() -> dbapi.Connection:
    # No databaseName param: HANA_PORT is already the tenant's own SQL port
    # (not the SYSTEMDB/MDC routing port), passing databaseName here fails
    # with "database '<name>' not connected".
    return dbapi.connect(
        address=HANA_HOST,
        port=HANA_PORT,
        user=HANA_USER,
        password=HANA_PASSWORD,
        currentSchema=HANA_SCHEMA or None,
    )


def get_material_measurements(conn: dbapi.Connection, matnr: str) -> Optional[dict]:
    """Weight/dimensions from MARA (General Material Data), one row per
    material. Reliably has BRGEW/NTGEW/GEWEI (weight); LAENG/BREIT/HOEHE
    (dimensions) are frequently 0/unset in practice - see pipeline.py, which
    treats a 0 dimension as "not available" rather than sending it as-is."""
    cur = conn.cursor()
    cur.execute(
        "SELECT BRGEW, NTGEW, GEWEI, LAENG, BREIT, HOEHE, MEABM, VOLUM, VOLEH "
        "FROM MARA WHERE MATNR = ?",
        (matnr,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return {
        "gross_weight": row[0], "net_weight": row[1], "weight_unit": row[2],
        "length": row[3], "width": row[4], "height": row[5], "dimension_unit": row[6],
        "volume": row[7], "volume_unit": row[8],
    }


def get_material_descriptions(conn: dbapi.Connection, matnr: str) -> dict[str, str]:
    """Material description texts (MAKT), keyed by SAP's own language code
    (SPRAS, e.g. "D"/"E"/"F"/"N" - not ISO) - see
    gs1_export/sap_language_mapping.json for the SPRAS -> GS1 languageCode
    translation. Many SPRAS rows are just a copy of the German text rather
    than a real translation - not something we can detect/fix here."""
    cur = conn.cursor()
    cur.execute("SELECT SPRAS, MAKTX FROM MAKT WHERE MATNR = ?", (matnr,))
    return {row[0]: row[1] for row in cur.fetchall() if row[1]}


def get_material_origin(conn: dbapi.Connection, matnr: str, werks: str) -> Optional[dict]:
    """Country of origin (HERKL) and customs tariff number (STAWN) from MARC
    (Plant Data), which is plant-specific (WERKS) - the same matnr can have a
    different HERKL per plant (e.g. most EGLO plants "IN", one seen "CN")."""
    cur = conn.cursor()
    cur.execute(
        "SELECT HERKL, STAWN FROM MARC WHERE MATNR = ? AND WERKS = ?",
        (matnr, werks),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return {"country_of_origin": row[0], "customs_tariff_number": row[1]}
