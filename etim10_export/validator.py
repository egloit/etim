"""XSD validation of the generated BMEcat against bmecat_etim_501.xsd (shipped in schema/)."""
from bisect import bisect_right
from functools import lru_cache
from pathlib import Path

from lxml import etree

_XSD_PATH = Path(__file__).with_name("schema") / "bmecat_etim_501.xsd"


@lru_cache(maxsize=1)
def _schema() -> etree.XMLSchema:
    return etree.XMLSchema(etree.parse(str(_XSD_PATH)))


def validate_xml(xml_bytes: bytes) -> list[dict]:
    """[] if valid, else one dict per XSD error (same shape as the GS1 validator,
    matnr attributed from the enclosing PRODUCT/SUPPLIER_PID where possible)."""
    schema = _schema()
    doc = etree.fromstring(xml_bytes)
    if schema.validate(doc):
        return []
    # PRODUCT start lines -> SUPPLIER_PID, to attribute each error to its article in one pass.
    ns = doc.nsmap[None]
    starts = sorted((p.sourceline, p.findtext(f"{{{ns}}}SUPPLIER_PID")) for p in doc.iter(f"{{{ns}}}PRODUCT"))
    lines = [line for line, _ in starts]
    errors = []
    for err in schema.error_log:
        i = bisect_right(lines, err.line) - 1
        matnr = starts[i][1] if i >= 0 else None
        errors.append({"matnr": matnr, "gtin": None, "element": err.path, "message": err.message, "pickid": None})
    return errors
