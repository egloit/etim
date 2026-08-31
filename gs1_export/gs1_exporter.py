"""
Builds the GS1 GDSN CatalogueItemNotification XML document.

Structure is modelled on a real export sample from the GS1 portal (SBDH +
transaction/documentCommand/catalogueItemNotification/catalogueItem/tradeItem),
cross-checked against CatalogueItemNotification.xsd, TradeItem.xsd and
GdsnCommon.xsd themselves:
  - catalogueItemNotificationMessage = StandardBusinessDocumentHeader (once) +
    transaction (maxOccurs="unbounded").
  - TransactionType (GdsnCommon.xsd) = transactionIdentification + EXACTLY ONE
    documentCommand (no maxOccurs -> default 1).
  - DocumentCommandType (GdsnCommon.xsd) = documentCommandHeader (once) +
    the abstract "document" substitution-group element with
    maxOccurs="unbounded" - catalogueItemNotification substitutes into it.
  - Each catalogueItemNotification carries exactly ONE catalogueItem
    (maxOccurs=1 there) which carries exactly one tradeItem.
  => multiple articles are batched into ONE transaction + ONE documentCommand,
     each holding multiple <catalogue_item_notification:catalogueItemNotification>
     elements (one per article) - NOT by repeating the whole <transaction>
     block, and NOT by repeating <catalogueItem> inside one notification.
  - TradeItemType (via TradeItem.xsd): informationProviderOfTradeItem,
    gdsnTradeItemClassification, targetMarket (>=1) and
    tradeItemSynchronisationDates are all minOccurs="1" (required); brandOwner
    is optional. additionalTradeItemClassificationProperty is a *sequence* of
    optional value elements (propertyDescription/propertyCode/
    propertyMeasurement/propertyInteger/...), not a choice - they may
    co-occur, matching the real sample.

Only the pieces this app actually has data for are populated: gtin,
additionalTradeItemIdentification, the fixed base-unit flags,
informationProviderOfTradeItem/brandOwner (from GS1_SENDER_GLN),
gdsnTradeItemClassification (gpcCategoryCode = BrickId +
additionalTradeItemClassificationProperty per active gs1_mapping row,
propertyDescription only - see pipeline.py for why), targetMarket and
tradeItemSynchronisationDates. Other GDSN extension modules (marketing info,
packaging, measurements, ...) are intentionally omitted - this export only
covers the Brick/Pick classification properties, not full product master
data sync.
"""
import uuid
from datetime import datetime, timezone
from typing import Optional

from lxml import etree

NS_SBDH = "http://www.unece.org/cefact/namespaces/StandardBusinessDocumentHeader"
NS_CIN = "urn:gs1:gdsn:catalogue_item_notification:xsd:3"
NS_XSI = "http://www.w3.org/2001/XMLSchema-instance"

CIN_SCHEMA_LOCATION = (
    "urn:gs1:gdsn:catalogue_item_notification:xsd:3 "
    "http://www.gs1globalregistry.net/3.1/schemas/gs1/gdsn/CatalogueItemNotification.xsd"
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def build_description_property_element(pickid: str, texts_by_language: dict[str, str]) -> etree._Element:
    prop = etree.Element("additionalTradeItemClassificationProperty")
    etree.SubElement(prop, "additionalTradeItemClassificationPropertyCode").text = pickid
    for language_code, text in texts_by_language.items():
        desc_el = etree.SubElement(prop, "propertyDescription")
        desc_el.set("languageCode", language_code)
        desc_el.text = text
    return prop


def build_code_property_element(pickid: str, code_value: str) -> etree._Element:
    """propertyCode has no languageCode - it's a single, language-independent
    GS1 vocabulary value (e.g. "muurmontage_vast"), looked up via
    public.gs1_pim_value_crosswalk."""
    prop = etree.Element("additionalTradeItemClassificationProperty")
    etree.SubElement(prop, "additionalTradeItemClassificationPropertyCode").text = pickid
    etree.SubElement(prop, "propertyCode").text = code_value
    return prop


def build_measurement_property_element(pickid: str, amount: int, unit_code: str) -> etree._Element:
    """propertyMeasurement has no languageCode either - a number plus a fixed
    GS1/UN-CEFACT measurementUnitCode (e.g. "WTT" for watt), looked up via
    public.gs1_gpc_attribute_types.measurement_unit_code."""
    prop = etree.Element("additionalTradeItemClassificationProperty")
    etree.SubElement(prop, "additionalTradeItemClassificationPropertyCode").text = pickid
    measurement_el = etree.SubElement(prop, "propertyMeasurement")
    measurement_el.set("measurementUnitCode", unit_code)
    measurement_el.text = str(amount)
    return prop


def build_string_property_element(pickid: str, value: str) -> etree._Element:
    """propertyString has no languageCode either - a plain free-text/code
    string (e.g. a Bebat/Stibat recycling code), unlike propertyDescription."""
    prop = etree.Element("additionalTradeItemClassificationProperty")
    etree.SubElement(prop, "additionalTradeItemClassificationPropertyCode").text = pickid
    etree.SubElement(prop, "propertyString").text = value
    return prop


def build_integer_property_element(pickid: str, value: int) -> etree._Element:
    """propertyInteger has no languageCode either - a plain whole number with
    no unit (e.g. count of items), unlike propertyMeasurement."""
    prop = etree.Element("additionalTradeItemClassificationProperty")
    etree.SubElement(prop, "additionalTradeItemClassificationPropertyCode").text = pickid
    etree.SubElement(prop, "propertyInteger").text = str(value)
    return prop


NS_DUTY_FEE_TAX = "urn:gs1:gdsn:duty_fee_tax_information:xsd:3"
DUTY_FEE_TAX_SCHEMA_LOCATION = (
    "urn:gs1:gdsn:duty_fee_tax_information:xsd:3 "
    "http://www.gs1globalregistry.net/3.1/schemas/gs1/gdsn/DutyFeeTaxInformationModule.xsd"
)

NS_LIGHTING_DEVICE = "urn:gs1:gdsn:lighting_device:xsd:3"
LIGHTING_DEVICE_SCHEMA_LOCATION = (
    "urn:gs1:gdsn:lighting_device:xsd:3 "
    "http://www.gs1globalregistry.net/3.1/schemas/gs1/gdsn/LightingDeviceModule.xsd"
)

NS_TRADE_ITEM_MEASUREMENTS = "urn:gs1:gdsn:trade_item_measurements:xsd:3"
TRADE_ITEM_MEASUREMENTS_SCHEMA_LOCATION = (
    "urn:gs1:gdsn:trade_item_measurements:xsd:3 "
    "http://www.gs1globalregistry.net/3.1/schemas/gs1/gdsn/TradeItemMeasurementsModule.xsd"
)

NS_TRADE_ITEM_DESCRIPTION = "urn:gs1:gdsn:trade_item_description:xsd:3"
TRADE_ITEM_DESCRIPTION_SCHEMA_LOCATION = (
    "urn:gs1:gdsn:trade_item_description:xsd:3 "
    "http://www.gs1globalregistry.net/3.1/schemas/gs1/gdsn/TradeItemDescriptionModule.xsd"
)

NS_PLACE_OF_ITEM_ACTIVITY = "urn:gs1:gdsn:place_of_item_activity:xsd:3"
PLACE_OF_ITEM_ACTIVITY_SCHEMA_LOCATION = (
    "urn:gs1:gdsn:place_of_item_activity:xsd:3 "
    "http://www.gs1globalregistry.net/3.1/schemas/gs1/gdsn/PlaceOfItemActivityModule.xsd"
)

NS_VARIABLE_TRADE_ITEM_INFORMATION = "urn:gs1:gdsn:variable_trade_item_information:xsd:3"
VARIABLE_TRADE_ITEM_INFORMATION_SCHEMA_LOCATION = (
    "urn:gs1:gdsn:variable_trade_item_information:xsd:3 "
    "http://www.gs1globalregistry.net/3.1/schemas/gs1/gdsn/VariableTradeItemInformationModule.xsd"
)

NS_DELIVERY_PURCHASING_INFORMATION = "urn:gs1:gdsn:delivery_purchasing_information:xsd:3"
DELIVERY_PURCHASING_INFORMATION_SCHEMA_LOCATION = (
    "urn:gs1:gdsn:delivery_purchasing_information:xsd:3 "
    "http://www.gs1globalregistry.net/3.1/schemas/gs1/gdsn/DeliveryPurchasingInformationModule.xsd"
)


def build_duty_fee_tax_information_module_element(
    agency_code: str, tax_type_code: str, category_code: str
) -> etree._Element:
    """tradeItemInformation/extension module for VAT info - fixed per VKORG/
    market, not article-specific, see pipeline.py/vkorg_vat_mapping.json."""
    module = etree.Element(
        f"{{{NS_DUTY_FEE_TAX}}}dutyFeeTaxInformationModule",
        nsmap={"duty_fee_tax_information": NS_DUTY_FEE_TAX, "xsi": NS_XSI},
    )
    module.set(f"{{{NS_XSI}}}schemaLocation", DUTY_FEE_TAX_SCHEMA_LOCATION)
    info = etree.SubElement(module, "dutyFeeTaxInformation")
    etree.SubElement(info, "dutyFeeTaxAgencyCode").text = agency_code
    etree.SubElement(info, "dutyFeeTaxTypeCode").text = tax_type_code
    duty_fee_tax = etree.SubElement(info, "dutyFeeTax")
    etree.SubElement(duty_fee_tax, "dutyFeeTaxCategoryCode").text = category_code
    return module


def build_lighting_device_module_element(declared_power: int, unit_code: str = "WTT") -> etree._Element:
    """tradeItemInformation/extension module for the declared wattage of a
    light source - sourced from PIM_FAS_05_1, the same field already used for
    Pick 4.052 (Power rating), not article/VKORG-dependent so no config
    mapping is needed unlike dutyFeeTaxInformationModule."""
    module = etree.Element(
        f"{{{NS_LIGHTING_DEVICE}}}lightingDeviceModule",
        nsmap={"lighting_device": NS_LIGHTING_DEVICE, "xsi": NS_XSI},
    )
    module.set(f"{{{NS_XSI}}}schemaLocation", LIGHTING_DEVICE_SCHEMA_LOCATION)
    bulb_info = etree.SubElement(module, "lightBulbInformation")
    declared_power_el = etree.SubElement(bulb_info, "declaredPower")
    declared_power_el.set("measurementUnitCode", unit_code)
    declared_power_el.text = str(declared_power)
    return module


def build_trade_item_measurements_module_element(
    depth: Optional[int] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
    dimension_unit_code: str = "MMT",
    gross_weight: Optional[int] = None,
    net_weight: Optional[int] = None,
    weight_unit_code: str = "GRM",
    net_content: Optional[int] = None,
    net_content_unit_code: str = "H87",
) -> etree._Element:
    """tradeItemInformation/extension module for physical measurements -
    element order/shape verified against a real GS1-portal reference export
    for matnr 43706 (depth, height, width, netContent, tradeItemWeight).
    Every value is optional and simply left out if not available - most of
    EGLO's SAP material master data doesn't have depth/width/height filled in
    (see pipeline.py), only weight is reliably present."""
    module = etree.Element(
        f"{{{NS_TRADE_ITEM_MEASUREMENTS}}}tradeItemMeasurementsModule",
        nsmap={"trade_item_measurements": NS_TRADE_ITEM_MEASUREMENTS, "xsi": NS_XSI},
    )
    module.set(f"{{{NS_XSI}}}schemaLocation", TRADE_ITEM_MEASUREMENTS_SCHEMA_LOCATION)
    measurements = etree.SubElement(module, "tradeItemMeasurements")

    if depth is not None:
        etree.SubElement(measurements, "depth", measurementUnitCode=dimension_unit_code).text = str(depth)
    if height is not None:
        etree.SubElement(measurements, "height", measurementUnitCode=dimension_unit_code).text = str(height)
    if net_content is not None:
        etree.SubElement(measurements, "netContent", measurementUnitCode=net_content_unit_code).text = str(net_content)
    if width is not None:
        etree.SubElement(measurements, "width", measurementUnitCode=dimension_unit_code).text = str(width)

    if gross_weight is not None or net_weight is not None:
        weight = etree.SubElement(measurements, "tradeItemWeight")
        if gross_weight is not None:
            etree.SubElement(weight, "grossWeight", measurementUnitCode=weight_unit_code).text = str(gross_weight)
        if net_weight is not None:
            etree.SubElement(weight, "netWeight", measurementUnitCode=weight_unit_code).text = str(net_weight)

    return module


def build_trade_item_description_module_element(
    descriptions_by_language: dict[str, str],
    functional_names_by_language: dict[str, str],
    brand_name: str = "",
) -> etree._Element:
    """tradeItemInformation/extension module for description texts - element
    order/shape verified against a real GS1-portal reference export for
    matnr 43706 (descriptionShort, functionalName, tradeItemDescription,
    brandNameInformation, in that order, all per-language except brandName).
    descriptions_by_language feeds both descriptionShort and
    tradeItemDescription (identical text in the reference export too)."""
    module = etree.Element(
        f"{{{NS_TRADE_ITEM_DESCRIPTION}}}tradeItemDescriptionModule",
        nsmap={"trade_item_description": NS_TRADE_ITEM_DESCRIPTION, "xsi": NS_XSI},
    )
    module.set(f"{{{NS_XSI}}}schemaLocation", TRADE_ITEM_DESCRIPTION_SCHEMA_LOCATION)
    info = etree.SubElement(module, "tradeItemDescriptionInformation")

    for lang_code, text in descriptions_by_language.items():
        etree.SubElement(info, "descriptionShort", languageCode=lang_code).text = text
    for lang_code, text in functional_names_by_language.items():
        etree.SubElement(info, "functionalName", languageCode=lang_code).text = text
    for lang_code, text in descriptions_by_language.items():
        etree.SubElement(info, "tradeItemDescription", languageCode=lang_code).text = text

    if brand_name:
        brand_info = etree.SubElement(info, "brandNameInformation")
        etree.SubElement(brand_info, "brandName").text = brand_name

    return module


def build_place_of_item_activity_module_element(
    country_of_origin_code: str,
    import_classification_value: Optional[str] = None,
) -> etree._Element:
    """tradeItemInformation/extension module for country of origin / customs
    classification - element order/shape verified against a real GS1-portal
    reference export for matnr 43706 (importClassification before
    placeOfProductActivity). country_of_origin_code is the UN M49 numeric
    country code (e.g. "156" for China), not the ISO alpha-2 code - see
    pipeline.py/country_code_mapping.json."""
    module = etree.Element(
        f"{{{NS_PLACE_OF_ITEM_ACTIVITY}}}placeOfItemActivityModule",
        nsmap={"place_of_item_activity": NS_PLACE_OF_ITEM_ACTIVITY, "xsi": NS_XSI},
    )
    module.set(f"{{{NS_XSI}}}schemaLocation", PLACE_OF_ITEM_ACTIVITY_SCHEMA_LOCATION)

    if import_classification_value:
        import_classification = etree.SubElement(module, "importClassification")
        etree.SubElement(import_classification, "importClassificationTypeCode").text = "INTRASTAT"
        etree.SubElement(import_classification, "importClassificationValue").text = import_classification_value

    place_of_activity = etree.SubElement(module, "placeOfProductActivity")
    country_of_origin = etree.SubElement(place_of_activity, "countryOfOrigin")
    etree.SubElement(country_of_origin, "countryCode").text = country_of_origin_code

    return module


def build_variable_trade_item_information_module_element(is_variable_unit: bool = False) -> etree._Element:
    """tradeItemInformation/extension module for GS1 error G1013
    (isTradeItemAVariableUnit must not be empty) - fixed 'false' for EGLO's
    standard lighting products (not variable-measure items like fresh food),
    not sourced from PIM/SAP since there's no per-article signal for this."""
    module = etree.Element(
        f"{{{NS_VARIABLE_TRADE_ITEM_INFORMATION}}}variableTradeItemInformationModule",
        nsmap={"variable_trade_item_information": NS_VARIABLE_TRADE_ITEM_INFORMATION, "xsi": NS_XSI},
    )
    module.set(f"{{{NS_XSI}}}schemaLocation", VARIABLE_TRADE_ITEM_INFORMATION_SCHEMA_LOCATION)
    info = etree.SubElement(module, "variableTradeItemInformation")
    etree.SubElement(info, "isTradeItemAVariableUnit").text = "true" if is_variable_unit else "false"
    return module


def build_delivery_purchasing_information_module_element(start_availability_date_time: Optional[str] = None) -> etree._Element:
    """tradeItemInformation/extension module for GS1 error G1004
    (startAvailabilityDateTime must be populated) - defaults to "now" since
    there's no real per-article source for this yet."""
    module = etree.Element(
        f"{{{NS_DELIVERY_PURCHASING_INFORMATION}}}deliveryPurchasingInformationModule",
        nsmap={"delivery_purchasing_information": NS_DELIVERY_PURCHASING_INFORMATION, "xsi": NS_XSI},
    )
    module.set(f"{{{NS_XSI}}}schemaLocation", DELIVERY_PURCHASING_INFORMATION_SCHEMA_LOCATION)
    info = etree.SubElement(module, "deliveryPurchasingInformation")
    etree.SubElement(info, "startAvailabilityDateTime").text = start_availability_date_time or _now_iso()
    return module


def build_trade_item_element(
    matnr: str,
    gtin: str,
    gpc_category_code: str,
    properties: list[etree._Element],
    sender_gln: str = "",
    sender_party_name: str = "",
    target_market_country_code: str = "",
    classification_system_code: str = "64",
    extension_modules: list[etree._Element] | None = None,
    contact_name: str = "",
    contact_email: str = "",
) -> etree._Element:
    trade_item = etree.Element("tradeItem")

    etree.SubElement(trade_item, "gtin").text = gtin
    etree.SubElement(
        trade_item,
        "additionalTradeItemIdentification",
        additionalTradeItemIdentificationTypeCode="SUPPLIER_ASSIGNED",
    ).text = matnr
    etree.SubElement(trade_item, "isTradeItemABaseUnit").text = "true"
    etree.SubElement(trade_item, "isTradeItemAConsumerUnit").text = "true"
    etree.SubElement(trade_item, "isTradeItemADespatchUnit").text = "true"
    etree.SubElement(trade_item, "isTradeItemAnOrderableUnit").text = "true"
    etree.SubElement(trade_item, "tradeItemUnitDescriptorCode").text = "BASE_UNIT_OR_EACH"

    # brandOwner is optional; informationProviderOfTradeItem is minOccurs="1" in
    # TradeItem.xsd, so it's always emitted (even with an empty gln if
    # GS1_SENDER_GLN isn't configured yet - the validator will flag that).
    if sender_gln:
        brand_owner = etree.SubElement(trade_item, "brandOwner")
        etree.SubElement(brand_owner, "gln").text = sender_gln
        if sender_party_name:
            etree.SubElement(brand_owner, "partyName").text = sender_party_name

    info_provider = etree.SubElement(trade_item, "informationProviderOfTradeItem")
    etree.SubElement(info_provider, "gln").text = sender_gln
    if sender_party_name:
        etree.SubElement(info_provider, "partyName").text = sender_party_name

    classification = etree.SubElement(trade_item, "gdsnTradeItemClassification")
    if gpc_category_code:
        etree.SubElement(classification, "gpcCategoryCode").text = gpc_category_code

    if properties:
        additional = etree.SubElement(classification, "additionalTradeItemClassification")
        etree.SubElement(additional, "additionalTradeItemClassificationSystemCode").text = classification_system_code
        value = etree.SubElement(additional, "additionalTradeItemClassificationValue")
        etree.SubElement(value, "additionalTradeItemClassificationCodeValue").text = "0"
        for prop_element in properties:
            value.append(prop_element)

    # targetMarket is minOccurs="1" (maxOccurs="unbounded") in TradeItem.xsd - if
    # no country code could be resolved (see pipeline.py), it's left out here and
    # the document will fail XSD validation until vkorg_country_mapping.json is filled in.
    if target_market_country_code:
        target_market = etree.SubElement(trade_item, "targetMarket")
        etree.SubElement(target_market, "targetMarketCountryCode").text = target_market_country_code

    # tradeItemContactInformation is a real TradeItem.xsd element (not an
    # extension module) - GS1's own error response (500.593) named the exact
    # required contactTypeCode value ("BZL") for consumer units, so that part
    # is fixed. Once contactTypeCode is used, GS1 also requires contactName/
    # contactAddress/targetMarketCommunicationChannel to be filled in (error
    # 500.594) - contact_email is reused for both contactAddress and the
    # communicationValue, a fixed EGLO-wide support contact, not per-article.
    # Element order (contactTypeCode, contactAddress, contactName,
    # targetMarketCommunicationChannel) matches TradeItemContactInformationType's
    # sequence in TradeItem.xsd.
    contact_info = etree.SubElement(trade_item, "tradeItemContactInformation")
    etree.SubElement(contact_info, "contactTypeCode").text = "BZL"
    if contact_email:
        etree.SubElement(contact_info, "contactAddress").text = contact_email
    if contact_name:
        etree.SubElement(contact_info, "contactName").text = contact_name
    if contact_email:
        comm_channel = etree.SubElement(contact_info, "targetMarketCommunicationChannel")
        channel = etree.SubElement(comm_channel, "communicationChannel")
        # "EMAIL" verified against the real CommunicationChannelCode picklist
        # (GS1 Benelux DHZTD attribute workbook) - "EM" (an earlier guess) is
        # not a valid code, GS1 rejected it with G541.
        etree.SubElement(channel, "communicationChannelCode").text = "EMAIL"
        etree.SubElement(channel, "communicationValue").text = contact_email

    # tradeItemInformation/extension is typed permissively (xsd:any-like) in
    # CatalogueItemNotification.xsd, so extension modules can be added here
    # without fetching their own XSDs for validation.
    if extension_modules:
        trade_item_information = etree.SubElement(trade_item, "tradeItemInformation")
        extension = etree.SubElement(trade_item_information, "extension")
        for module_element in extension_modules:
            extension.append(module_element)

    # tradeItemSynchronisationDates is minOccurs="1" in TradeItem.xsd.
    # effectiveDateTime is GS1-mandatory too (error G1283) - "now" is a
    # reasonable default since we don't have a real per-article source for it.
    sync_dates = etree.SubElement(trade_item, "tradeItemSynchronisationDates")
    now = _now_iso()
    etree.SubElement(sync_dates, "lastChangeDateTime").text = now
    etree.SubElement(sync_dates, "effectiveDateTime").text = now

    return trade_item


def build_catalogue_item_element(trade_item: etree._Element) -> etree._Element:
    catalogue_item = etree.Element("catalogueItem")
    state = etree.SubElement(catalogue_item, "catalogueItemState")
    etree.SubElement(state, "catalogueItemStateCode").text = "REGISTERED"
    catalogue_item.append(trade_item)
    return catalogue_item


def build_notification_element(catalogue_item: etree._Element, sender_gln: str = "") -> etree._Element:
    """One <catalogue_item_notification:catalogueItemNotification> per article.
    DocumentCommandType's "document" (abstract, substitutionGroup) is
    maxOccurs="unbounded", so multiple of these go into a single
    documentCommand - see build_document()."""
    notification = etree.Element(f"{{{NS_CIN}}}catalogueItemNotification")
    etree.SubElement(notification, "creationDateTime").text = _now_iso()
    etree.SubElement(notification, "documentStatusCode").text = "COPY"
    etree.SubElement(notification, "documentActionCode").text = "ADD"
    notification_id = etree.SubElement(notification, "catalogueItemNotificationIdentification")
    etree.SubElement(notification_id, "entityIdentification").text = f"CatalogueItemNotification-{uuid.uuid4()}"
    etree.SubElement(etree.SubElement(notification_id, "contentOwner"), "gln").text = sender_gln
    etree.SubElement(notification, "isReload").text = "false"
    notification.append(catalogue_item)

    return notification


def build_document(notifications: list[etree._Element], sender_gln: str = "", receiver_gln: str = "") -> bytes:
    root = etree.Element(
        f"{{{NS_CIN}}}catalogueItemNotificationMessage",
        nsmap={"catalogue_item_notification": NS_CIN, "xsi": NS_XSI},
    )
    root.set(f"{{{NS_XSI}}}schemaLocation", CIN_SCHEMA_LOCATION)

    sbdh = etree.SubElement(root, f"{{{NS_SBDH}}}StandardBusinessDocumentHeader", nsmap={None: NS_SBDH})
    etree.SubElement(sbdh, f"{{{NS_SBDH}}}HeaderVersion").text = "1.0"

    sender_el = etree.SubElement(sbdh, f"{{{NS_SBDH}}}Sender")
    etree.SubElement(sender_el, f"{{{NS_SBDH}}}Identifier", Authority="GS1").text = sender_gln

    receiver_el = etree.SubElement(sbdh, f"{{{NS_SBDH}}}Receiver")
    etree.SubElement(receiver_el, f"{{{NS_SBDH}}}Identifier", Authority="GS1").text = receiver_gln

    doc_id = etree.SubElement(sbdh, f"{{{NS_SBDH}}}DocumentIdentification")
    etree.SubElement(doc_id, f"{{{NS_SBDH}}}Standard").text = "GS1"
    etree.SubElement(doc_id, f"{{{NS_SBDH}}}TypeVersion").text = "3.1"
    etree.SubElement(doc_id, f"{{{NS_SBDH}}}InstanceIdentifier").text = f"Message-{uuid.uuid4()}"
    etree.SubElement(doc_id, f"{{{NS_SBDH}}}Type").text = "catalogueItemNotification"
    etree.SubElement(doc_id, f"{{{NS_SBDH}}}CreationDateAndTime").text = _now_iso()

    transaction = etree.SubElement(root, "transaction")
    transaction_id = etree.SubElement(transaction, "transactionIdentification")
    etree.SubElement(transaction_id, "entityIdentification").text = f"Transaction-{uuid.uuid4()}"
    etree.SubElement(etree.SubElement(transaction_id, "contentOwner"), "gln").text = sender_gln

    document_command = etree.SubElement(transaction, "documentCommand")
    command_header = etree.SubElement(document_command, "documentCommandHeader", type="ADD")
    command_id = etree.SubElement(command_header, "documentCommandIdentification")
    etree.SubElement(command_id, "entityIdentification").text = f"DocumentCommand-{uuid.uuid4()}"
    etree.SubElement(etree.SubElement(command_id, "contentOwner"), "gln").text = sender_gln

    for notification in notifications:
        document_command.append(notification)

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)
