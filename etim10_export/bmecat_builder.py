"""
Builds the ETIM BMEcat 5.0 XML (T_NEW_CATALOG) from plain dicts prepared by
pipeline.py - no DB access here, so it's unit-testable on its own.

Element order follows bmecat_etim_501.xsd / the Lobster profile
"BME_CAT_ETIM10_Export - XML Generierung" (version 17).
"""
from decimal import Decimal, InvalidOperation
from typing import Optional

from lxml import etree

NS = "https://www.etim-international.com/bmecat/50"
XSI = "http://www.w3.org/2001/XMLSchema-instance"


def _el(parent: etree._Element, tag: str, text=None, **attrs) -> etree._Element:
    el = etree.SubElement(parent, f"{{{NS}}}{tag}", {k: v for k, v in attrs.items() if v is not None})
    if text is not None:
        el.text = str(text)
    return el


def format_decimal(value, places: Optional[int] = None) -> Optional[str]:
    """Decimal text without trailing zeros ('10.00' -> '10', 0.5 -> '0.5');
    None for empty/non-numeric input."""
    if value is None or value == "":
        return None
    try:
        number = Decimal(str(value).replace(",", "."))
    except InvalidOperation:
        return None
    if places is not None:
        number = round(number, places)
    text = format(number.normalize(), "f")
    return "0" if text in ("-0", "") else text


def build_header(root: etree._Element, header: dict) -> None:
    head = _el(root, "HEADER")
    _el(head, "GENERATOR_INFO", header["generator_info"])

    catalog = _el(head, "CATALOG")
    for i, lang in enumerate(header["languages"]):
        _el(catalog, "LANGUAGE", lang, default="true" if i == 0 else None)
    _el(catalog, "CATALOG_ID", "ETIM-BMEcat-5.0")
    _el(catalog, "CATALOG_VERSION", "01.01")
    _el(catalog, "CATALOG_NAME", f"{header['supplier']['name']} BMEcat Version 5.0", lang=header["languages"][0])
    _el(_el(catalog, "DATETIME", type="generation_date"), "DATE", header["generation_date"])
    if header.get("currency"):
        _el(catalog, "CURRENCY", header["currency"])
    _el(catalog, "MIME_ROOT", "https://www.eglo.com/", lang=header["languages"][0])

    buyer = header["buyer"]
    buyer_el = _el(head, "BUYER")
    if buyer.get("gln"):
        _el(buyer_el, "BUYER_ID", buyer["gln"], type="gln")
    _el(buyer_el, "BUYER_ID", buyer["kunnr"], type="buyer_specific")
    _el(buyer_el, "BUYER_NAME", buyer.get("name") or buyer["kunnr"])

    supplier = header["supplier"]
    supplier_el = _el(head, "SUPPLIER")
    if supplier.get("gln"):
        _el(supplier_el, "SUPPLIER_ID", supplier["gln"], type="gln")
    _el(supplier_el, "SUPPLIER_NAME", supplier["name"])
    address = _el(supplier_el, "ADDRESS", type="supplier")
    _el(address, "CONTACT", supplier["contact"])
    if supplier.get("vat_id"):
        _el(address, "VAT_ID", supplier["vat_id"])
    _el(address, "EMAIL", supplier["email"])
    _el(address, "URL", supplier["url"])
    mime = _el(_el(supplier_el, "MIME_INFO"), "MIME")
    _el(mime, "MIME_SOURCE", supplier["logo_url"])
    _el(mime, "MIME_DESCR", "MD18")  # MD18 = company logo

    _el(_el(head, "USER_DEFINED_EXTENSIONS"), "UDX.EDXF.VERSION", "5.0")


def build_product(parent: etree._Element, p: dict) -> etree._Element:
    product = _el(parent, "PRODUCT", mode="new")
    _el(product, "SUPPLIER_PID", p["matnr"])

    details = _el(product, "PRODUCT_DETAILS")
    for lang, text in p["descriptions_short"].items():
        _el(details, "DESCRIPTION_SHORT", text, lang=lang)
    for lang, text in p.get("descriptions_long", {}).items():
        _el(details, "DESCRIPTION_LONG", text, lang=lang)
    if p.get("gtin"):
        _el(details, "INTERNATIONAL_PID", p["gtin"], type="gtin")
    _el(details, "MANUFACTURER_PID", p["matnr"])
    _el(details, "MANUFACTURER_NAME", "Eglo")
    _el(details, "MANUFACTURER_TYPE_DESCR", p["matnr"])
    _el(details, "SPECIAL_TREATMENT_CLASS", "NONE", type="NOT_RELEVANT")
    for lang, words in p.get("keywords", {}).items():
        for word in words:
            _el(details, "KEYWORD", word, lang=lang)
    _el(details, "PRODUCT_TYPE", "physical")

    features = _el(product, "PRODUCT_FEATURES")
    _el(features, "REFERENCE_FEATURE_SYSTEM_NAME", "ETIM-10.0")
    _el(features, "REFERENCE_FEATURE_GROUP_ID", p["class_id"])
    for f in p.get("features", []):
        feature = _el(features, "FEATURE")
        _el(feature, "FNAME", f["feature_id"])
        for value in f["values"]:
            _el(feature, "FVALUE", value)
        if f.get("unit_id"):
            _el(feature, "FUNIT", f["unit_id"])

    quantity = p.get("pieces_per_box") or 1
    order = _el(product, "PRODUCT_ORDER_DETAILS")
    _el(order, "ORDER_UNIT", "C62")
    _el(order, "CONTENT_UNIT", "C62")
    _el(order, "NO_CU_PER_OU", format_decimal(quantity))
    _el(order, "PRICE_QUANTITY", "1")
    _el(order, "QUANTITY_MIN", format_decimal(quantity))
    _el(order, "QUANTITY_INTERVAL", format_decimal(quantity))

    # Always written, like Lobster: without a price only the start date - not
    # XSD-valid (PRODUCT_PRICE is mandatory), decided with the user 2026-10-02
    # since that's what customers receive today (c_PriceData has no prices).
    prices = p.get("prices") or {"amounts": []}
    price_details = _el(product, "PRODUCT_PRICE_DETAILS")
    _el(_el(price_details, "DATETIME", type="valid_start_date"), "DATE", p["price_date"])
    for price_type, amount in prices["amounts"]:
        price = _el(price_details, "PRODUCT_PRICE", price_type=price_type)
        _el(price, "PRICE_AMOUNT", amount)
        if prices.get("currency"):
            _el(price, "PRICE_CURRENCY", prices["currency"])
        if prices.get("tax") is not None:
            _el(price, "TAX", prices["tax"])

    udx = _el(product, "USER_DEFINED_EXTENSIONS")
    if p.get("mimes"):
        mime_info = _el(udx, "UDX.EDXF.MIME_INFO")
        for m in p["mimes"]:
            mime = _el(mime_info, "UDX.EDXF.MIME")
            _el(mime, "UDX.EDXF.MIME_SOURCE", m["source"])
            _el(mime, "UDX.EDXF.MIME_CODE", m["code"])
            if m.get("filename"):
                _el(mime, "UDX.EDXF.MIME_FILENAME", m["filename"])
    if p.get("series"):
        _el(udx, "UDX.EDXF.PRODUCT_SERIES", p["series"], lang=p["series_lang"])
    if p.get("packing_unit"):
        pu = p["packing_unit"]
        unit = _el(_el(udx, "UDX.EDXF.PACKING_UNITS"), "UDX.EDXF.PACKING_UNIT")
        _el(unit, "UDX.EDXF.QUANTITY_MIN", format_decimal(pu["quantity"]))
        _el(unit, "UDX.EDXF.QUANTITY_MAX", format_decimal(pu["quantity"]))
        _el(unit, "UDX.EDXF.PACKING_UNIT_CODE", pu["code"])
        for key, tag in (("volume", "VOLUME"), ("weight", "WEIGHT"), ("length", "LENGTH"),
                         ("width", "WIDTH"), ("depth", "DEPTH")):
            if pu.get(key) is not None:
                _el(unit, f"UDX.EDXF.{tag}", pu[key])
        if pu.get("gtin"):
            _el(unit, "UDX.EDXF.GTIN", pu["gtin"])
    if p.get("logistic"):
        lg = _el(udx, "UDX.EDXF.PRODUCT_LOGISTIC_DETAILS")
        for key, tag in (("volume", "NETVOLUME"), ("weight", "NETWEIGHT"), ("length", "NETLENGTH"),
                         ("width", "NETWIDTH"), ("depth", "NETDEPTH")):
            if p["logistic"].get(key) is not None:
                _el(lg, f"UDX.EDXF.{tag}", p["logistic"][key])
    if p.get("battery_contained") is not None:
        _el(udx, "UDX.EDXF.BATTERY_CONTAINED", "true" if p["battery_contained"] else "false")
    _el(udx, "UDX.EDXF.CE_MARKING", "true" if p.get("ce_marking") else "false")
    if len(udx) == 0:
        product.remove(udx)

    if p.get("customs_number") or p.get("country_of_origin"):
        logistic = _el(product, "PRODUCT_LOGISTIC_DETAILS")
        if p.get("customs_number"):
            _el(_el(logistic, "CUSTOMS_TARIFF_NUMBER"), "CUSTOMS_NUMBER", p["customs_number"])
        if p.get("country_of_origin"):
            _el(logistic, "COUNTRY_OF_ORIGIN", p["country_of_origin"])
    return product


def build_document(header: dict, products: list[dict]) -> bytes:
    root = etree.Element(f"{{{NS}}}BMECAT", nsmap={None: NS, "xsi": XSI}, version="2005")
    root.set(f"{{{XSI}}}schemaLocation", f"{NS} https://www.etim-international.com/bmecat_etim_50.xsd")
    build_header(root, header)
    catalog = _el(root, "T_NEW_CATALOG")
    for p in products:
        build_product(catalog, p)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)
