import json
from pathlib import Path

import pytest

from gs1_export.pim_reader import (
    get_pim_number_as_int,
    get_pim_raw_code,
    get_pim_raw_codes,
    get_pim_value,
    get_zztypen_code,
    resolve_text,
)

FIXTURE = Path(__file__).with_name("fixtures") / "sample_product.json"


@pytest.fixture
def product():
    with open(FIXTURE, "r", encoding="utf-8") as f:
        return json.load(f)


def test_missing_field_returns_none(product):
    assert get_pim_value(product, "DOES_NOT_EXIST") is None


def test_plain_text(product):
    value = get_pim_value(product, "ZZSER")
    assert value["attribute_type"] == "pim_catalog_text"
    assert resolve_text(value, "en_GB") == "PASTERI PRO"
    assert resolve_text(value, "fr_FR") == "PASTERI PRO"  # no per-language variant -> fallback


def test_number_and_boolean(product):
    weight = get_pim_value(product, "BRGEW")
    assert resolve_text(weight, "en_GB") == "0.4430"

    sap_sync = get_pim_value(product, "SAP_SYNC")
    assert resolve_text(sap_sync, "en_GB") == "True"


def test_simpleselect_labels_per_language(product):
    value = get_pim_value(product, "ZZGLASF")
    assert value["attribute_type"] == "pim_catalog_simpleselect"
    assert resolve_text(value, "en_GB") == "[C12] cappuccino, gold"
    assert resolve_text(value, "fr_FR") == "[C12] cappuccino, or"
    assert resolve_text(value, "nl_NL") == "[C12] cappucino, goud"


def test_multiselect_joins_selected_labels(product):
    value = get_pim_value(product, "B2C_FILTER_COLOR")
    assert value["attribute_type"] == "pim_catalog_multiselect"
    assert resolve_text(value, "en_GB") == "Gold"
    assert resolve_text(value, "fr_FR") == "Or"


def test_language_dependent_text(product):
    value = get_pim_value(product, "MAKTX")
    assert resolve_text(value, "fr_FR") == "Abat-jour H-250, B-250 CAPPUCCINO/OR"
    assert resolve_text(value, "en_GB") == "SCHIRM H-250, B-250 CAPPUCCINO/GOLD"
    assert resolve_text(value, "de_AT") == "SCHIRM H-250, B-250 CAPPUCCINO/GOLD"


def test_metric(product):
    value = get_pim_value(product, "pim_bounding_box_width")
    assert value["metric"]["amount"] == "110.00"
    assert resolve_text(value, "en_GB") == "110.00 mm"


def test_reference_entity_falls_back_to_plain_text(product):
    value = get_pim_value(product, "PIM_glas_shadecolour_main")
    assert resolve_text(value, "en_GB") == "cappuccino"


def test_get_zztypen_code_returns_raw_code_not_label(product):
    assert get_zztypen_code(product) == "ZZTYPEN_EAC"


def test_get_pim_raw_codes_returns_all_selected_codes_for_multiselect(product):
    # Fixture only has one selected value, but the function must return a list
    # (not just the first item) - required for "true if ANY selected" crosswalk rules.
    assert get_pim_raw_codes(product, "B2C_FILTER_COLOR") == ["B2C_FILTER_COLOR_GOLD"]
    assert get_pim_raw_code(product, "B2C_FILTER_COLOR") == "B2C_FILTER_COLOR_GOLD"


def test_get_pim_number_as_int(product):
    # BRGEW is pim_catalog_number with data: "0.4430" in the fixture.
    assert get_pim_number_as_int(product, "BRGEW") == 0
    assert get_pim_number_as_int(product, "DOES_NOT_EXIST") is None


def test_get_pim_number_as_int_strips_unit_suffix_and_comma_decimal(product):
    product = dict(product)
    product["values"] = dict(product["values"])
    product["values"]["PIM_MAX_POWER_INCL_TRANSFORMER"] = [{
        "data": "10W", "scope": None, "locale": None, "attribute_type": "pim_catalog_text",
    }]
    product["values"]["PIM_FAS_05_1"] = [{
        "data": "32,5", "scope": None, "locale": None, "attribute_type": "pim_catalog_text",
    }]
    assert get_pim_number_as_int(product, "PIM_MAX_POWER_INCL_TRANSFORMER") == 10
    assert get_pim_number_as_int(product, "PIM_FAS_05_1") == 32


def test_get_pim_raw_codes_handles_boolean_fields(product):
    # SAP_SYNC is pim_catalog_boolean with data: true in the fixture.
    assert get_pim_raw_codes(product, "SAP_SYNC") == ["true"]
    assert get_pim_raw_code(product, "SAP_SYNC") == "true"


def test_get_pim_raw_codes_empty_for_missing_field(product):
    assert get_pim_raw_codes(product, "DOES_NOT_EXIST") == []
    assert get_pim_raw_code(product, "DOES_NOT_EXIST") is None


def test_field_lookup_is_case_insensitive(product):
    # public.gs1_mapping.pim_field values have been observed with different casing
    # than the PIM attribute code itself (e.g. "B2C_Filter_Color" vs "B2C_FILTER_COLOR").
    exact = get_pim_value(product, "B2C_FILTER_COLOR")
    mixed_case = get_pim_value(product, "B2C_Filter_Color")
    assert mixed_case is not None
    assert mixed_case == exact
