"""
Computes the ETIM 10 features of one article from its PIM JSON, driven by the
etim10_feature_rules / etim10_value_crosswalk tables (see
sql/create_etim10_tables.sql for the rule semantics), and checks the result
against the ETIM 10 model (etim10_model_* tables).
"""
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

import psycopg

from gs1_export import pim_reader

EMPTY_VALUES = {"", "-"}


@dataclass
class RuleSet:
    """All rule/model data needed for an export, loaded once per batch."""
    class_by_zztyp: dict[str, str]
    rules_by_class: dict[str, list[dict]]
    crosswalk: dict[str, dict[str, str]]
    model_features: dict[tuple[str, str], dict]          # (class, feature) -> {type, unit}
    allowed_values: dict[tuple[str, str], set[str]]      # (class, feature) -> {EV...}
    feature_names: dict[str, str] = field(default_factory=dict)
    value_names: dict[str, str] = field(default_factory=dict)        # EV.. -> description

    def pim_fields(self) -> set[str]:
        """All PIM attributes the active rules read (plus ZZTYPEN for the class)."""
        fields = {"ZZTYPEN"}
        for rules in self.rules_by_class.values():
            for rule in rules:
                fields.update(f for f in (rule["pim_field"], rule["required_pim_field"]) if f)
        return fields

    @classmethod
    def load(cls, conn: psycopg.Connection) -> "RuleSet":
        with conn.cursor() as cur:
            cur.execute("SELECT zztyp, class_id FROM public.etim10_class_mapping WHERE active")
            class_by_zztyp = dict(cur.fetchall())

            cur.execute(
                """
                SELECT class_id, feature_id, slot, source_type, pim_field, crosswalk_set, fix_value,
                       transform, required_pim_field, sort_order
                FROM public.etim10_feature_rules
                WHERE active
                ORDER BY class_id, feature_id, slot, sort_order, id
                """
            )
            columns = [d.name for d in cur.description]
            rules_by_class: dict[str, list[dict]] = defaultdict(list)
            for row in cur.fetchall():
                rule = dict(zip(columns, row))
                rules_by_class[rule["class_id"]].append(rule)

            cur.execute("SELECT crosswalk_set, pim_code, etim_value FROM public.etim10_value_crosswalk WHERE active")
            crosswalk: dict[str, dict[str, str]] = defaultdict(dict)
            for name, code, value in cur.fetchall():
                crosswalk[name][code] = value

            # Only the classes actually used by the rules - keeps the model small.
            class_ids = sorted(set(class_by_zztyp.values()))
            cur.execute(
                """
                SELECT class_id, feature_id, feature_type, unit_id
                FROM public.etim10_model_class_feature WHERE class_id = ANY(%s)
                """,
                (class_ids,),
            )
            model_features = {(c, f): {"type": t, "unit": u} for c, f, t, u in cur.fetchall()}

            cur.execute(
                "SELECT class_id, feature_id, value_id FROM public.etim10_model_allowed_value WHERE class_id = ANY(%s)",
                (class_ids,),
            )
            allowed_values: dict[tuple[str, str], set[str]] = defaultdict(set)
            for c, f, v in cur.fetchall():
                allowed_values[(c, f)].add(v)

            cur.execute(
                """
                SELECT f.feature_id, f.description FROM public.etim10_model_feature f
                WHERE f.feature_id IN (SELECT feature_id FROM public.etim10_model_class_feature
                                       WHERE class_id = ANY(%s))
                """,
                (class_ids,),
            )
            feature_names = dict(cur.fetchall())

            cur.execute("SELECT value_id, description FROM public.etim10_model_value")
            value_names = dict(cur.fetchall())

        return cls(class_by_zztyp, dict(rules_by_class), dict(crosswalk), model_features,
                   dict(allowed_values), feature_names, value_names)


def _format_number(value) -> str:
    """12.0 / '12.0000' -> '12', '0.50' -> '0.5'; non-numbers pass through."""
    text = str(value).strip()
    try:
        number = float(text.replace(",", "."))
    except ValueError:
        return text
    formatted = f"{number:.10f}".rstrip("0").rstrip(".")
    return "0" if formatted in ("-0", "") else formatted


def _extract_number(text: str) -> Optional[str]:
    """First number in a PIM text, tolerant of decimal commas and glued-on
    units ('5,4W' -> '5.4'), same as pim_reader.get_pim_number_as_int()."""
    match = pim_reader._NUMBER_RE.search(text or "")
    return _format_number(match.group().replace(",", ".")) if match else None


def _raw_values(product: dict, pim_field: str) -> list[str]:
    """Raw PIM data as strings: select codes, booleans ('true'/'false'),
    numbers (normalised), metric amounts, plain text."""
    entries = pim_reader._find_entries(product, pim_field)
    if not entries:
        return []
    data = entries[0].get("data")
    if isinstance(data, bool):
        return ["true" if data else "false"]
    if isinstance(data, (int, float)):
        return [_format_number(data)]
    if isinstance(data, dict):  # pim_catalog_metric
        amount = data.get("amount")
        return [_format_number(amount)] if amount not in (None, "") else []
    if isinstance(data, list):
        return [str(v) for v in data if v not in (None, "")]
    if isinstance(data, str):
        if not data.strip():
            return []
        attribute_type = entries[0].get("attribute_type", "")
        return [_format_number(data) if attribute_type == "pim_catalog_number" else data.strip()]
    return []


def _apply_transform(value: str, transform: Optional[str]) -> str:
    if not transform:
        return value
    name, _, arg = transform.partition(":")
    if name == "substring_after":
        return value.split(arg, 1)[1] if arg in value else ""
    return value


def _evaluate(rule: dict, product: dict, crosswalk: dict[str, dict[str, str]]) -> tuple[str, bool]:
    """(value, from_config): from_config is True when the value doesn't come from
    the article's PIM data but from the rule itself (fix value or crosswalk
    default '*')."""
    if rule["required_pim_field"] and not _raw_values(product, rule["required_pim_field"]):
        return "", False
    source = rule["source_type"]
    if source == "fix":
        return rule["fix_value"], True
    values = _raw_values(product, rule["pim_field"])
    if source == "copy":
        return (_apply_transform(values[0], rule["transform"]) if values else ""), False
    table = crosswalk.get(rule["crosswalk_set"], {})
    for code in values or [""]:
        if code in table:
            return table[code], False
    return table.get("*", ""), True


def compute_raw(product: dict, class_id: str, rules: RuleSet) -> dict[str, dict[str, str]]:
    """Rule output before the model check: {feature_id: {'value': .., 'value2': ..,
    'value_from_config': bool}}. Several rules for the same feature/slot: first
    non-empty one (by sort_order) wins."""
    result: dict[str, dict] = defaultdict(dict)
    for rule in rules.rules_by_class.get(class_id, []):
        slot = result[rule["feature_id"]]
        if slot.get(rule["slot"], "") not in EMPTY_VALUES:
            continue
        slot[rule["slot"]], slot[f"{rule['slot']}_from_config"] = _evaluate(rule, product, rules.crosswalk)
    return dict(result)


def compute_features(product: dict, class_id: str, rules: RuleSet, matnr: str = "") -> tuple[list[dict], list[str]]:
    """Validated features for the BMEcat file, sorted by feature id:
    [{'feature_id', 'values': [v] or [min, max], 'unit_id'}], plus warnings for
    PIM values dropped because ETIM 10 doesn't allow them. Invalid values that
    come from the rule configuration (fix value / crosswalk default, e.g.
    'sonstige' where the class doesn't allow it) are dropped silently - they say
    nothing about the article and would repeat for every article of the class."""
    features, warnings = [], []
    for feature_id, slots in sorted(compute_raw(product, class_id, rules).items()):
        value = (slots.get("value") or "").strip()
        value2 = (slots.get("value2") or "").strip()
        if value in EMPTY_VALUES:
            continue
        from_config = slots.get("value_from_config", False)
        model = rules.model_features.get((class_id, feature_id))
        prefix = f"{matnr}: {feature_id}"
        if model is None:
            if not from_config:
                warnings.append(f"{prefix} gehört in ETIM 10 nicht zur Klasse {class_id} – ausgelassen.")
            continue
        ftype = model["type"]
        if ftype == "A":
            if value not in rules.allowed_values.get((class_id, feature_id), set()):
                if not from_config:
                    warnings.append(f"{prefix} ({rules.feature_names.get(feature_id, '')}): Wert '{value}' "
                                    f"ist für {class_id} nicht zulässig – ausgelassen.")
                continue
            values = [value]
        elif ftype == "L":
            if value.lower() not in ("true", "false"):
                warnings.append(f"{prefix} ({rules.feature_names.get(feature_id, '')}): '{value}' ist kein "
                                f"logischer Wert – ausgelassen.")
                continue
            values = [value.lower()]
        else:  # N numeric, R range
            numbers = [_extract_number(v) for v in (value, value2) if v not in EMPTY_VALUES]
            if numbers[0] is None:
                warnings.append(f"{prefix} ({rules.feature_names.get(feature_id, '')}): '{value}' ist keine "
                                f"Zahl – ausgelassen.")
                continue
            numbers = [n for n in numbers if n is not None]
            if ftype == "R" and len(numbers) == 1:
                numbers = numbers * 2
            values = numbers[:2] if ftype == "R" else numbers[:1]
        features.append({"feature_id": feature_id, "values": values,
                         "unit_id": model["unit"] if ftype in ("N", "R") else None})
    return features, warnings
