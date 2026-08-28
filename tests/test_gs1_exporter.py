from lxml import etree

from gs1_export.gs1_exporter import (
    NS_CIN,
    NS_DUTY_FEE_TAX,
    NS_LIGHTING_DEVICE,
    NS_SBDH,
    build_catalogue_item_element,
    build_code_property_element,
    build_description_property_element,
    build_document,
    build_duty_fee_tax_information_module_element,
    build_integer_property_element,
    build_lighting_device_module_element,
    build_measurement_property_element,
    build_notification_element,
    build_string_property_element,
    build_trade_item_element,
)


def test_build_trade_item_element_structure():
    properties = [
        build_description_property_element("4.014", {"en": "White", "fr": "Blanc"}),
        build_description_property_element("4.015", {"en": "cappuccino, gold"}),
    ]
    item = build_trade_item_element(
        matnr="62053",
        gtin="09002759620530",
        gpc_category_code="10000552",
        properties=properties,
        sender_gln="8719333022437",
        sender_party_name="EGLO",
        target_market_country_code="056",
    )

    assert item.find("gtin").text == "09002759620530"
    assert item.find("additionalTradeItemIdentification").text == "62053"
    assert item.find("brandOwner/gln").text == "8719333022437"
    assert item.find("informationProviderOfTradeItem/gln").text == "8719333022437"
    assert item.find("gdsnTradeItemClassification/gpcCategoryCode").text == "10000552"
    assert item.find("targetMarket/targetMarketCountryCode").text == "056"
    # tradeItemSynchronisationDates is minOccurs="1" in TradeItem.xsd - must always be present.
    assert item.find("tradeItemSynchronisationDates/lastChangeDateTime") is not None

    props = item.findall(
        "gdsnTradeItemClassification/additionalTradeItemClassification"
        "/additionalTradeItemClassificationValue/additionalTradeItemClassificationProperty"
    )
    assert len(props) == 2

    code = props[0].find("additionalTradeItemClassificationPropertyCode")
    assert code.text == "4.014"

    descriptions = props[0].findall("propertyDescription")
    assert {d.get("languageCode"): d.text for d in descriptions} == {"en": "White", "fr": "Blanc"}


def test_build_code_property_element_has_no_language_code():
    prop = build_code_property_element("4.199", "muurmontage_vast")

    assert prop.find("additionalTradeItemClassificationPropertyCode").text == "4.199"
    code_el = prop.find("propertyCode")
    assert code_el.text == "muurmontage_vast"
    assert code_el.get("languageCode") is None
    assert prop.find("propertyDescription") is None


def test_build_measurement_property_element_has_no_language_code():
    prop = build_measurement_property_element("4.052", 10, "WTT")

    assert prop.find("additionalTradeItemClassificationPropertyCode").text == "4.052"
    measurement_el = prop.find("propertyMeasurement")
    assert measurement_el.text == "10"
    assert measurement_el.get("measurementUnitCode") == "WTT"
    assert measurement_el.get("languageCode") is None
    assert prop.find("propertyDescription") is None
    assert prop.find("propertyCode") is None


def test_build_string_property_element_has_no_language_code():
    prop = build_string_property_element("4.651", "B106010726")

    assert prop.find("additionalTradeItemClassificationPropertyCode").text == "4.651"
    string_el = prop.find("propertyString")
    assert string_el.text == "B106010726"
    assert string_el.get("languageCode") is None
    assert prop.find("propertyDescription") is None
    assert prop.find("propertyCode") is None


def test_build_integer_property_element_has_no_language_code():
    prop = build_integer_property_element("4.435", 1)

    assert prop.find("additionalTradeItemClassificationPropertyCode").text == "4.435"
    int_el = prop.find("propertyInteger")
    assert int_el.text == "1"
    assert int_el.get("languageCode") is None
    assert int_el.get("measurementUnitCode") is None
    assert prop.find("propertyDescription") is None
    assert prop.find("propertyCode") is None


def test_build_duty_fee_tax_information_module_element():
    module = build_duty_fee_tax_information_module_element(
        agency_code="281", tax_type_code="VAT", category_code="STANDARD"
    )

    assert etree.QName(module).localname == "dutyFeeTaxInformationModule"
    assert etree.QName(module).namespace == NS_DUTY_FEE_TAX
    info = module.find("dutyFeeTaxInformation")
    assert info.find("dutyFeeTaxAgencyCode").text == "281"
    assert info.find("dutyFeeTaxTypeCode").text == "VAT"
    assert info.find("dutyFeeTax/dutyFeeTaxCategoryCode").text == "STANDARD"


def test_build_lighting_device_module_element():
    module = build_lighting_device_module_element(10, "WTT")

    assert etree.QName(module).localname == "lightingDeviceModule"
    assert etree.QName(module).namespace == NS_LIGHTING_DEVICE
    declared_power = module.find("lightBulbInformation/declaredPower")
    assert declared_power.text == "10"
    assert declared_power.get("measurementUnitCode") == "WTT"


def test_build_trade_item_element_without_extension_modules_has_no_trade_item_information():
    item = build_trade_item_element(
        matnr="62053", gtin="09002759620530", gpc_category_code="10000552", properties=[],
    )
    assert item.find("tradeItemInformation") is None


def test_build_trade_item_element_with_extension_modules():
    module = build_duty_fee_tax_information_module_element("281", "VAT", "STANDARD")
    item = build_trade_item_element(
        matnr="62053",
        gtin="09002759620530",
        gpc_category_code="10000552",
        properties=[],
        extension_modules=[module],
    )

    extension = item.find("tradeItemInformation/extension")
    assert extension is not None
    assert extension.find(f"{{{NS_DUTY_FEE_TAX}}}dutyFeeTaxInformationModule") is not None


def test_build_document_combines_multiple_articles_into_one_transaction():
    item1 = build_catalogue_item_element(
        build_trade_item_element(
            "111", "00000000000111", "10000552",
            [build_description_property_element("4.014", {"en": "White"})],
        )
    )
    item2 = build_catalogue_item_element(
        build_trade_item_element(
            "222", "00000000000222", "10000552",
            [build_description_property_element("4.014", {"en": "Black"})],
        )
    )
    notifications = [
        build_notification_element(item1, sender_gln="8719333022437"),
        build_notification_element(item2, sender_gln="8719333022437"),
    ]

    xml_bytes = build_document(notifications, sender_gln="8719333022437", receiver_gln="8712345013042")
    root = etree.fromstring(xml_bytes)

    assert etree.QName(root).localname == "catalogueItemNotificationMessage"
    assert root.find(f".//{{{NS_SBDH}}}Sender/{{{NS_SBDH}}}Identifier").text == "8719333022437"
    assert root.find(f".//{{{NS_SBDH}}}Receiver/{{{NS_SBDH}}}Identifier").text == "8712345013042"

    # TransactionType.documentCommand is NOT repeatable (GdsnCommon.xsd), so
    # there's exactly one <transaction>/<documentCommand> for the whole batch,
    # holding multiple catalogueItemNotification elements (its "document"
    # substitution-group ref IS maxOccurs="unbounded").
    transaction_els = root.findall("transaction")
    assert len(transaction_els) == 1
    assert len(root.findall("transaction/documentCommand")) == 1

    trade_items = root.findall(f".//transaction/documentCommand/{{{NS_CIN}}}catalogueItemNotification/catalogueItem/tradeItem")
    assert [i.find("additionalTradeItemIdentification").text for i in trade_items] == ["111", "222"]
