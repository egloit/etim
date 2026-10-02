"""XSD validation of the generated BMEcat against bmecat_etim_501.xsd (shipped in schema/)."""
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
    errors = []
    for err in schema.error_log:
        matnr = None
        for el in doc.iter():
            if el.sourceline == err.line:
                product = next((a for a in el.iterancestors() if a.tag.endswith("}PRODUCT")), None)
                if product is not None:
                    matnr = product.findtext(f"{{{product.nsmap[None]}}}SUPPLIER_PID")
                break
        errors.append({"matnr": matnr, "gtin": None, "element": err.path, "message": err.message, "pickid": None})
    return errors
