"""
XSD validation for the generated GS1 XML.

GS1_XSD_ROOT (env var) must point at the top-level/envelope XSD of the GDSN
schema package once the user has copied it into gs1_export/schema/. Until
then, validation is skipped with a clear warning instead of failing the
export.
"""
import logging
import os

from lxml import etree

logger = logging.getLogger(__name__)

GS1_XSD_ROOT = os.getenv("GS1_XSD_ROOT", "")

_schema: etree.XMLSchema | None = None
_schema_load_attempted = False


def _load_schema() -> etree.XMLSchema | None:
    global _schema, _schema_load_attempted
    if _schema_load_attempted:
        return _schema
    _schema_load_attempted = True
    if not GS1_XSD_ROOT:
        return None
    try:
        _schema = etree.XMLSchema(etree.parse(GS1_XSD_ROOT))
    except (OSError, etree.XMLSchemaParseError) as exc:
        logger.error("GS1 XSD ROOT konnte nicht geladen werden (%s): %s", GS1_XSD_ROOT, exc)
        _schema = None
    return _schema


def validate_xml(xml_bytes: bytes) -> list[dict]:
    """Validate *xml_bytes* against GS1_XSD_ROOT.

    Returns a list of {"matnr": None, "gtin": None, "element": str, "message": str,
    "pickid": None} dicts – empty if valid or if no schema is configured.
    matnr/gtin/pickid attribution requires walking the generated tree back to
    its source article, which isn't implemented yet (Phase 1 limitation) –
    they're included as None per the required error shape and can be filled
    in once the real envelope structure is finalised.
    """
    schema = _load_schema()
    if schema is None:
        return [{
            "matnr": None,
            "gtin": None,
            "element": None,
            "message": "GS1_XSD_ROOT nicht konfiguriert oder Schema nicht ladbar – Validierung übersprungen.",
            "pickid": None,
        }]

    doc = etree.fromstring(xml_bytes)
    if schema.validate(doc):
        return []

    return [
        {
            "matnr": None,
            "gtin": None,
            "element": err.path,
            "message": err.message,
            "pickid": None,
        }
        for err in schema.error_log
    ]
