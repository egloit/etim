from lxml import etree

from gs1_export.gs1_exporter import (
    NS_CIN,
    NS_DELIVERY_PURCHASING_INFORMATION,
    NS_DUTY_FEE_TAX,
    NS_LIGHTING_DEVICE,
    NS_MARKETING_INFORMATION,
    NS_PACKAGING_INFORMATION,
    NS_PLACE_OF_ITEM_ACTIVITY,
    NS_REFERENCED_FILE_DETAIL_INFORMATION,
    NS_SBDH,
    NS_TRADE_ITEM_DESCRIPTION,
    NS_TRADE_ITEM_MEASUREMENTS,
    NS_VARIABLE_TRADE_ITEM_INFORMATION,
    build_catalogue_item_element,
    build_code_property_element,
    build_delivery_purchasing_information_module_element,
    build_description_property_element,
    build_document,
    build_duty_fee_tax_information_module_element,
    build_integer_property_element,
    build_lighting_device_module_element,
    build_marketing_information_module_element,
    build_measurement_property_element,
    build_notification_element,
    build_packaging_information_module_element,
    build_place_of_item_activity_module_element,
    build_referenced_file_detail_information_module_element,
    build_string_property_element,
    build_trade_item_description_module_element,
    build_trade_item_element,
    build_trade_item_measurements_module_element,
    build_variable_trade_item_information_module_element,
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
        contact_name="EGLO SAP Support",
        contact_email="sap.support@eglo.com",
    )

    assert item.find("gtin").text == "09002759620530"
    assert item.find("additionalTradeItemIdentification").text == "62053"
    assert item.find("brandOwner/gln").text == "8719333022437"
    assert item.find("informationProviderOfTradeItem/gln").text == "8719333022437"
    assert item.find("gdsnTradeItemClassification/gpcCategoryCode").text == "10000552"
    assert item.find("targetMarket/targetMarketCountryCode").text == "056"
    # tradeItemSynchronisationDates is minOccurs="1" in TradeItem.xsd - must always be present.
    assert item.find("tradeItemSynchronisationDates/lastChangeDateTime") is not None
    assert item.find("tradeItemSynchronisationDates/effectiveDateTime") is not None
    # Fixed value per GS1's own error response (500.593) for consumer units.
    contact = item.find("tradeItemContactInformation")
    assert contact.find("contactTypeCode").text == "BZL"
    # Element order matches TradeItemContactInformationType's sequence in TradeItem.xsd.
    assert [etree.QName(c).localname for c in contact] == [
        "contactTypeCode", "contactAddress", "contactName", "targetMarketCommunicationChannel",
    ]
    assert contact.find("contactAddress").text == "sap.support@eglo.com"
    assert contact.find("contactName").text == "EGLO SAP Support"
    channel = contact.find("targetMarketCommunicationChannel/communicationChannel")
    assert channel.find("communicationChannelCode").text == "EMAIL"
    assert channel.find("communicationValue").text == "sap.support@eglo.com"

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


def test_build_trade_item_measurements_module_element_full():
    module = build_trade_item_measurements_module_element(
        depth=185, width=185, height=215, gross_weight=665, net_weight=590, net_content=1,
    )

    assert etree.QName(module).localname == "tradeItemMeasurementsModule"
    assert etree.QName(module).namespace == NS_TRADE_ITEM_MEASUREMENTS
    measurements = module.find("tradeItemMeasurements")
    # Element order verified against a real GS1-portal reference export.
    assert [etree.QName(c).localname for c in measurements] == [
        "depth", "height", "netContent", "width", "tradeItemWeight",
    ]
    assert measurements.find("depth").text == "185"
    assert measurements.find("depth").get("measurementUnitCode") == "MMT"
    assert measurements.find("tradeItemWeight/grossWeight").text == "665"
    assert measurements.find("tradeItemWeight/grossWeight").get("measurementUnitCode") == "GRM"
    assert measurements.find("tradeItemWeight/netWeight").text == "590"


def test_build_trade_item_measurements_module_element_omits_missing_values():
    module = build_trade_item_measurements_module_element(gross_weight=595)

    measurements = module.find("tradeItemMeasurements")
    assert measurements.find("depth") is None
    assert measurements.find("width") is None
    assert measurements.find("height") is None
    assert measurements.find("netContent") is None
    assert measurements.find("tradeItemWeight/grossWeight").text == "595"
    assert measurements.find("tradeItemWeight/netWeight") is None


def test_build_trade_item_description_module_element():
    module = build_trade_item_description_module_element(
        descriptions_by_language={"en": "WL/1 white/oak-optic TOWNSHEND", "nl": "WL/1 WEISS/EICHE-OPTIK TOWNSHEND"},
        functional_names_by_language={"en": "WALL LIGHT", "nl": "WANDLAMP"},
        brand_name="EGLO",
    )

    assert etree.QName(module).localname == "tradeItemDescriptionModule"
    assert etree.QName(module).namespace == NS_TRADE_ITEM_DESCRIPTION
    info = module.find("tradeItemDescriptionInformation")
    # Element order verified against a real GS1-portal reference export.
    assert [etree.QName(c).localname for c in info] == [
        "descriptionShort", "descriptionShort", "functionalName", "functionalName",
        "tradeItemDescription", "tradeItemDescription", "brandNameInformation",
    ]
    short_descs = {d.get("languageCode"): d.text for d in info.findall("descriptionShort")}
    assert short_descs == {"en": "WL/1 white/oak-optic TOWNSHEND", "nl": "WL/1 WEISS/EICHE-OPTIK TOWNSHEND"}
    functional_names = {d.get("languageCode"): d.text for d in info.findall("functionalName")}
    assert functional_names == {"en": "WALL LIGHT", "nl": "WANDLAMP"}
    assert info.find("brandNameInformation/brandName").text == "EGLO"


def test_build_trade_item_description_module_element_omits_brand_when_empty():
    module = build_trade_item_description_module_element(
        descriptions_by_language={"en": "Text"}, functional_names_by_language={"en": "Text"}, brand_name="",
    )
    assert module.find("tradeItemDescriptionInformation/brandNameInformation") is None


def test_build_marketing_information_module_element():
    module = build_marketing_information_module_element(
        messages_by_language={"en": "The TOWNSHEND wall light is made of white metal.", "nl": "Deze wandlamp..."},
    )

    assert etree.QName(module).localname == "marketingInformationModule"
    assert etree.QName(module).namespace == NS_MARKETING_INFORMATION
    info = module.find("marketingInformation")
    messages = {m.get("languageCode"): m.text for m in info.findall("tradeItemMarketingMessage")}
    assert messages == {"en": "The TOWNSHEND wall light is made of white metal.", "nl": "Deze wandlamp..."}


def test_build_marketing_information_module_element_empty_when_no_languages():
    module = build_marketing_information_module_element(messages_by_language={})
    assert module.find("marketingInformation").findall("tradeItemMarketingMessage") == []


def test_build_referenced_file_detail_information_module_element():
    module = build_referenced_file_detail_information_module_element(
        file_type_code="PRODUCT_IMAGE",
        file_format_name="Jpeg",
        file_name="09002759440558_C1R0.JPEG",
        uri="https://assets.eglo.com/09002759440558_C1R0.JPEG",
        is_primary_file=True,
        media_source_gln="8719333022437",
    )

    assert etree.QName(module).localname == "referencedFileDetailInformationModule"
    assert etree.QName(module).namespace == NS_REFERENCED_FILE_DETAIL_INFORMATION
    header = module.find("referencedFileHeader")
    assert header.find("referencedFileTypeCode").text == "PRODUCT_IMAGE"
    assert header.find("fileFormatName").text == "Jpeg"
    assert header.find("fileName").text == "09002759440558_C1R0.JPEG"
    assert header.find("uniformResourceIdentifier").text == "https://assets.eglo.com/09002759440558_C1R0.JPEG"
    assert header.find("isPrimaryFile").text == "TRUE"
    assert header.find("avpList/stringAVP").text == "8719333022437"
    assert header.find("avpList/stringAVP").get("attributeName") == "mediaSourceGln"


def test_build_referenced_file_detail_information_module_element_no_gln_omits_avp_list():
    module = build_referenced_file_detail_information_module_element(
        file_type_code="PRODUCT_IMAGE", file_format_name="Jpeg", file_name="x.jpg", uri="https://x/x.jpg",
    )
    assert module.find("referencedFileHeader/isPrimaryFile").text == "FALSE"
    assert module.find("referencedFileHeader/avpList") is None


def test_build_packaging_information_module_element():
    module = build_packaging_information_module_element(
        "BX", {"en": "BOX", "fr": "BOITE", "nl": "DOOS"},
    )

    assert etree.QName(module).localname == "packagingInformationModule"
    assert etree.QName(module).namespace == NS_PACKAGING_INFORMATION
    packaging = module.find("packaging")
    assert packaging.find("packagingTypeCode").text == "BX"
    descriptions = {d.get("languageCode"): d.text for d in packaging.findall("packagingTypeDescription")}
    assert descriptions == {"en": "BOX", "fr": "BOITE", "nl": "DOOS"}


def test_build_place_of_item_activity_module_element_with_import_classification():
    module = build_place_of_item_activity_module_element(
        country_of_origin_code="156", import_classification_value="94051990",
    )

    assert etree.QName(module).localname == "placeOfItemActivityModule"
    assert etree.QName(module).namespace == NS_PLACE_OF_ITEM_ACTIVITY
    # importClassification comes before placeOfProductActivity in the reference export.
    assert [etree.QName(c).localname for c in module] == ["importClassification", "placeOfProductActivity"]
    assert module.find("importClassification/importClassificationTypeCode").text == "INTRASTAT"
    assert module.find("importClassification/importClassificationValue").text == "94051990"
    assert module.find("placeOfProductActivity/countryOfOrigin/countryCode").text == "156"


def test_build_place_of_item_activity_module_element_without_import_classification():
    module = build_place_of_item_activity_module_element(country_of_origin_code="356")
    assert module.find("importClassification") is None
    assert module.find("placeOfProductActivity/countryOfOrigin/countryCode").text == "356"


def test_build_variable_trade_item_information_module_element_defaults_to_false():
    module = build_variable_trade_item_information_module_element()

    assert etree.QName(module).localname == "variableTradeItemInformationModule"
    assert etree.QName(module).namespace == NS_VARIABLE_TRADE_ITEM_INFORMATION
    assert module.find("variableTradeItemInformation/isTradeItemAVariableUnit").text == "false"


def test_build_variable_trade_item_information_module_element_true():
    module = build_variable_trade_item_information_module_element(True)
    assert module.find("variableTradeItemInformation/isTradeItemAVariableUnit").text == "true"


def test_build_delivery_purchasing_information_module_element_uses_given_value():
    module = build_delivery_purchasing_information_module_element("2026-08-28T12:00:00Z")

    assert etree.QName(module).localname == "deliveryPurchasingInformationModule"
    assert etree.QName(module).namespace == NS_DELIVERY_PURCHASING_INFORMATION
    assert module.find("deliveryPurchasingInformation/startAvailabilityDateTime").text == "2026-08-28T12:00:00Z"


def test_build_delivery_purchasing_information_module_element_defaults_to_now():
    module = build_delivery_purchasing_information_module_element()
    assert module.find("deliveryPurchasingInformation/startAvailabilityDateTime").text is not None
