from etim10_export.load_fallback_values import beam_value, cri_value, values_from_eprel, values_from_webcenter


def test_cri_value_ranges():
    assert cri_value(83) == "EV003581"
    assert cri_value("90") == "EV003580"
    assert cri_value(None) is None
    assert cri_value(10) is None


def test_beam_value_ranges():
    assert beam_value(360) == "EV011539"   # omnidirectional -> open beam/diffuse
    assert beam_value(120) == "EV011538"   # > 80
    assert beam_value(38) == "EV011536"    # 21-40
    assert beam_value("4") == "EV011540"   # < 5
    assert beam_value("") is None


def test_values_from_eprel():
    record = {"colourRenderingIndex": 83, "correlatedColourTempMin": 3000, "correlatedColourTempMax": 3000,
              "beamAngle": None, "beamAngleCorrespondence": "SPHERE_360", "dimmable": "NO",
              "displacementFactor": 0, "flickerMetric": 0.1, "stroboscopicEffectMetric": None,
              "colourConsistency": 6, "luminousFlux": 1900, "powerOnMode": 15}

    assert values_from_eprel(record) == {
        "EF000442": ("EV003581", None),
        "EF009346": ("3000", "3000"),
        "EF008157": ("EV011539", None),
        "EF000137": ("false", None),
        "EF015827": ("0.1", None),
        "EF011946": ("EV014019", None),
    }  # power factor 0 = not declared; flux/power never loaded


def test_values_from_webcenter():
    row = {"cri_ra": "80", "farbtemperatur_k": "2700 | 4000 | 6500", "abstrahlwinkel_grad": "360",
           "anzahl_leuchtmittel": "3"}

    assert values_from_webcenter(row) == {
        "EF000442": ("EV003581", None),
        "EF009346": ("2700", "6500"),
        "EF008157": ("EV011539", None),
        "EF006436": ("3", None),
    }
