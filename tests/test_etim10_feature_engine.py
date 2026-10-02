import pytest

from etim10_export.feature_engine import RuleSet, compute_features, compute_raw

CLASS = "EC002892"


def _product(**values):
    def entry(data, attribute_type):
        return [{"data": data, "scope": None, "locale": None, "attribute_type": attribute_type}]

    out = {}
    for code, data in values.items():
        if isinstance(data, list):
            out[code] = entry(data, "pim_catalog_multiselect")
        elif isinstance(data, bool):
            out[code] = entry(data, "pim_catalog_boolean")
        elif isinstance(data, (int, float)):
            out[code] = entry(data, "pim_catalog_number")
        else:
            out[code] = entry(data, "pim_catalog_simpleselect")
    return {"values": out}


def _rule(feature_id, source_type, slot="value", sort_order=10, **kw):
    rule = dict(class_id=CLASS, feature_id=feature_id, slot=slot, source_type=source_type, pim_field=None,
                crosswalk_set=None, fix_value=None, transform=None, required_pim_field=None, sort_order=sort_order)
    rule.update(kw)
    return rule


@pytest.fixture
def rules():
    return RuleSet(
        class_by_zztyp={"ZZTYPEN_DEL": CLASS},
        rules_by_class={CLASS: [
            _rule("EF000187", "crosswalk", pim_field="ZZNETZS", crosswalk_set="netz"),
            _rule("EF012154", "crosswalk", pim_field="PIM_dimmable_with", crosswalk_set="dali"),
            _rule("EF000004", "fix", fix_value="EV000583"),
            _rule("EF000015", "copy", pim_field="ZZLMDUR"),
            _rule("EF000015", "copy", pim_field="ZZLMBRE", sort_order=20),
            _rule("EF000280", "copy", pim_field="PIM_FAS_05_1"),
            _rule("EF000280", "copy", slot="value2", pim_field="PIM_MAX_POWER"),
            _rule("EF009347", "copy", pim_field="PIM_MAX_POWER", transform="substring_after:="),
            _rule("EF005905", "fix", fix_value="true", required_pim_field="PIM_FAS_07_1"),
            _rule("EF000136", "crosswalk", pim_field="ZZGHFAR", crosswalk_set="farbe"),
            _rule("EF008157", "fix", fix_value="EV000154"),
        ]},
        crosswalk={
            "netz": {"ZZNETZS_01": "EV000460"},
            "dali": {"PIM_dimmable_with_DALI": "true", "*": "false"},
            "farbe": {"ZZGHFAR_X": "EV999999", "*": "EV000154"},
        },
        model_features={
            (CLASS, "EF000187"): {"type": "A", "unit": None},
            (CLASS, "EF012154"): {"type": "L", "unit": None},
            (CLASS, "EF000004"): {"type": "A", "unit": None},
            (CLASS, "EF000015"): {"type": "N", "unit": "EU570448"},
            (CLASS, "EF000280"): {"type": "R", "unit": "EU570054"},
            (CLASS, "EF005905"): {"type": "L", "unit": None},
            (CLASS, "EF000136"): {"type": "A", "unit": None},
            (CLASS, "EF008157"): {"type": "A", "unit": None},
        },
        allowed_values={(CLASS, "EF000187"): {"EV000460"}, (CLASS, "EF000004"): {"EV000583"},
                        (CLASS, "EF000136"): {"EV000202"}},
    )


def test_crosswalk_exact_match(rules):
    assert compute_raw(_product(ZZNETZS="ZZNETZS_01"), CLASS, rules)["EF000187"]["value"] == "EV000460"


def test_crosswalk_without_match_or_default_is_empty(rules):
    assert compute_raw(_product(ZZNETZS="ZZNETZS_99"), CLASS, rules)["EF000187"]["value"] == ""


def test_crosswalk_matches_any_multiselect_code(rules):
    product = _product(PIM_dimmable_with=["PIM_dimmable_with_Wandschalter", "PIM_dimmable_with_DALI"])
    assert compute_raw(product, CLASS, rules)["EF012154"]["value"] == "true"


def test_crosswalk_default_applies_to_empty_field(rules):
    assert compute_raw(_product(), CLASS, rules)["EF012154"]["value"] == "false"


def test_first_non_empty_rule_wins(rules):
    assert compute_raw(_product(ZZLMDUR=90, ZZLMBRE=0), CLASS, rules)["EF000015"]["value"] == "90"
    assert compute_raw(_product(ZZLMBRE=12.50), CLASS, rules)["EF000015"]["value"] == "12.5"


def test_required_pim_field(rules):
    assert compute_raw(_product(), CLASS, rules)["EF005905"]["value"] == ""
    assert compute_raw(_product(PIM_FAS_07_1="X"), CLASS, rules)["EF005905"]["value"] == "true"


def test_substring_after_transform(rules):
    assert compute_raw(_product(PIM_MAX_POWER="LED = 10W"), CLASS, rules)["EF009347"]["value"] == " 10W"


def test_compute_features_validates_against_model(rules):
    product = _product(ZZNETZS="ZZNETZS_01", ZZLMDUR=90, PIM_FAS_05_1="5,4", PIM_MAX_POWER="14W",
                       ZZGHFAR="ZZGHFAR_X")
    features, warnings = compute_features(product, CLASS, rules, "4711")
    by_id = {f["feature_id"]: f for f in features}

    assert by_id["EF000187"] == {"feature_id": "EF000187", "values": ["EV000460"], "unit_id": None}
    assert by_id["EF000015"] == {"feature_id": "EF000015", "values": ["90"], "unit_id": "EU570448"}
    # range: min/max from value/value2, numbers read like GS1 does ('5,4' -> 5.4, '14W' -> 14)
    assert by_id["EF000280"]["values"] == ["5.4", "14"]
    assert by_id["EF012154"]["values"] == ["false"]
    # EV999999 (from the article's PIM value) isn't allowed for EF000136 -> dropped with a warning
    assert "EF000136" not in by_id
    assert any("EF000136" in w for w in warnings)
    # an invalid fix value comes from the rule, not the article -> dropped silently
    assert "EF008157" not in by_id
    assert not any("EF008157" in w for w in warnings)
    # EF009347 has no model entry for the class -> dropped
    assert "EF009347" not in by_id


def test_invalid_crosswalk_default_is_dropped_silently(rules):
    features, warnings = compute_features(_product(ZZGHFAR="ZZGHFAR_UNKNOWN"), CLASS, rules, "4711")
    assert "EF000136" not in {f["feature_id"] for f in features}
    assert not any("EF000136" in w for w in warnings)


def test_diff_features_reports_added_changed_removed(rules):
    from etim10_export.pipeline import diff_features

    rules.feature_names.update({"EF000187": "Voltage type", "EF012154": "Dimming DALI", "EF000004": "Protection class"})
    rules.value_names.update({"EV000460": "AC", "EV000583": "II"})
    old = {"class_id": CLASS, "features": {"EF000187": ["EV000460"], "EF012154": ["false"], "EF000004": ["EV000583"]}}
    new = {"class_id": CLASS, "features": {"EF000187": ["EV000460"], "EF012154": ["true"], "EF000015": ["90"]}}
    changes = diff_features(old, new, rules)
    assert [(c["kind"], c["feature_id"], c["old"], c["new"]) for c in changes] == [
        ("removed", "EF000004", "II", None),
        ("added", "EF000015", None, "90"),
        ("changed", "EF012154", "false", "true"),
    ]
    assert diff_features(old, old, rules) == []
