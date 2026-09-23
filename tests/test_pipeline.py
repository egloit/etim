import json
from pathlib import Path

import pytest
from lxml import etree

from gs1_export import database, pipeline, sap_database, validator
from gs1_export.gs1_exporter import NS_CIN

FIXTURE = Path(__file__).with_name("fixtures") / "sample_product.json"


@pytest.fixture
def product():
    with open(FIXTURE, "r", encoding="utf-8") as f:
        return json.load(f)


class FakeConnection:
    def close(self):
        pass


@pytest.fixture(autouse=True)
def _stub_export_history(monkeypatch):
    """Most pipeline tests aren't about export-history tracking, and
    FakeConnection has no cursor() - stub these to no-ops by default so
    tests don't need to care. Tests that actually cover history recording
    override this with a recording stub instead, see below."""
    monkeypatch.setattr(database, "get_product_updated_at", lambda conn, matnr: None)
    monkeypatch.setattr(database, "upsert_export_history", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _stub_sap_connection(monkeypatch):
    """Most pipeline tests aren't about the SAP-sourced extension modules -
    simulate "HANA unreachable" by default (export_batch() already handles
    this gracefully, see its warnings.append) so tests don't need a real
    HANA connection. Tests that actually cover the SAP modules override this
    with a fake connection instead, see below."""
    def boom():
        raise OSError("HANA connection refused")

    monkeypatch.setattr(sap_database, "get_connection", boom)


@pytest.fixture(autouse=True)
def _stub_product_image(monkeypatch):
    """Most pipeline tests aren't about referencedFileDetailInformationModule
    - default to "no image found" so tests don't need a cursor for it. Tests
    that actually cover the image module override this, see below."""
    monkeypatch.setattr(database, "get_primary_product_image", lambda conn, matnr: None)


@pytest.fixture(autouse=True)
def _stub_marketing_text(monkeypatch):
    """Most pipeline tests aren't about tradeItemMarketingMessage - default to
    "no PIM_ARTIKELTEXT found" so tests don't need a cursor for it. Tests that
    actually cover marketingInformationModule override this, see below."""
    monkeypatch.setattr(database, "get_pim_catalog_textarea_value", lambda conn, matnr, attribute_code, locale: None)


def _trade_items(xml_bytes):
    root = etree.fromstring(xml_bytes)
    return root.findall(f".//transaction/documentCommand/{{{NS_CIN}}}catalogueItemNotification/catalogueItem/tradeItem")


def test_export_batch_raises_pipeline_error_on_connection_failure(monkeypatch):
    def boom():
        raise OSError("connection refused")

    monkeypatch.setattr(database, "get_connection", boom)

    with pytest.raises(pipeline.PipelineError):
        pipeline.export_batch(["62053"], ["eng"])


def test_export_batch_warns_but_continues_for_unknown_matnr(monkeypatch):
    monkeypatch.setattr(database, "get_connection", lambda: FakeConnection())
    monkeypatch.setattr(database, "get_product_by_matnr", lambda conn, matnr: None)
    # Decouple from whatever GS1_XSD_ROOT happens to be set to in this
    # environment's .env - this test is about the warning/skip behaviour,
    # not about validator.py itself (which has its own tests).
    monkeypatch.setattr(validator, "validate_xml", lambda xml_bytes: ["unused"])

    xml_bytes, warnings, validation_errors = pipeline.export_batch(["999999"], ["eng"])

    assert any("999999" in w for w in warnings)
    assert _trade_items(xml_bytes) == []
    assert validation_errors == ["unused"]


def test_export_batch_builds_trade_item_for_known_matnr(monkeypatch, product):
    monkeypatch.setattr(database, "get_connection", lambda: FakeConnection())
    monkeypatch.setattr(database, "get_product_by_matnr", lambda conn, matnr: product)
    monkeypatch.setattr(database, "get_brick_id", lambda conn, code: "10000552")
    monkeypatch.setattr(
        database,
        "get_active_mappings",
        lambda conn, brick_id: [
            {"pickid": "4.014", "pimfeld": "B2C_FILTER_COLOR"},
            {"pickid": "4.015", "pimfeld": "ZZGLASF"},
            {"pickid": "4.020", "pimfeld": "ZZSER"},
        ],
    )
    # No gs1_gpc_attribute_types entries yet -> treated as unclassified/propertyDescription.
    monkeypatch.setattr(database, "get_gdsn_attribute_type", lambda conn, brick_id, pick_id: None)

    xml_bytes, warnings, _ = pipeline.export_batch(["62053"], ["eng", "fr"], vkorg="0030")

    items = _trade_items(xml_bytes)
    assert len(items) == 1
    assert items[0].find("additionalTradeItemIdentification").text == "62053"
    assert items[0].find("gtin").text == "09002759620530"  # EAN11 zero-padded to 14 digits
    assert items[0].find("gdsnTradeItemClassification/gpcCategoryCode").text == "10000552"
    assert items[0].find("informationProviderOfTradeItem") is not None  # required per TradeItem.xsd
    assert items[0].find("tradeItemSynchronisationDates/lastChangeDateTime") is not None

    props = items[0].findall(
        "gdsnTradeItemClassification/additionalTradeItemClassification"
        "/additionalTradeItemClassificationValue/additionalTradeItemClassificationProperty"
    )
    pickids = [p.find("additionalTradeItemClassificationPropertyCode").text for p in props]
    assert pickids == ["4.014", "4.015", "4.020"]

    color_prop = props[0]
    descriptions = {d.get("languageCode"): d.text for d in color_prop.findall("propertyDescription")}
    assert descriptions == {"en": "Gold", "fr": "Or"}

    # No entry for VKORG 0030 in vkorg_country_mapping.json yet -> warning, no targetMarket element.
    assert any("0030" in w for w in warnings)
    assert items[0].find("targetMarket") is None
    # Same for vkorg_vat_mapping.json -> warning, no dutyFeeTaxInformationModule
    # (tradeItemInformation/extension still exists though - variableTradeItemInformation/
    # deliveryPurchasingInformation are always added, fixed values, no config needed).
    assert any("dutyFeeTaxInformationModule" in w for w in warnings)
    assert items[0].find("tradeItemInformation/extension/{urn:gs1:gdsn:duty_fee_tax_information:xsd:3}"
                          "dutyFeeTaxInformationModule") is None
    assert items[0].find("tradeItemInformation/extension/{urn:gs1:gdsn:variable_trade_item_information:xsd:3}"
                          "variableTradeItemInformationModule/variableTradeItemInformation/"
                          "isTradeItemAVariableUnit").text == "false"
    # packagingInformationModule (GS1 error 500.061) - fixed "BX"/box value,
    # always added regardless of config, only for the requested languages.
    packaging = items[0].find("tradeItemInformation/extension/{urn:gs1:gdsn:packaging_information:xsd:3}"
                               "packagingInformationModule/packaging")
    assert packaging.find("packagingTypeCode").text == "BX"
    packaging_descriptions = {d.get("languageCode"): d.text for d in packaging.findall("packagingTypeDescription")}
    assert packaging_descriptions == {"en": "BOX", "fr": "BOITE"}


def test_export_batch_builds_duty_fee_tax_module_when_vkorg_configured(monkeypatch, product, tmp_path):
    monkeypatch.setattr(database, "get_connection", lambda: FakeConnection())
    monkeypatch.setattr(database, "get_product_by_matnr", lambda conn, matnr: product)
    monkeypatch.setattr(database, "get_brick_id", lambda conn, code: "10000552")
    monkeypatch.setattr(
        database, "get_active_mappings",
        lambda conn, brick_id: [{"pickid": "4.020", "pimfeld": "ZZSER"}],
    )
    monkeypatch.setattr(database, "get_gdsn_attribute_type", lambda conn, brick_id, pick_id: None)

    vat_config_path = tmp_path / "vkorg_vat_mapping.json"
    vat_config_path.write_text(
        json.dumps({"0030": {
            "dutyFeeTaxAgencyCode": "281",
            "dutyFeeTaxTypeCode": "VAT",
            "dutyFeeTaxCategoryCode": "STANDARD",
        }}),
        encoding="utf-8",
    )
    monkeypatch.setattr(pipeline, "_VKORG_VAT_CONFIG_PATH", vat_config_path)

    xml_bytes, warnings, _ = pipeline.export_batch(["62053"], ["eng"], vkorg="0030")

    items = _trade_items(xml_bytes)
    assert not any("dutyFeeTaxInformationModule" in w for w in warnings)
    from gs1_export.gs1_exporter import NS_DUTY_FEE_TAX
    module = items[0].find(f"tradeItemInformation/extension/{{{NS_DUTY_FEE_TAX}}}dutyFeeTaxInformationModule")
    assert module is not None
    assert module.find("dutyFeeTaxInformation/dutyFeeTaxAgencyCode").text == "281"


def test_export_batch_builds_property_string_via_manual_lookup(monkeypatch, product):
    monkeypatch.setattr(database, "get_connection", lambda: FakeConnection())
    monkeypatch.setattr(database, "get_product_by_matnr", lambda conn, matnr: product)
    monkeypatch.setattr(database, "get_brick_id", lambda conn, code: "10000552")
    monkeypatch.setattr(
        database, "get_active_mappings",
        lambda conn, brick_id: [{"pickid": "4.651", "pimfeld": "(manual)"}],
    )
    monkeypatch.setattr(database, "get_gdsn_attribute_type", lambda conn, brick_id, pick_id: "propertyString")
    monkeypatch.setattr(
        database, "get_manual_property_value",
        lambda conn, matnr, pick_id: "B106010726" if matnr == "62053" and pick_id == "4.651" else None,
    )

    xml_bytes, _, _ = pipeline.export_batch(["62053"], ["eng"])

    items = _trade_items(xml_bytes)
    props = items[0].findall(
        "gdsnTradeItemClassification/additionalTradeItemClassification"
        "/additionalTradeItemClassificationValue/additionalTradeItemClassificationProperty"
    )
    assert len(props) == 1
    assert props[0].find("additionalTradeItemClassificationPropertyCode").text == "4.651"
    assert props[0].find("propertyString").text == "B106010726"


def test_export_batch_skips_property_string_without_manual_entry(monkeypatch, product):
    monkeypatch.setattr(database, "get_connection", lambda: FakeConnection())
    monkeypatch.setattr(database, "get_product_by_matnr", lambda conn, matnr: product)
    monkeypatch.setattr(database, "get_brick_id", lambda conn, code: "10000552")
    monkeypatch.setattr(
        database, "get_active_mappings",
        lambda conn, brick_id: [{"pickid": "4.651", "pimfeld": "(manual)"}],
    )
    monkeypatch.setattr(database, "get_gdsn_attribute_type", lambda conn, brick_id, pick_id: "propertyString")
    monkeypatch.setattr(database, "get_manual_property_value", lambda conn, matnr, pick_id: None)

    xml_bytes, _, _ = pipeline.export_batch(["62053"], ["eng"])

    items = _trade_items(xml_bytes)
    props = items[0].findall(
        "gdsnTradeItemClassification/additionalTradeItemClassification"
        "/additionalTradeItemClassificationValue/additionalTradeItemClassificationProperty"
    )
    assert props == []


def test_export_batch_builds_lighting_device_module_when_pim_fas_05_1_present(monkeypatch, product):
    monkeypatch.setattr(database, "get_connection", lambda: FakeConnection())
    product = dict(product)
    product["values"] = dict(product["values"])
    product["values"]["PIM_FAS_05_1"] = [
        {"data": "10", "scope": None, "locale": None, "attribute_type": "pim_catalog_text"}
    ]
    monkeypatch.setattr(database, "get_product_by_matnr", lambda conn, matnr: product)
    monkeypatch.setattr(database, "get_brick_id", lambda conn, code: "10000552")
    monkeypatch.setattr(
        database, "get_active_mappings",
        lambda conn, brick_id: [{"pickid": "4.020", "pimfeld": "ZZSER"}],
    )
    monkeypatch.setattr(database, "get_gdsn_attribute_type", lambda conn, brick_id, pick_id: None)

    xml_bytes, _, _ = pipeline.export_batch(["62053"], ["eng"])

    from gs1_export.gs1_exporter import NS_LIGHTING_DEVICE
    items = _trade_items(xml_bytes)
    module = items[0].find(f"tradeItemInformation/extension/{{{NS_LIGHTING_DEVICE}}}lightingDeviceModule")
    assert module is not None
    assert module.find("lightBulbInformation/declaredPower").text == "10"


def test_export_batch_skips_lighting_device_module_without_pim_fas_05_1(monkeypatch, product):
    monkeypatch.setattr(database, "get_connection", lambda: FakeConnection())
    monkeypatch.setattr(database, "get_product_by_matnr", lambda conn, matnr: product)
    monkeypatch.setattr(database, "get_brick_id", lambda conn, code: "10000552")
    monkeypatch.setattr(
        database, "get_active_mappings",
        lambda conn, brick_id: [{"pickid": "4.020", "pimfeld": "ZZSER"}],
    )
    monkeypatch.setattr(database, "get_gdsn_attribute_type", lambda conn, brick_id, pick_id: None)

    xml_bytes, _, _ = pipeline.export_batch(["62053"], ["eng"])

    items = _trade_items(xml_bytes)
    from gs1_export.gs1_exporter import NS_LIGHTING_DEVICE
    assert items[0].find(f"tradeItemInformation/extension/{{{NS_LIGHTING_DEVICE}}}lightingDeviceModule") is None


def test_file_format_name_known_extensions():
    assert pipeline._file_format_name("390047_101_0001.jpg") == "Jpeg"
    assert pipeline._file_format_name("390047_101_0001.JPEG") == "Jpeg"
    assert pipeline._file_format_name("sheet.pdf") == "Pdf"


def test_file_format_name_unknown_extension_capitalised():
    assert pipeline._file_format_name("clip.mp4") == "Mp4"
    assert pipeline._file_format_name("no_extension") == ""


def test_export_batch_builds_referenced_file_module_when_image_present(monkeypatch, product):
    monkeypatch.setattr(database, "get_connection", lambda: FakeConnection())
    monkeypatch.setattr(database, "get_product_by_matnr", lambda conn, matnr: product)
    monkeypatch.setattr(database, "get_brick_id", lambda conn, code: "10000552")
    monkeypatch.setattr(
        database, "get_active_mappings",
        lambda conn, brick_id: [{"pickid": "4.020", "pimfeld": "ZZSER"}],
    )
    monkeypatch.setattr(database, "get_gdsn_attribute_type", lambda conn, brick_id, pick_id: None)
    monkeypatch.setattr(
        database,
        "get_primary_product_image",
        lambda conn, matnr: {
            "url": "https://eglo.contentdeliveryhub.net/api/data/std/images/abc/c/JPG",
            "filename": "62053_101_0001.jpg",
        },
    )

    xml_bytes, _, _ = pipeline.export_batch(["62053"], ["eng"])

    from gs1_export.gs1_exporter import NS_REFERENCED_FILE_DETAIL_INFORMATION
    items = _trade_items(xml_bytes)
    module = items[0].find(
        f"tradeItemInformation/extension/{{{NS_REFERENCED_FILE_DETAIL_INFORMATION}}}referencedFileDetailInformationModule"
    )
    assert module is not None
    header = module.find("referencedFileHeader")
    assert header.find("referencedFileTypeCode").text == "PRODUCT_IMAGE"
    assert header.find("fileFormatName").text == "Jpeg"
    assert header.find("fileName").text == "62053_101_0001.jpg"
    assert header.find("uniformResourceIdentifier").text == "https://eglo.contentdeliveryhub.net/api/data/std/images/abc/c/JPG"
    assert header.find("isPrimaryFile").text == "TRUE"


def test_export_batch_skips_referenced_file_module_without_image(monkeypatch, product):
    monkeypatch.setattr(database, "get_connection", lambda: FakeConnection())
    monkeypatch.setattr(database, "get_product_by_matnr", lambda conn, matnr: product)
    monkeypatch.setattr(database, "get_brick_id", lambda conn, code: "10000552")
    monkeypatch.setattr(
        database, "get_active_mappings",
        lambda conn, brick_id: [{"pickid": "4.020", "pimfeld": "ZZSER"}],
    )
    monkeypatch.setattr(database, "get_gdsn_attribute_type", lambda conn, brick_id, pick_id: None)

    xml_bytes, _, _ = pipeline.export_batch(["62053"], ["eng"])

    from gs1_export.gs1_exporter import NS_REFERENCED_FILE_DETAIL_INFORMATION
    items = _trade_items(xml_bytes)
    assert items[0].find(
        f"tradeItemInformation/extension/{{{NS_REFERENCED_FILE_DETAIL_INFORMATION}}}referencedFileDetailInformationModule"
    ) is None


def test_export_batch_builds_marketing_information_module_when_artikeltext_present(monkeypatch, product):
    monkeypatch.setattr(database, "get_connection", lambda: FakeConnection())
    monkeypatch.setattr(database, "get_product_by_matnr", lambda conn, matnr: product)
    monkeypatch.setattr(database, "get_brick_id", lambda conn, code: "10000552")
    monkeypatch.setattr(
        database, "get_active_mappings",
        lambda conn, brick_id: [{"pickid": "4.020", "pimfeld": "ZZSER"}],
    )
    monkeypatch.setattr(database, "get_gdsn_attribute_type", lambda conn, brick_id, pick_id: None)
    monkeypatch.setattr(
        database,
        "get_pim_catalog_textarea_value",
        lambda conn, matnr, attribute_code, locale: (
            "The TOWNSHEND wall light is made of white metal." if locale == "en_GB" else None
        ),
    )

    xml_bytes, _, _ = pipeline.export_batch(["62053"], ["eng"])

    from gs1_export.gs1_exporter import NS_MARKETING_INFORMATION
    items = _trade_items(xml_bytes)
    module = items[0].find(f"tradeItemInformation/extension/{{{NS_MARKETING_INFORMATION}}}marketingInformationModule")
    assert module is not None
    message = module.find("marketingInformation/tradeItemMarketingMessage")
    assert message.text == "The TOWNSHEND wall light is made of white metal."
    assert message.get("languageCode") == "en"


def test_export_batch_skips_marketing_information_module_without_artikeltext(monkeypatch, product):
    monkeypatch.setattr(database, "get_connection", lambda: FakeConnection())
    monkeypatch.setattr(database, "get_product_by_matnr", lambda conn, matnr: product)
    monkeypatch.setattr(database, "get_brick_id", lambda conn, code: "10000552")
    monkeypatch.setattr(
        database, "get_active_mappings",
        lambda conn, brick_id: [{"pickid": "4.020", "pimfeld": "ZZSER"}],
    )
    monkeypatch.setattr(database, "get_gdsn_attribute_type", lambda conn, brick_id, pick_id: None)

    xml_bytes, _, _ = pipeline.export_batch(["62053"], ["eng"])

    items = _trade_items(xml_bytes)
    from gs1_export.gs1_exporter import NS_MARKETING_INFORMATION
    assert items[0].find(
        f"tradeItemInformation/extension/{{{NS_MARKETING_INFORMATION}}}marketingInformationModule"
    ) is None


def test_export_batch_only_builds_propertydescription_picks(monkeypatch, product):
    monkeypatch.setattr(database, "get_connection", lambda: FakeConnection())
    monkeypatch.setattr(database, "get_product_by_matnr", lambda conn, matnr: product)
    monkeypatch.setattr(database, "get_brick_id", lambda conn, code: "10000552")
    monkeypatch.setattr(
        database,
        "get_active_mappings",
        lambda conn, brick_id: [
            {"pickid": "4.012", "pimfeld": "ZZSER"},   # gs1_gpc_attribute_types says propertyCode
            {"pickid": "4.020", "pimfeld": "ZZSER"},   # gs1_gpc_attribute_types says propertyDescription
            {"pickid": "9.999", "pimfeld": "ZZSER"},   # not in gs1_gpc_attribute_types at all
        ],
    )
    gdsn_types = {"4.012": "propertyCode", "4.020": "propertyDescription"}
    monkeypatch.setattr(
        database, "get_gdsn_attribute_type",
        lambda conn, brick_id, pick_id: gdsn_types.get(pick_id),
    )
    # No gs1_pim_value_crosswalk entry for 4.012 -> stays skipped.
    monkeypatch.setattr(database, "get_pim_value_crosswalk_exact", lambda conn, pick_id, pim_code: None)
    monkeypatch.setattr(database, "get_pim_value_crosswalk_wildcard", lambda conn, pick_id: None)

    xml_bytes, _, _ = pipeline.export_batch(["62053"], ["eng"])

    items = _trade_items(xml_bytes)
    props = items[0].findall(
        "gdsnTradeItemClassification/additionalTradeItemClassification"
        "/additionalTradeItemClassificationValue/additionalTradeItemClassificationProperty"
    )
    pickids = [p.find("additionalTradeItemClassificationPropertyCode").text for p in props]
    # 4.012 (propertyCode, no crosswalk entry) is skipped; 4.020 (propertyDescription) and the
    # unclassified 9.999 (no gs1_gpc_attribute_types entry -> treated as propertyDescription) are built.
    assert pickids == ["4.020", "9.999"]


def test_export_batch_builds_property_code_via_crosswalk(monkeypatch, product):
    monkeypatch.setattr(database, "get_connection", lambda: FakeConnection())
    monkeypatch.setattr(database, "get_product_by_matnr", lambda conn, matnr: product)
    monkeypatch.setattr(database, "get_brick_id", lambda conn, code: "10000552")
    monkeypatch.setattr(
        database,
        "get_active_mappings",
        lambda conn, brick_id: [{"pickid": "4.199", "pimfeld": "ZZTYPEN"}],
    )
    monkeypatch.setattr(database, "get_gdsn_attribute_type", lambda conn, brick_id, pick_id: "propertyCode")
    monkeypatch.setattr(
        database, "get_pim_value_crosswalk_exact",
        lambda conn, pick_id, pim_code: "muurmontage_vast" if pim_code == "ZZTYPEN_EAC" else None,
    )
    monkeypatch.setattr(database, "get_pim_value_crosswalk_wildcard", lambda conn, pick_id: None)

    xml_bytes, _, _ = pipeline.export_batch(["62053"], ["eng"])

    items = _trade_items(xml_bytes)
    props = items[0].findall(
        "gdsnTradeItemClassification/additionalTradeItemClassification"
        "/additionalTradeItemClassificationValue/additionalTradeItemClassificationProperty"
    )
    assert len(props) == 1
    assert props[0].find("additionalTradeItemClassificationPropertyCode").text == "4.199"
    code_el = props[0].find("propertyCode")
    assert code_el.text == "muurmontage_vast"
    assert code_el.get("languageCode") is None
    assert props[0].find("propertyDescription") is None


@pytest.mark.parametrize(
    "matkl_code, expected",
    [
        ("MATKL_FSC100NEWCODE", "TRUE"),  # not in gs1_pim_value_crosswalk at all
        ("MATKL_888888", "FALSE"),  # has an (unused) crosswalk row also saying FALSE
        ("MATKL_ORDINARY", "FALSE"),
    ],
)
def test_export_batch_builds_property_code_4_226_via_fsc_substring_not_crosswalk(
    monkeypatch, product, matkl_code, expected
):
    """4.226 checks the raw code for "FSC" directly - gs1_pim_value_crosswalk
    is never consulted for this pick, so a code missing from (or even
    contradicting) the crosswalk table still resolves correctly."""
    monkeypatch.setattr(database, "get_connection", lambda: FakeConnection())
    monkeypatch.setattr(database, "get_product_by_matnr", lambda conn, matnr: product)
    monkeypatch.setattr(database, "get_brick_id", lambda conn, code: "10008403")
    monkeypatch.setattr(
        database,
        "get_active_mappings",
        lambda conn, brick_id: [{"pickid": "4.226", "pimfeld": "MATKL"}],
    )
    monkeypatch.setattr(database, "get_gdsn_attribute_type", lambda conn, brick_id, pick_id: "propertyCode")

    def boom_crosswalk(*a, **k):
        raise AssertionError("gs1_pim_value_crosswalk must not be consulted for pick 4.226")

    monkeypatch.setattr(database, "get_pim_value_crosswalk_exact", boom_crosswalk)
    monkeypatch.setattr(database, "get_pim_value_crosswalk_wildcard", boom_crosswalk)
    product["values"]["MATKL"] = [{"data": matkl_code}]

    xml_bytes, _, _ = pipeline.export_batch(["62053"], ["eng"])

    items = _trade_items(xml_bytes)
    props = items[0].findall(
        "gdsnTradeItemClassification/additionalTradeItemClassification"
        "/additionalTradeItemClassificationValue/additionalTradeItemClassificationProperty"
    )
    assert len(props) == 1
    assert props[0].find("propertyCode").text == expected


def test_export_batch_property_code_uses_wildcard_when_pim_field_entirely_unset(monkeypatch, product):
    """A PIM field that was never populated for this article (e.g. ZZDIMMR
    unset) should still get the Pick's wildcard default (e.g. "* -> FALSE"),
    not be silently skipped - matches the same "no exact match -> wildcard"
    rule already used when a raw code exists but doesn't match anything."""
    monkeypatch.setattr(database, "get_connection", lambda: FakeConnection())
    monkeypatch.setattr(database, "get_product_by_matnr", lambda conn, matnr: product)
    monkeypatch.setattr(database, "get_brick_id", lambda conn, code: "10000552")
    monkeypatch.setattr(
        database, "get_active_mappings",
        lambda conn, brick_id: [{"pickid": "4.290", "pimfeld": "ZZDIMMR_DOES_NOT_EXIST"}],
    )
    monkeypatch.setattr(database, "get_gdsn_attribute_type", lambda conn, brick_id, pick_id: "propertyCode")
    monkeypatch.setattr(database, "get_pim_value_crosswalk_exact", lambda conn, pick_id, pim_code: None)
    monkeypatch.setattr(database, "get_pim_value_crosswalk_wildcard", lambda conn, pick_id: "FALSE")

    xml_bytes, _, _ = pipeline.export_batch(["62053"], ["eng"])

    items = _trade_items(xml_bytes)
    props = items[0].findall(
        "gdsnTradeItemClassification/additionalTradeItemClassification"
        "/additionalTradeItemClassificationValue/additionalTradeItemClassificationProperty"
    )
    assert len(props) == 1
    assert props[0].find("propertyCode").text == "FALSE"


def test_export_batch_property_code_still_skips_when_unset_and_no_wildcard(monkeypatch, product):
    monkeypatch.setattr(database, "get_connection", lambda: FakeConnection())
    monkeypatch.setattr(database, "get_product_by_matnr", lambda conn, matnr: product)
    monkeypatch.setattr(database, "get_brick_id", lambda conn, code: "10000552")
    monkeypatch.setattr(
        database, "get_active_mappings",
        lambda conn, brick_id: [{"pickid": "4.290", "pimfeld": "ZZDIMMR_DOES_NOT_EXIST"}],
    )
    monkeypatch.setattr(database, "get_gdsn_attribute_type", lambda conn, brick_id, pick_id: "propertyCode")
    monkeypatch.setattr(database, "get_pim_value_crosswalk_exact", lambda conn, pick_id, pim_code: None)
    monkeypatch.setattr(database, "get_pim_value_crosswalk_wildcard", lambda conn, pick_id: None)

    xml_bytes, _, _ = pipeline.export_batch(["62053"], ["eng"])

    items = _trade_items(xml_bytes)
    assert items[0].findall(
        "gdsnTradeItemClassification/additionalTradeItemClassification"
        "/additionalTradeItemClassificationValue/additionalTradeItemClassificationProperty"
    ) == []


def test_export_batch_property_code_checks_all_multiselect_codes(monkeypatch, product):
    """Multiselect fields can have several selected codes; the crosswalk must be
    checked against ALL of them (not just the first) before falling back to the
    wildcard - e.g. Pick 4.418: TRUE if B2C_FILTER_FUNCTION_REMOTE OR
    B2C_FILTER_FUNCTION_FB_INKL is selected, regardless of order."""
    monkeypatch.setattr(database, "get_connection", lambda: FakeConnection())
    product = dict(product)
    product["values"] = dict(product["values"])
    product["values"]["B2C_Filter_Function"] = [{
        "data": ["B2C_FILTER_FUNCTION_TIMER", "B2C_FILTER_FUNCTION_REMOTE"],
        "scope": None,
        "locale": None,
        "attribute_type": "pim_catalog_multiselect",
    }]
    monkeypatch.setattr(database, "get_product_by_matnr", lambda conn, matnr: product)
    monkeypatch.setattr(database, "get_brick_id", lambda conn, code: "10000552")
    monkeypatch.setattr(
        database,
        "get_active_mappings",
        lambda conn, brick_id: [{"pickid": "4.418", "pimfeld": "B2C_Filter_Function"}],
    )
    monkeypatch.setattr(database, "get_gdsn_attribute_type", lambda conn, brick_id, pick_id: "propertyCode")
    crosswalk = {"B2C_FILTER_FUNCTION_FB_INKL": "TRUE", "B2C_FILTER_FUNCTION_REMOTE": "TRUE"}
    monkeypatch.setattr(
        database, "get_pim_value_crosswalk_exact",
        lambda conn, pick_id, pim_code: crosswalk.get(pim_code),
    )
    monkeypatch.setattr(database, "get_pim_value_crosswalk_wildcard", lambda conn, pick_id: "FALSE")

    xml_bytes, _, _ = pipeline.export_batch(["62053"], ["eng"])

    items = _trade_items(xml_bytes)
    props = items[0].findall(
        "gdsnTradeItemClassification/additionalTradeItemClassification"
        "/additionalTradeItemClassificationValue/additionalTradeItemClassificationProperty"
    )
    assert len(props) == 1
    # TIMER (first selected) has no exact match, but REMOTE (second) does -> TRUE, not the wildcard FALSE.
    assert props[0].find("propertyCode").text == "TRUE"


def test_export_batch_builds_property_measurement(monkeypatch, product):
    monkeypatch.setattr(database, "get_connection", lambda: FakeConnection())
    monkeypatch.setattr(database, "get_product_by_matnr", lambda conn, matnr: product)
    monkeypatch.setattr(database, "get_brick_id", lambda conn, code: "10008403")
    monkeypatch.setattr(
        database,
        "get_active_mappings",
        lambda conn, brick_id: [{"pickid": "4.052", "pimfeld": "BRGEW"}],
    )
    monkeypatch.setattr(database, "get_gdsn_attribute_type", lambda conn, brick_id, pick_id: "propertyMeasurement")
    monkeypatch.setattr(database, "get_measurement_unit_code", lambda conn, brick_id, pick_id: "WTT")
    # BRGEW is a plain number field, not select-sourced -> no crosswalk entry, falls through to direct parsing.
    monkeypatch.setattr(database, "get_pim_value_crosswalk_exact", lambda conn, pick_id, pim_code: None)
    monkeypatch.setattr(database, "get_pim_value_crosswalk_wildcard", lambda conn, pick_id: None)

    xml_bytes, _, _ = pipeline.export_batch(["62053"], ["eng"])

    items = _trade_items(xml_bytes)
    props = items[0].findall(
        "gdsnTradeItemClassification/additionalTradeItemClassification"
        "/additionalTradeItemClassificationValue/additionalTradeItemClassificationProperty"
    )
    assert len(props) == 1
    assert props[0].find("additionalTradeItemClassificationPropertyCode").text == "4.052"
    measurement_el = props[0].find("propertyMeasurement")
    assert measurement_el.text == "0"  # BRGEW fixture value "0.4430" -> int(0.443) == 0
    assert measurement_el.get("measurementUnitCode") == "WTT"


def test_export_batch_skips_property_measurement_without_unit_code(monkeypatch, product):
    monkeypatch.setattr(database, "get_connection", lambda: FakeConnection())
    monkeypatch.setattr(database, "get_product_by_matnr", lambda conn, matnr: product)
    monkeypatch.setattr(database, "get_brick_id", lambda conn, code: "10008403")
    monkeypatch.setattr(
        database,
        "get_active_mappings",
        lambda conn, brick_id: [{"pickid": "4.052", "pimfeld": "BRGEW"}],
    )
    monkeypatch.setattr(database, "get_gdsn_attribute_type", lambda conn, brick_id, pick_id: "propertyMeasurement")
    monkeypatch.setattr(database, "get_measurement_unit_code", lambda conn, brick_id, pick_id: None)

    xml_bytes, _, _ = pipeline.export_batch(["62053"], ["eng"])

    assert _trade_items(xml_bytes)[0].findall(
        "gdsnTradeItemClassification/additionalTradeItemClassification"
        "/additionalTradeItemClassificationValue/additionalTradeItemClassificationProperty"
    ) == []


def test_export_batch_builds_property_measurement_via_crosswalk(monkeypatch, product):
    """4.769/ZZNETZS: the source is a select field (e.g. "ZZNETZS_02"), not a
    plain number - the numeric value has to come from the crosswalk, not from
    parsing the raw code itself."""
    monkeypatch.setattr(database, "get_connection", lambda: FakeConnection())
    product = dict(product)
    product["values"] = dict(product["values"])
    product["values"]["ZZNETZS"] = [{
        "data": "ZZNETZS_02",
        "scope": None,
        "locale": None,
        "attribute_type": "pim_catalog_simpleselect",
        "linked_data": {"code": "ZZNETZS_02", "labels": {"en_GB": "[02] 220-240V,50/60Hz"}},
    }]
    monkeypatch.setattr(database, "get_product_by_matnr", lambda conn, matnr: product)
    monkeypatch.setattr(database, "get_brick_id", lambda conn, code: "10008403")
    monkeypatch.setattr(
        database,
        "get_active_mappings",
        lambda conn, brick_id: [{"pickid": "4.769", "pimfeld": "ZZNETZS"}],
    )
    monkeypatch.setattr(database, "get_gdsn_attribute_type", lambda conn, brick_id, pick_id: "propertyMeasurement")
    monkeypatch.setattr(database, "get_measurement_unit_code", lambda conn, brick_id, pick_id: "VLT")
    monkeypatch.setattr(
        database, "get_pim_value_crosswalk_exact",
        lambda conn, pick_id, pim_code: "230" if pim_code == "ZZNETZS_02" else None,
    )
    monkeypatch.setattr(database, "get_pim_value_crosswalk_wildcard", lambda conn, pick_id: None)

    xml_bytes, _, _ = pipeline.export_batch(["62053"], ["eng"])

    items = _trade_items(xml_bytes)
    props = items[0].findall(
        "gdsnTradeItemClassification/additionalTradeItemClassification"
        "/additionalTradeItemClassificationValue/additionalTradeItemClassificationProperty"
    )
    assert len(props) == 1
    measurement_el = props[0].find("propertyMeasurement")
    assert measurement_el.text == "230"
    assert measurement_el.get("measurementUnitCode") == "VLT"


def test_export_batch_builds_property_integer(monkeypatch, product):
    monkeypatch.setattr(database, "get_connection", lambda: FakeConnection())
    monkeypatch.setattr(database, "get_product_by_matnr", lambda conn, matnr: product)
    monkeypatch.setattr(database, "get_brick_id", lambda conn, code: "10008403")
    monkeypatch.setattr(
        database,
        "get_active_mappings",
        lambda conn, brick_id: [{"pickid": "4.435", "pimfeld": "BRGEW"}],
    )
    monkeypatch.setattr(database, "get_gdsn_attribute_type", lambda conn, brick_id, pick_id: "propertyInteger")

    xml_bytes, _, _ = pipeline.export_batch(["62053"], ["eng"])

    items = _trade_items(xml_bytes)
    props = items[0].findall(
        "gdsnTradeItemClassification/additionalTradeItemClassification"
        "/additionalTradeItemClassificationValue/additionalTradeItemClassificationProperty"
    )
    assert len(props) == 1
    assert props[0].find("additionalTradeItemClassificationPropertyCode").text == "4.435"
    int_el = props[0].find("propertyInteger")
    assert int_el.text == "0"  # BRGEW fixture value "0.4430" -> int(0.443) == 0
    assert int_el.get("measurementUnitCode") is None


def test_export_batch_skips_property_integer_without_number(monkeypatch, product):
    monkeypatch.setattr(database, "get_connection", lambda: FakeConnection())
    monkeypatch.setattr(database, "get_product_by_matnr", lambda conn, matnr: product)
    monkeypatch.setattr(database, "get_brick_id", lambda conn, code: "10008403")
    monkeypatch.setattr(
        database,
        "get_active_mappings",
        lambda conn, brick_id: [{"pickid": "4.435", "pimfeld": "DOES_NOT_EXIST"}],
    )
    monkeypatch.setattr(database, "get_gdsn_attribute_type", lambda conn, brick_id, pick_id: "propertyInteger")

    xml_bytes, _, _ = pipeline.export_batch(["62053"], ["eng"])

    assert _trade_items(xml_bytes)[0].findall(
        "gdsnTradeItemClassification/additionalTradeItemClassification"
        "/additionalTradeItemClassificationValue/additionalTradeItemClassificationProperty"
    ) == []


def test_export_batch_records_export_history_for_successful_matnr(monkeypatch, product):
    monkeypatch.setattr(database, "get_connection", lambda: FakeConnection())
    monkeypatch.setattr(database, "get_product_by_matnr", lambda conn, matnr: product)
    monkeypatch.setattr(database, "get_product_updated_at", lambda conn, matnr: "2026-08-21T10:00:00Z")
    monkeypatch.setattr(database, "get_brick_id", lambda conn, code: "10000552")
    monkeypatch.setattr(
        database, "get_active_mappings",
        lambda conn, brick_id: [{"pickid": "4.020", "pimfeld": "ZZSER"}],
    )
    monkeypatch.setattr(database, "get_gdsn_attribute_type", lambda conn, brick_id, pick_id: None)

    calls = []
    monkeypatch.setattr(database, "upsert_export_history", lambda conn, *args: calls.append(args))

    pipeline.export_batch(["62053"], ["eng"], exported_by="user@eglo.com")

    assert calls == [("62053", "10000552", "2026-08-21T10:00:00Z", "user@eglo.com")]


def test_export_batch_does_not_record_history_for_unknown_matnr(monkeypatch):
    monkeypatch.setattr(database, "get_connection", lambda: FakeConnection())
    monkeypatch.setattr(database, "get_product_by_matnr", lambda conn, matnr: None)
    monkeypatch.setattr(validator, "validate_xml", lambda xml_bytes: [])

    calls = []
    monkeypatch.setattr(database, "upsert_export_history", lambda conn, *args: calls.append(args))

    pipeline.export_batch(["999999"], ["eng"])

    assert calls == []


def test_get_changed_articles_delegates_to_database(monkeypatch):
    monkeypatch.setattr(database, "get_connection", lambda: FakeConnection())
    monkeypatch.setattr(database, "get_changed_articles", lambda conn: [{"matnr": "43706"}])

    assert pipeline.get_changed_articles() == [{"matnr": "43706"}]


def test_get_changed_articles_raises_pipeline_error_on_connection_failure(monkeypatch):
    def boom():
        raise OSError("connection refused")

    monkeypatch.setattr(database, "get_connection", boom)

    with pytest.raises(pipeline.PipelineError):
        pipeline.get_changed_articles()


def test_save_export_file_delegates_to_database(monkeypatch):
    monkeypatch.setattr(database, "get_connection", lambda: FakeConnection())
    calls = []
    monkeypatch.setattr(database, "save_export_file", lambda conn, *args: calls.append(args))

    pipeline.save_export_file("gs1_export_20260828.xml", "user@eglo.com", ["43706"], b"<xml/>")

    assert calls == [("gs1_export_20260828.xml", "user@eglo.com", ["43706"], b"<xml/>")]


def test_save_export_file_swallows_connection_failure(monkeypatch):
    def boom():
        raise OSError("connection refused")

    monkeypatch.setattr(database, "get_connection", boom)

    # Must not raise - archiving must never break an otherwise-successful export/download.
    pipeline.save_export_file("gs1_export_20260828.xml", "user@eglo.com", ["43706"], b"<xml/>")


def test_list_export_files_delegates_to_database(monkeypatch):
    monkeypatch.setattr(database, "get_connection", lambda: FakeConnection())
    monkeypatch.setattr(database, "list_export_files", lambda conn, exported_by: [{"id": 7}])

    assert pipeline.list_export_files("user@eglo.com") == [{"id": 7}]


def test_list_export_files_raises_pipeline_error_on_connection_failure(monkeypatch):
    def boom():
        raise OSError("connection refused")

    monkeypatch.setattr(database, "get_connection", boom)

    with pytest.raises(pipeline.PipelineError):
        pipeline.list_export_files("user@eglo.com")


def test_get_export_file_delegates_to_database(monkeypatch):
    monkeypatch.setattr(database, "get_connection", lambda: FakeConnection())
    monkeypatch.setattr(
        database, "get_export_file",
        lambda conn, file_id, exported_by: {"filename": "x.xml", "xml_content": b"<xml/>"},
    )

    assert pipeline.get_export_file(7, "user@eglo.com") == {"filename": "x.xml", "xml_content": b"<xml/>"}


def test_get_export_file_raises_pipeline_error_on_connection_failure(monkeypatch):
    def boom():
        raise OSError("connection refused")

    monkeypatch.setattr(database, "get_connection", boom)

    with pytest.raises(pipeline.PipelineError):
        pipeline.get_export_file(7, "user@eglo.com")


# --- SAP-sourced extension modules (weight/dimensions, description, origin) ---

def test_weight_in_grams_converts_kg():
    assert pipeline._weight_in_grams(0.595, "KG") == 595


def test_weight_in_grams_passes_through_grams():
    assert pipeline._weight_in_grams(595, "G") == 595


def test_weight_in_grams_none_for_zero_or_unknown_unit():
    assert pipeline._weight_in_grams(0, "KG") is None
    assert pipeline._weight_in_grams(1, "LB") is None
    assert pipeline._weight_in_grams(None, "KG") is None


def test_dimension_in_mm_converts_cm():
    assert pipeline._dimension_in_mm(18.5, "CM") == 185


def test_dimension_in_mm_none_for_zero_or_unknown_unit():
    assert pipeline._dimension_in_mm(0, "MM") is None
    assert pipeline._dimension_in_mm(5, "") is None


def test_package_dimension_in_mm_passes_through():
    assert pipeline._package_dimension_in_mm(185) == 185


def test_package_dimension_in_mm_none_for_zero_or_missing():
    assert pipeline._package_dimension_in_mm(0) is None
    assert pipeline._package_dimension_in_mm(None) is None


class FakeHanaConnection:
    def close(self):
        pass


def test_build_sap_extension_modules_builds_all_three(monkeypatch):
    monkeypatch.setattr(
        sap_database, "get_material_measurements",
        lambda conn, matnr: {
            "gross_weight": 0.665, "net_weight": 0.59, "weight_unit": "KG",
            "length": 18.5, "width": 18.5, "height": 21.5, "dimension_unit": "CM",
            "volume": 0, "volume_unit": "",
        },
    )
    monkeypatch.setattr(
        sap_database, "get_material_descriptions",
        lambda conn, matnr: {"E": "WL/1 white/oak-optic TOWNSHEND", "N": "WL/1 WEISS/EICHE-OPTIK TOWNSHEND"},
    )
    monkeypatch.setattr(
        sap_database, "get_material_origin",
        lambda conn, matnr, werks: {"country_of_origin": "CN", "customs_tariff_number": "9405199090"},
    )

    modules = pipeline._build_sap_extension_modules(
        FakeHanaConnection(), "43706", "0090",
        [("eng", {"gs1_code": "en"}), ("nl", {"gs1_code": "nl"})],
        sap_language_config={"E": "en", "N": "nl", "F": "fr"},
        country_code_config={"CN": "156", "IN": "356"},
    )

    assert len(modules) == 3
    localnames = [etree.QName(m).localname for m in modules]
    assert localnames == ["tradeItemMeasurementsModule", "tradeItemDescriptionModule", "placeOfItemActivityModule"]
    measurements = modules[0].find("tradeItemMeasurements")
    assert measurements.find("depth").text == "185"
    assert measurements.find("tradeItemWeight/grossWeight").text == "665"
    descriptions = modules[1].find("tradeItemDescriptionInformation")
    assert {d.get("languageCode") for d in descriptions.findall("descriptionShort")} == {"en", "nl"}
    assert modules[2].find("placeOfProductActivity/countryOfOrigin/countryCode").text == "156"
    assert modules[2].find("importClassification/importClassificationValue").text == "94051990"


def test_build_sap_extension_modules_prefers_package_dimensions_over_length_width_height(monkeypatch):
    monkeypatch.setattr(
        sap_database, "get_material_measurements",
        lambda conn, matnr: {
            "gross_weight": None, "net_weight": None, "weight_unit": "KG",
            "length": 18.5, "width": 18.5, "height": 21.5, "dimension_unit": "CM",
            "volume": 0, "volume_unit": "",
            "package_length": 200, "package_width": 190, "package_depth": 220,
        },
    )
    monkeypatch.setattr(sap_database, "get_material_descriptions", lambda conn, matnr: {})
    monkeypatch.setattr(sap_database, "get_material_origin", lambda conn, matnr, werks: None)

    modules = pipeline._build_sap_extension_modules(
        FakeHanaConnection(), "43706", "", [], sap_language_config={}, country_code_config={},
    )

    measurements = modules[0].find("tradeItemMeasurements")
    assert measurements.find("depth").text == "200"
    assert measurements.find("width").text == "190"
    assert measurements.find("height").text == "220"


def test_build_sap_extension_modules_returns_empty_list_when_hana_unreachable():
    modules = pipeline._build_sap_extension_modules(
        None, "43706", "0090", [], sap_language_config={}, country_code_config={},
    )
    assert modules == []


def test_build_sap_extension_modules_skips_origin_without_werks(monkeypatch):
    monkeypatch.setattr(sap_database, "get_material_measurements", lambda conn, matnr: None)
    monkeypatch.setattr(sap_database, "get_material_descriptions", lambda conn, matnr: {})
    origin_calls = []
    monkeypatch.setattr(
        sap_database, "get_material_origin",
        lambda conn, matnr, werks: origin_calls.append((matnr, werks)),
    )

    modules = pipeline._build_sap_extension_modules(
        FakeHanaConnection(), "43706", "", [], sap_language_config={}, country_code_config={},
    )

    assert modules == []
    assert origin_calls == []


def test_export_batch_includes_sap_modules_when_hana_available(monkeypatch, product):
    monkeypatch.setattr(database, "get_connection", lambda: FakeConnection())
    monkeypatch.setattr(database, "get_product_by_matnr", lambda conn, matnr: product)
    monkeypatch.setattr(database, "get_brick_id", lambda conn, code: "10000552")
    monkeypatch.setattr(
        database, "get_active_mappings",
        lambda conn, brick_id: [{"pickid": "4.020", "pimfeld": "ZZSER"}],
    )
    monkeypatch.setattr(database, "get_gdsn_attribute_type", lambda conn, brick_id, pick_id: None)
    monkeypatch.setattr(sap_database, "get_connection", lambda: FakeHanaConnection())
    monkeypatch.setattr(
        sap_database, "get_material_measurements",
        lambda conn, matnr: {
            "gross_weight": 0.595, "net_weight": None, "weight_unit": "KG",
            "length": None, "width": None, "height": None, "dimension_unit": "",
            "volume": None, "volume_unit": "",
        },
    )
    monkeypatch.setattr(sap_database, "get_material_descriptions", lambda conn, matnr: {})
    monkeypatch.setattr(sap_database, "get_material_origin", lambda conn, matnr, werks: None)

    xml_bytes, _, _ = pipeline.export_batch(["62053"], ["eng"], werks="0090")

    item = _trade_items(xml_bytes)[0]
    assert item.find("tradeItemInformation/extension/{urn:gs1:gdsn:trade_item_measurements:xsd:3}"
                      "tradeItemMeasurementsModule/tradeItemMeasurements/tradeItemWeight/grossWeight").text == "595"


def test_export_batch_warns_when_hana_connection_fails(monkeypatch, product):
    monkeypatch.setattr(database, "get_connection", lambda: FakeConnection())
    monkeypatch.setattr(database, "get_product_by_matnr", lambda conn, matnr: product)
    monkeypatch.setattr(database, "get_brick_id", lambda conn, code: "10000552")
    monkeypatch.setattr(database, "get_active_mappings", lambda conn, brick_id: [])
    # _stub_sap_connection (autouse) already makes sap_database.get_connection raise.

    _, warnings, _ = pipeline.export_batch(["62053"], ["eng"])

    assert any("SAP-HANA-Verbindung" in w for w in warnings)
