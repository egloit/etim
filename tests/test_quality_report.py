import io

import pytest
from openpyxl import load_workbook

import quality_report as q
from etim10_export.feature_engine import RuleSet
from gs1_export import gs1_mapping
from gs1_export import pipeline as gs1_pipeline

CLASS = "EC002892"


def _product(**values):
    def entry(data, attribute_type, linked=None):
        e = {"data": data, "scope": None, "locale": None, "attribute_type": attribute_type}
        if linked:
            e["linked_data"] = linked
        return [e]

    out = {}
    for code, data in values.items():
        if isinstance(data, list):
            out[code] = entry(data, "pim_catalog_multiselect")
        elif isinstance(data, bool):
            out[code] = entry(data, "pim_catalog_boolean")
        elif isinstance(data, (int, float)):
            out[code] = entry(data, "pim_catalog_number")
        else:
            out[code] = entry(data, "pim_catalog_simpleselect", {"labels": {"en_GB": f"label {data}"}})
    return {"values": out}


def _rule(feature_id, source_type, **kw):
    rule = dict(class_id=CLASS, feature_id=feature_id, slot="value", source_type=source_type, pim_field=None,
                crosswalk_set=None, fix_value=None, transform=None, required_pim_field=None, sort_order=10)
    rule.update(kw)
    return rule


@pytest.fixture
def rules():
    return RuleSet(
        class_by_zztyp={"ZZTYPEN_DEL": CLASS},
        rules_by_class={CLASS: [
            _rule("EF000187", "crosswalk", pim_field="ZZNETZS", crosswalk_set="netz"),
            _rule("EF012154", "crosswalk", pim_field="PIM_dimmable_with", crosswalk_set="dali"),
            _rule("EF000136", "crosswalk", pim_field="ZZGHFAR", crosswalk_set="farbe"),
            _rule("EF000015", "copy", pim_field="ZZLMDUR"),
        ]},
        crosswalk={
            "netz": {"ZZNETZS_01": "EV000460"},
            "dali": {"PIM_dimmable_with_DALI": "true", "*": "false"},
            "farbe": {"ZZGHFAR_X": "EV999999"},
        },
        model_features={
            (CLASS, "EF000187"): {"type": "A", "unit": None},
            (CLASS, "EF012154"): {"type": "L", "unit": None},
            (CLASS, "EF000136"): {"type": "A", "unit": None},
            (CLASS, "EF000015"): {"type": "N", "unit": "EU570448"},
            (CLASS, "EF000664"): {"type": "L", "unit": None},
        },
        allowed_values={(CLASS, "EF000187"): {"EV000460"}, (CLASS, "EF000136"): {"EV000080"}},
        feature_names={"EF000187": "Voltage type", "EF012154": "Dimming DALI", "EF000136": "Housing colour",
                       "EF000015": "Diameter", "EF000664": "Suitable for wall mounting"},
        value_names={"EV000460": "AC"},
    )


MODEL = [
    {"feature_id": "EF000664", "type": "L", "unit_id": None, "unit_desc": None, "desc": "Suitable for wall mounting"},
    {"feature_id": "EF000187", "type": "A", "unit_id": None, "unit_desc": None, "desc": "Voltage type"},
    {"feature_id": "EF012154", "type": "L", "unit_id": None, "unit_desc": None, "desc": "Dimming DALI"},
    {"feature_id": "EF000136", "type": "A", "unit_id": None, "unit_desc": None, "desc": "Housing colour"},
    {"feature_id": "EF000015", "type": "N", "unit_id": "EU570448", "unit_desc": "mm", "desc": "Diameter"},
]


def test_parse_matnrs():
    assert q.parse_matnrs(" 32048, 89534;900092\n32048  1 ") == ["32048", "89534", "900092", "1"]
    assert q.parse_matnrs("") == []


def test_pim_display_select_with_label_and_text():
    product = _product(ZZNETZS="ZZNETZS_01", ZZLMDUR=12.5, PIM_X=True)
    assert q.pim_display(product, "ZZNETZS") == "ZZNETZS_01 (label ZZNETZS_01)"
    assert q.pim_display(product, "ZZLMDUR") == "12.5"
    assert q.pim_display(product, "PIM_X") == "true"
    assert q.pim_display(product, "MISSING") == ""
    product["values"]["MAKTX"] = [{"locale": "de_DE", "data": "Leuchte", "attribute_type": "pim_catalog_text"},
                                  {"locale": "en_GB", "data": "Lamp", "attribute_type": "pim_catalog_text"}]
    assert q.pim_display(product, "MAKTX") == "Lamp [en_GB]"
    assert q.pim_display(product, "MAKTX", show_locale=False) == "Lamp"


def test_etim_rows_cover_every_class_feature_with_status(rules):
    product = _product(ZZNETZS="ZZNETZS_01", ZZGHFAR="ZZGHFAR_X")
    rows, _ = q._etim_feature_rows("4711", product, CLASS, rules, MODEL, None)
    by_id = {r["feature_id"]: r for r in rows}

    assert [r["feature_id"] for r in rows] == [m["feature_id"] for m in MODEL]
    assert by_id["EF000664"]["status"] == q.ST_NO_MAPPING
    assert by_id["EF000664"]["feature"] == "Suitable for wall mounting"

    ok = by_id["EF000187"]
    assert (ok["status"], ok["value"], ok["value_desc"], ok["pim_field"]) == (q.ST_OK, "EV000460", "AC", "ZZNETZS")
    assert ok["pim_value"] == "ZZNETZS_01 (label ZZNETZS_01)"

    default = by_id["EF012154"]
    assert default["status"] == q.ST_DEFAULT
    assert (default["value"], default["value_desc"], default["note"]) == ("false", "No", "PIM field empty")

    dropped = by_id["EF000136"]
    assert dropped["status"] == q.ST_DROPPED and dropped["value"] == "EV999999"
    assert dropped["note"] == "Value 'EV999999' is not allowed for EF000136 in EC002892"

    assert by_id["EF000015"]["status"] == q.ST_EMPTY


def test_etim_rows_fallback_value_source(rules):
    rows, _ = q._etim_feature_rows("4711", _product(), CLASS, rules, MODEL,
                                   {"EF000015": {"value": "120", "value2": "", "source": "webcenter"}})
    row = next(r for r in rows if r["feature_id"] == "EF000015")
    assert (row["status"], row["source"], row["value_desc"]) == (q.ST_OK, "fallback value (webcenter)", "120 mm")


def test_resolve_pick_marks_crosswalk_default(monkeypatch):
    monkeypatch.setattr(gs1_mapping, "get_gdsn_attribute_type", lambda conn, b, p: "propertyCode")
    monkeypatch.setattr(gs1_mapping, "get_pim_value_crosswalk_exact", lambda conn, p, code: None)
    monkeypatch.setattr(gs1_mapping, "get_pim_value_crosswalk_wildcard", lambda conn, p: "FALSE")
    pv = gs1_pipeline.resolve_pick(None, "4711", _product(ZZX="ZZX_1"), "100", "4.290", "ZZX", [])
    assert (pv.value, pv.source, pv.pim_codes) == ("FALSE", gs1_pipeline.SOURCE_CROSSWALK_DEFAULT, ["ZZX_1"])

    monkeypatch.setattr(gs1_mapping, "get_pim_value_crosswalk_exact", lambda conn, p, code: "TRUE")
    pv = gs1_pipeline.resolve_pick(None, "4711", _product(ZZX="ZZX_1"), "100", "4.290", "ZZX", [])
    assert (pv.value, pv.source) == ("TRUE", gs1_pipeline.SOURCE_CROSSWALK)


def test_resolve_pick_measurement_falls_back_to_manual_value(monkeypatch):
    monkeypatch.setattr(gs1_mapping, "get_gdsn_attribute_type", lambda conn, b, p: "propertyMeasurement")
    monkeypatch.setattr(gs1_mapping, "get_measurement_unit_code", lambda conn, b, p: "MMT")
    monkeypatch.setattr(gs1_mapping, "get_manual_property_value", lambda conn, m, p: "12,5")
    pv = gs1_pipeline.resolve_pick(None, "4711", _product(), "100", "4.100", "ZZLMDUR", [])
    assert (pv.value, pv.unit_code, pv.source) == (13, "MMT", gs1_pipeline.SOURCE_MANUAL)

    monkeypatch.setattr(gs1_mapping, "get_measurement_unit_code", lambda conn, b, p: None)
    pv = gs1_pipeline.resolve_pick(None, "4711", _product(), "100", "4.100", "ZZLMDUR", [])
    assert pv.value is None and pv.skip_reason


def test_gs1_rows_statuses(monkeypatch):
    reference = [
        {"pick_id": "4.012", "attribute_name": "Material", "mandatory": True, "gdsn_attribute_name": "propertyCode"},
        {"pick_id": "4.100", "attribute_name": "Diameter", "mandatory": False,
         "gdsn_attribute_name": "propertyMeasurement"},
        {"pick_id": "4.200", "attribute_name": "Shape", "mandatory": True, "gdsn_attribute_name": "propertyCode"},
        {"pick_id": "4.300", "attribute_name": "Finish", "mandatory": False, "gdsn_attribute_name": "propertyCode"},
    ]
    mappings = [{"pickid": "4.012", "pimfeld": "ZZGHMAT"}, {"pickid": "4.100", "pimfeld": "ZZLMDUR"}]
    results = {
        "4.012": gs1_pipeline.PickValue("propertyCode", "metaal", gs1_pipeline.SOURCE_CROSSWALK, pim_codes=["A04"]),
        "4.100": gs1_pipeline.PickValue("propertyMeasurement", None, unit_code="MMT"),
    }
    monkeypatch.setattr(gs1_pipeline, "resolve_pick", lambda conn, m, p, b, pick, f, langs: results[pick])
    rows = q._gs1_attribute_rows(None, "4711", _product(ZZGHMAT="A04"), "100", reference, mappings,
                                 {"4.012": [("metaal", "Metal")]}, [])
    by_pick = {r["pick_id"]: r for r in rows}
    assert (by_pick["4.012"]["status"], by_pick["4.012"]["value_desc"]) == (q.ST_OK, "Metal")
    assert by_pick["4.100"]["status"] == q.ST_EMPTY
    assert by_pick["4.200"]["status"] == q.ST_MISSING
    assert by_pick["4.300"]["status"] == q.ST_NO_MAPPING


def test_to_xlsx_sheets_and_status_colours():
    etim = {"overview": [{"matnr": "4711", "name": "Lamp", "system": "ETIM", "zztyp": "", "class": CLASS,
                          "class_desc": "Luminaire", "total": 1, "filled": 1, "default": 0, "empty": 0,
                          "missing": 0, "dropped": 0, "no_mapping": 0, "remark": ""}],
            "rows": [{"matnr": "4711", "name": "Lamp", "class": CLASS, "class_desc": "Luminaire",
                      "feature_id": "EF000664", "feature": "Suitable for wall mounting", "type": "logical",
                      "unit": "", "value": "true", "value_desc": "Yes", "pim_field": "ZZX", "pim_value": "",
                      "source": "PIM", "status": q.ST_OK, "note": ""}],
            "allowed": []}
    wb = load_workbook(io.BytesIO(q.to_xlsx(etim, None, ["4711"])))
    assert wb.sheetnames == ["Overview", "ETIM", "ETIM allowed values", "Legend"]
    ws = wb["ETIM"]
    assert ws["F2"].value == "Suitable for wall mounting"
    assert ws["N2"].value == q.ST_OK and ws["N2"].fill.start_color.rgb.endswith("C6EFCE")


@pytest.mark.parametrize("matnrs, systems, message", [
    ([], ["etim"], "at least one"),
    ([str(i) for i in range(q.MAX_ARTICLES + 1)], ["etim"], "At most"),
    (["1"], [], "ETIM and/or GS1"),
])
def test_build_report_input_errors(matnrs, systems, message):
    with pytest.raises(q.QualityCheckError, match=message):
        q.build_report(matnrs, systems, "0012")
