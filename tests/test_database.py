from gs1_export import database


class FakeCursor:
    def __init__(self, responses):
        self._responses = responses
        self._last_params = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, query, params):
        self._last_params = params

    def fetchone(self):
        pim_code = self._last_params[1]
        value = self._responses.get(pim_code)
        return (value,) if value is not None else None


class FakeConnection:
    def __init__(self, responses):
        self._responses = responses

    def cursor(self):
        return FakeCursor(self._responses)


def test_crosswalk_prefers_exact_match_over_wildcard():
    conn = FakeConnection({"ZZTYPEN_EAL": "buiten", "*": "binnen"})
    assert database.get_pim_value_crosswalk(conn, "4.364", "ZZTYPEN_EAL") == "buiten"


def test_crosswalk_falls_back_to_wildcard_when_no_exact_match():
    conn = FakeConnection({"ZZTYPEN_EAL": "buiten", "ZZTYPEN_SAR": "buiten", "*": "binnen"})
    assert database.get_pim_value_crosswalk(conn, "4.364", "ZZTYPEN_WAL") == "binnen"


def test_crosswalk_returns_none_without_exact_match_or_wildcard():
    conn = FakeConnection({"ZZTYPEN_EAL": "buiten"})
    assert database.get_pim_value_crosswalk(conn, "4.364", "ZZTYPEN_WAL") is None
