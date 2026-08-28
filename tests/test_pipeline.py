import json
from pathlib import Path

import pytest
from lxml import etree

from gs1_export import database, pipeline, validator
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
    # Same for vkorg_vat_mapping.json -> warning, no tradeItemInformation/extension.
    assert any("dutyFeeTaxInformationModule" in w for w in warnings)
    assert items[0].find("tradeItemInformation") is None


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
