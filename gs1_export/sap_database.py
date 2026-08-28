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
