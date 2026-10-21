import io
import smtplib
import zipfile

import pytest

import mailer
from mailer import build_export_mail, send_mail


def _attachments(msg):
    return {part.get_filename(): part.get_content() for part in msg.iter_attachments()}


def test_build_export_mail_attaches_zip_and_problem_list():
    msg = build_export_mail("user@eglo.com", "etim10_0000012345_20261021_120000.xml", b"<BMECAT/>", 2,
                            ["30658: EF004284 nicht zulässig"], 1, b"xlsx")
    assert msg["From"] == "etim-export@eglo.com"
    assert msg["To"] == "user@eglo.com"
    assert "2 Artikel" in msg["Subject"]
    body = msg.get_body().get_content()
    assert "Warnungen: 1" in body and "XSD-Fehler: 1" in body and "- 30658: EF004284" in body

    files = _attachments(msg)
    assert set(files) == {"etim10_0000012345_20261021_120000.zip",
                          "etim10_0000012345_20261021_120000_fehlerliste.xlsx"}
    with zipfile.ZipFile(io.BytesIO(files["etim10_0000012345_20261021_120000.zip"])) as zf:
        assert zf.read("etim10_0000012345_20261021_120000.xml") == b"<BMECAT/>"


def test_build_export_mail_skips_too_large_file(monkeypatch):
    monkeypatch.setattr(mailer, "MAIL_MAX_ATTACHMENT_BYTES", 10)
    msg = build_export_mail("user@eglo.com", "x.xml", b"<BMECAT/>" * 100, 1, [], 0)
    assert _attachments(msg) == {}
    assert "Meine Exporte" in msg.get_body().get_content()


def test_build_export_mail_caps_warnings_in_body():
    msg = build_export_mail("user@eglo.com", "x.xml", b"<BMECAT/>", 1, [f"w{i}" for i in range(40)], 0)
    body = msg.get_body().get_content()
    assert "- w29" in body and "- w30" not in body and "10 weitere" in body


def test_send_mail_raises_german_message(monkeypatch):
    def refuse(*args, **kwargs):
        raise ConnectionRefusedError("refused")
    monkeypatch.setattr(smtplib, "SMTP", refuse)
    with pytest.raises(Exception, match="konnte nicht gesendet werden"):
        send_mail(build_export_mail("user@eglo.com", "x.xml", b"<BMECAT/>", 1, [], 0))
