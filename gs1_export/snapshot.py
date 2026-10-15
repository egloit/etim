"""
Per-article snapshot of what a GS1 export contained, to show later which
values changed since the export (gs1_export_history.value_snapshot).

A snapshot is a flat {key: value} dict of one tradeItem:
  "brick"      -> gpcCategoryCode
  "pick:4.015" -> value of a GPC classification property ("weiss",
                  "10 WTT", "en: Brown | nl: Bruin")
  "<path>"     -> every other leaf (extension modules etc.), e.g.
                  "tradeItemMeasurements/depth [MMT]"; repeated leaves are
                  joined with " | ".
Volatile values (lastChangeDateTime, identification ids) are left out.
"""
from typing import Optional

from lxml import etree

# Filled with "now" on every build (gs1_exporter._now_iso) or random ids - never a real change.
_VOLATILE = {"lastChangeDateTime", "effectiveDateTime", "startAvailabilityDateTime", "creationDateTime",
             "entityIdentification"}
_PROPERTY = "additionalTradeItemClassificationProperty"
_PROPERTY_CODE = "additionalTradeItemClassificationPropertyCode"


def _local(el: etree._Element) -> str:
    return etree.QName(el).localname


def _value_text(el: etree._Element) -> str:
    text = (el.text or "").strip()
    qualifier = el.get("languageCode") or el.get("measurementUnitCode")
    if el.get("languageCode"):
        return f"{qualifier}: {text}"
    return f"{text} {qualifier}" if qualifier else text


def snapshot_trade_item(trade_item: etree._Element) -> dict[str, str]:
    values: dict[str, list[str]] = {}
    for el in trade_item.iter():
        if not isinstance(el.tag, str) or len(el) or not (el.text or "").strip():
            continue
        name = _local(el)
        if name in _VOLATILE or name == _PROPERTY_CODE:
            continue
        ancestors = [_local(a) for a in el.iterancestors() if isinstance(a.tag, str)]
        if _PROPERTY in ancestors:
            prop = next(a for a in el.iterancestors() if _local(a) == _PROPERTY)
            key = f"pick:{prop.findtext(_PROPERTY_CODE) or prop.findtext('{*}' + _PROPERTY_CODE)}"
        elif name == "gpcCategoryCode":
            key = "brick"
        else:
            path = list(reversed(ancestors))
            path = path[path.index("tradeItem") + 1:] if "tradeItem" in path else path
            # skip GS1 wrapper levels so keys stay readable
            path = [p for p in path if p not in ("extension", "tradeItemInformation")]
            key = "/".join(path[-2:] + [name])
        values.setdefault(key, []).append(_value_text(el))
    return {k: " | ".join(v) for k, v in values.items()}


def diff_snapshots(old: dict[str, str], new: dict[str, str], names: Optional[dict[str, str]] = None) -> list[dict]:
    """Changes between two snapshots, sorted by key: [{kind, key, name, old, new}]."""
    names = names or {}
    changes = []
    for key in sorted(set(old) | set(new)):
        before, after = old.get(key), new.get(key)
        if before == after:
            continue
        kind = "added" if before is None else "removed" if after is None else "changed"
        label = names.get(key) or ("GPC-Brick" if key == "brick" else key)
        changes.append({"kind": kind, "key": key, "name": label, "old": before, "new": after})
    return changes
