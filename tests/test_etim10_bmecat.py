from decimal import Decimal

from lxml import etree

from etim10_export import validator
from etim10_export.bmecat_builder import NS, build_document, format_decimal
from etim10_export.pipeline import _is_missing_price_error, _packaging, _prices, group_warnings

HEADER = {
    "generator_info": "test",
    "languages": ["ger", "eng"],
    "generation_date": "2026-10-02",
    "currency": "EUR",
    "buyer": {"kunnr": "0000208123", "name": "SONEPAR ÖSTERREICH GMBH", "gln": "9120074380158"},
    "supplier": {"name": "EGLO Leuchten GmbH", "gln": None, "vat_id": "ATU33096404", "contact": "ETIM Support Team",
                 "email": "sap.support@eglo.com", "url": "https://www.eglo.com", "logo_url": "https://www.eglo.com/logo.svg"},
}

MARA = {"ean": "9008606392759", "ev_length": 100, "ev_width": 310, "ev_depth": 310,
        "gross_weight": Decimal("3.3"), "net_weight": Decimal("2.9"), "weight_unit": "KG",
        "volume": Decimal("9.61"), "volume_unit": "CDM", "mb_quantity": 4,
        "mb_length": 328, "mb_width": 420, "mb_depth": 320, "mb_weight": Decimal("13.2")}


def _product(**overrides):
    quantity, packing_unit, logistic = _packaging(MARA)
    product = {
        "matnr": "902778",
        "price_date": "2026-10-02",
        "descriptions_short": {"ger": "LED-STRIPE 25M AUF ROLLE 'COLLEPASSO'"},
        "descriptions_long": {"ger": "Langtext"},
        "gtin": "9008606392759",
        "keywords": {"ger": ["LED-Band", "IP65"]},
        "class_id": "EC002706",
        "features": [
            {"feature_id": "EF000004", "values": ["EV000583"], "unit_id": None},
            {"feature_id": "EF009346", "values": ["2700", "2700"], "unit_id": "EU570076"},
        ],
        "pieces_per_box": quantity,
        "packing_unit": packing_unit,
        "logistic": logistic,
        "prices": _prices({"currency": "EUR", "VatRatePercentage": Decimal("20.00"),
                           "SuggestedRetailPriceIncludingVat": Decimal("189.90"),
                           "PurchasePriceExcludingVat": Decimal("0"), "SalesPriceExcludingVat": Decimal("80.5")}),
        "mimes": [{"source": "https://example.com/a.jpg", "code": "MD01", "filename": "902778_101_0001.jpg"}],
        "series": "COLLEPASSO", "series_lang": "ger",
        "battery_contained": False, "ce_marking": True,
        "customs_number": "9405423100", "country_of_origin": "CN",
    }
    product.update(overrides)
    return product


def test_format_decimal():
    assert format_decimal("10.00") == "10"
    assert format_decimal(Decimal("0.50")) == "0.5"
    assert format_decimal(0.0440832, 6) == "0.044083"
    assert format_decimal("") is None
    assert format_decimal("abc") is None


def test_packaging_master_box_uses_box_dimensions_and_m3():
    quantity, unit, logistic = _packaging(MARA)
    assert quantity == 4
    assert unit == {"code": "CT", "quantity": 4, "volume": "0.044083", "weight": "13.2",
                    "length": "0.328", "width": "0.42", "depth": "0.32"}
    # VOLUM 9.61 dm3 -> 0.00961 m3, net weight from NTGEW, single packaging dims in m
    assert logistic == {"volume": "0.00961", "weight": "2.9", "length": "0.1", "width": "0.31", "depth": "0.31"}


def test_packaging_single_piece():
    _, unit, _ = _packaging({**MARA, "mb_quantity": 0})
    assert unit["code"] == "PA" and unit["quantity"] == 1
    assert unit["weight"] == "3.3" and unit["gtin"] == "9008606392759"


def test_prices_only_positive_amounts_and_tax_as_factor():
    prices = _prices({"currency": "EUR", "VatRatePercentage": Decimal("20"),
                      "SuggestedRetailPriceIncludingVat": Decimal("189.90"),
                      "PurchasePriceExcludingVat": Decimal("0"), "SalesPriceExcludingVat": None})
    assert prices == {"amounts": [("nrp", "189.9")], "currency": "EUR", "tax": "0.2"}
    assert _prices({"currency": "EUR", "VatRatePercentage": None, "SuggestedRetailPriceIncludingVat": Decimal("0"),
                    "PurchasePriceExcludingVat": None, "SalesPriceExcludingVat": None}) is None


def test_document_is_xsd_valid():
    xml = build_document(HEADER, [_product()])
    assert validator.validate_xml(xml) == []

    doc = etree.fromstring(xml)
    ns = {"b": NS}
    assert doc.findtext(".//b:BUYER_ID[@type='gln']", namespaces=ns) == "9120074380158"
    assert doc.find(".//b:SUPPLIER_ID", ns) is None  # no GLN configured -> left out
    feature = doc.findall(".//b:FEATURE", ns)[1]
    assert [v.text for v in feature.findall("b:FVALUE", ns)] == ["2700", "2700"]
    assert feature.findtext("b:FUNIT", namespaces=ns) == "EU570076"
    assert [p.get("price_type") for p in doc.findall(".//b:PRODUCT_PRICE", ns)] == ["nrp", "net_customer"]
    assert doc.findtext(".//b:TAX", namespaces=ns) == "0.2"


def test_document_without_price_keeps_date_only_price_block():
    # Like Lobster (decided 2026-10-02): date-only price block, the only XSD error,
    # which the pipeline turns into one summary warning.
    xml = build_document(HEADER, [_product(prices=None)])
    doc = etree.fromstring(xml)
    assert doc.findtext(".//{%s}PRODUCT_PRICE_DETAILS/{%s}DATETIME/{%s}DATE" % (NS, NS, NS)) == "2026-10-02"
    errors = validator.validate_xml(xml)
    assert len(errors) == 1 and errors[0]["matnr"] == "902778"
    assert _is_missing_price_error(errors[0])


def test_group_warnings_collapses_repeated_causes():
    warnings = ["3 Artikel ohne Preis – Preisblock nur mit Datum.",
                "1: EF004282 (Light outlet): Wert 'EV003775' ist für EC001744 nicht zulässig – ausgelassen.",
                "2: EF004282 (Light outlet): Wert 'EV003775' ist für EC001744 nicht zulässig – ausgelassen.",
                "6EX08-EFRO-BLN: Zolltarifnummer in SAP (MARC, Werk 0090) leer."]
    assert group_warnings(warnings) == [
        "3 Artikel ohne Preis – Preisblock nur mit Datum.",
        "EF004282 (Light outlet): Wert 'EV003775' ist für EC001744 nicht zulässig – ausgelassen – 2 Artikel (z. B. 1, 2)",
        "6EX08-EFRO-BLN: Zolltarifnummer in SAP (MARC, Werk 0090) leer.",
    ]

