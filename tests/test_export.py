"""Tests for vCard export."""

from schnabel.export import contact_to_vcard, _fold_line, _format_phone
from schnabel.model import Contact, ContactField


def test_fold_line_short():
    line = "FN:Hans Mueller"
    assert _fold_line(line) == line  # no folding needed


def test_fold_line_long():
    line = "NOTE:" + "x" * 100
    folded = _fold_line(line)
    assert "\r\n " in folded


def test_format_swiss_phone():
    formatted = _format_phone("+41791234567")
    assert formatted.startswith("079")


def test_format_german_phone():
    formatted = _format_phone("+491701234567")
    assert formatted.startswith("+49")


def test_contact_to_vcard_basic():
    c = Contact(fn="Hans Mueller", family_name="Mueller", given_name="Hans")
    c.fields.append(ContactField("email", "hans@test.com", {"TYPE": "HOME"}))
    c.fields.append(ContactField("tel", "+41791234567", {"TYPE": "CELL"}))

    vcard = contact_to_vcard(c)
    assert "BEGIN:VCARD" in vcard
    assert "VERSION:3.0" in vcard
    assert "FN:Hans Mueller" in vcard
    assert "N:Mueller;Hans;;;" in vcard
    assert "EMAIL" in vcard
    assert "TEL" in vcard
    assert "END:VCARD" in vcard
    assert "\r\n" in vcard  # CRLF line endings
