def test_file_scope_admin_sees_all(monkeypatch):
    import main
    monkeypatch.setattr(main, "ADMIN_EMAILS", {"admin@eglo.com"})
    assert main._file_scope("Admin@Eglo.com") is None
    assert main._file_scope("user@eglo.com") == "user@eglo.com"
    assert main._is_admin("") is False
