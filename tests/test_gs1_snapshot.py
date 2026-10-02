from lxml import etree

from gs1_export.snapshot import diff_snapshots, snapshot_trade_item


def test_gs1_snapshot_and_diff():
    xml = b"""<tradeItem><gtin>09002759437060</gtin>
      <gdsnTradeItemClassification><gpcCategoryCode>10008403</gpcCategoryCode>
        <additionalTradeItemClassification><additionalTradeItemClassificationValue>
          <additionalTradeItemClassificationProperty>
            <additionalTradeItemClassificationPropertyCode>4.015</additionalTradeItemClassificationPropertyCode>
            <propertyCode>weiss</propertyCode></additionalTradeItemClassificationProperty>
          <additionalTradeItemClassificationProperty>
            <additionalTradeItemClassificationPropertyCode>4.052</additionalTradeItemClassificationPropertyCode>
            <propertyMeasurement measurementUnitCode="WTT">10</propertyMeasurement></additionalTradeItemClassificationProperty>
        </additionalTradeItemClassificationValue></additionalTradeItemClassification></gdsnTradeItemClassification>
      <tradeItemSynchronisationDates><lastChangeDateTime>2026-10-02T10:00:00</lastChangeDateTime></tradeItemSynchronisationDates>
    </tradeItem>"""
    snap = snapshot_trade_item(etree.fromstring(xml))
    assert snap == {"gtin": "09002759437060", "brick": "10008403", "pick:4.015": "weiss", "pick:4.052": "10 WTT"}
    new = {**snap, "pick:4.015": "zwart", "pick:4.250": "IP44"}
    del new["pick:4.052"]
    changes = diff_snapshots(snap, new, {"pick:4.015": "4.015 Colour Family"})
    assert [(c["kind"], c["name"], c["old"], c["new"]) for c in changes] == [
        ("changed", "4.015 Colour Family", "weiss", "zwart"),
        ("removed", "pick:4.052", "10 WTT", None),
        ("added", "pick:4.250", None, "IP44"),
    ]
