from datetime import datetime

import psycopg

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


class _RecordingCursor:
    """Captures the (query, params) of every execute() call, for asserting on
    what get_changed_articles()/upsert_export_history() actually send to the
    DB - independent of the narrow FakeCursor above, which assumes a fixed
    query shape (single param lookup) not shared by these functions."""

    def __init__(self, fetchall_result=None, fetchone_result=None, raise_on_execute=None):
        self.calls = []
        self._fetchall_result = fetchall_result or []
        self._fetchone_result = fetchone_result
        self._raise_on_execute = raise_on_execute

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, query, params=None):
        self.calls.append((query, params))
        # SET statement_timeout is a harmless utility call, never the one that
        # would actually raise UndefinedTable against a real table.
        raises_here = (
            "gs1_export_history" in query or "gs1_export_files" in query
            or "pim_egloakeneo_product_values_pim_catalog_text" in query
        )
        if self._raise_on_execute and raises_here:
            raise self._raise_on_execute

    def fetchall(self):
        return self._fetchall_result

    def fetchone(self):
        return self._fetchone_result


class _RecordingConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


def test_upsert_export_history_sends_expected_params():
    cursor = _RecordingCursor()
    conn = _RecordingConnection(cursor)
    database.upsert_export_history(conn, "43706", "10008403", "2026-08-21T10:00:00Z", "user@eglo.com")
    query, params = cursor.calls[0]
    assert "INSERT INTO public.gs1_export_history" in query
    assert params == ("43706", "10008403", "2026-08-21T10:00:00Z", "user@eglo.com")


def test_upsert_export_history_tolerates_missing_table():
    cursor = _RecordingCursor(raise_on_execute=psycopg.errors.UndefinedTable())
    conn = _RecordingConnection(cursor)
    # Must not raise - a missing table must never abort an otherwise-successful export.
    database.upsert_export_history(conn, "43706", "10008403", "2026-08-21T10:00:00Z", None)


def test_get_changed_articles_maps_rows():
    now = datetime(2026, 8, 28, 9, 0, 0)
    cursor = _RecordingCursor(fetchall_result=[
        ("43706", now, "2026-08-20T10:00:00Z", "2026-08-27T15:30:00Z"),
    ])
    conn = _RecordingConnection(cursor)
    result = database.get_changed_articles(conn)
    assert result == [{
        "matnr": "43706",
        "exported_at": now.isoformat(),
        "pim_updated_at_export": "2026-08-20T10:00:00Z",
        "pim_updated_now": "2026-08-27T15:30:00Z",
    }]


def test_get_changed_articles_returns_empty_list_when_table_missing():
    cursor = _RecordingCursor(raise_on_execute=psycopg.errors.UndefinedTable())
    conn = _RecordingConnection(cursor)
    assert database.get_changed_articles(conn) == []


def test_save_export_file_sends_expected_params():
    cursor = _RecordingCursor()
    conn = _RecordingConnection(cursor)
    database.save_export_file(conn, "gs1_export_20260828.xml", "user@eglo.com", ["43706", "89534"], b"<xml/>")
    query, params = cursor.calls[0]
    assert "INSERT INTO public.gs1_export_files" in query
    assert params == ("gs1_export_20260828.xml", "user@eglo.com", ["43706", "89534"], 2, b"<xml/>")


def test_save_export_file_tolerates_missing_table():
    cursor = _RecordingCursor(raise_on_execute=psycopg.errors.UndefinedTable())
    conn = _RecordingConnection(cursor)
    # Must not raise - archiving must never break an otherwise-successful export/download.
    database.save_export_file(conn, "gs1_export_20260828.xml", "user@eglo.com", ["43706"], b"<xml/>")


def test_list_export_files_maps_rows():
    now = datetime(2026, 8, 28, 9, 0, 0)
    cursor = _RecordingCursor(fetchall_result=[
        (7, "gs1_export_20260828.xml", now, 2, ["43706", "89534"]),
    ])
    conn = _RecordingConnection(cursor)
    result = database.list_export_files(conn, "user@eglo.com")
    assert result == [{
        "id": 7,
        "filename": "gs1_export_20260828.xml",
        "exported_at": now.isoformat(),
        "article_count": 2,
        "matnrs": ["43706", "89534"],
    }]
    assert cursor.calls[0][1] == ("user@eglo.com",)


def test_list_export_files_returns_empty_list_when_table_missing():
    cursor = _RecordingCursor(raise_on_execute=psycopg.errors.UndefinedTable())
    conn = _RecordingConnection(cursor)
    assert database.list_export_files(conn, "user@eglo.com") == []


def test_get_export_file_returns_owned_file():
    cursor = _RecordingCursor(fetchone_result=("gs1_export_20260828.xml", b"<xml/>"))
    conn = _RecordingConnection(cursor)
    result = database.get_export_file(conn, 7, "user@eglo.com")
    assert result == {"filename": "gs1_export_20260828.xml", "xml_content": b"<xml/>"}
    assert cursor.calls[0][1] == (7, "user@eglo.com")


def test_get_export_file_returns_none_when_not_found_or_not_owned():
    cursor = _RecordingCursor(fetchone_result=None)
    conn = _RecordingConnection(cursor)
    assert database.get_export_file(conn, 7, "other@eglo.com") is None


def test_get_export_file_returns_none_when_table_missing():
    cursor = _RecordingCursor(raise_on_execute=psycopg.errors.UndefinedTable())
    conn = _RecordingConnection(cursor)
    assert database.get_export_file(conn, 7, "user@eglo.com") is None


def test_get_pim_catalog_text_value_unescapes_slashes():
    cursor = _RecordingCursor(fetchone_result=("WL\\/1 E27 white\\/wood 'TOWNSHEND'",))
    conn = _RecordingConnection(cursor)
    assert database.get_pim_catalog_text_value(conn, "43706", "MAKTX", "en_US") == "WL/1 E27 white/wood 'TOWNSHEND'"


def test_get_pim_catalog_text_value_treats_empty_array_marker_as_none():
    cursor = _RecordingCursor(fetchone_result=("[]",))
    conn = _RecordingConnection(cursor)
    assert database.get_pim_catalog_text_value(conn, "43706", "MAKTX", "en_GB") is None


def test_get_pim_catalog_text_value_returns_none_when_no_row():
    cursor = _RecordingCursor(fetchone_result=None)
    conn = _RecordingConnection(cursor)
    assert database.get_pim_catalog_text_value(conn, "43706", "MAKTX", "en_GB") is None


def test_get_pim_catalog_text_value_tolerates_missing_table():
    cursor = _RecordingCursor(raise_on_execute=psycopg.errors.UndefinedTable())
    conn = _RecordingConnection(cursor)
    assert database.get_pim_catalog_text_value(conn, "43706", "MAKTX", "en_GB") is None
