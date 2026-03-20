"""Tests for phone TYPE auto-detection and Swiss formatting."""

from schnabel.sanitize import auto_detect_phone_type
from schnabel.export import _format_phone


# --- 3a: TYPE auto-detection ---

def test_swiss_mobile_079():
    assert auto_detect_phone_type("+41791234567") == "CELL"


def test_swiss_mobile_078():
    assert auto_detect_phone_type("+41781234567") == "CELL"


def test_swiss_mobile_076():
    assert auto_detect_phone_type("+41761234567") == "CELL"


def test_swiss_mobile_national_format():
    assert auto_detect_phone_type("079 123 45 67") == "CELL"


def test_swiss_landline_044():
    assert auto_detect_phone_type("+41441234567") == "HOME"


def test_swiss_landline_031():
    assert auto_detect_phone_type("+41311234567") == "HOME"


def test_swiss_landline_national_format():
    assert auto_detect_phone_type("044 123 45 67") == "HOME"


def test_foreign_number_returns_none():
    """Non-Swiss numbers should return None (leave TYPE unchanged)."""
    assert auto_detect_phone_type("+491701234567") is None


def test_invalid_number_returns_none():
    assert auto_detect_phone_type("not a number") is None


def test_already_typed_not_overwritten(tmp_db):
    """Sanitize should NOT overwrite an existing TYPE."""
    from schnabel.model import Contact, ContactField
    from schnabel.sanitize import sanitize_contacts

    c = Contact(fn="Test", family_name="Test", given_name="Test", category="real")
    c.fields.append(ContactField("tel", "+41791234567", {"TYPE": "WORK"}))
    cid = tmp_db.insert_contact(c)
    tmp_db.commit()

    sanitize_contacts(tmp_db)

    contact = tmp_db.get_contact(cid)
    tel_field = [f for f in contact.fields if f.field_type == "tel"][0]
    assert tel_field.field_params["TYPE"] == "WORK"  # not changed to CELL


def test_type_auto_detect_during_sanitize(tmp_db):
    """Sanitize should add TYPE=CELL for untyped Swiss mobile numbers."""
    from schnabel.model import Contact, ContactField
    from schnabel.sanitize import sanitize_contacts

    c = Contact(fn="Test", family_name="Test", given_name="Test", category="real")
    c.fields.append(ContactField("tel", "+41791234567"))  # no TYPE
    cid = tmp_db.insert_contact(c)
    tmp_db.commit()

    report = sanitize_contacts(tmp_db)

    contact = tmp_db.get_contact(cid)
    tel_field = [f for f in contact.fields if f.field_type == "tel"][0]
    assert tel_field.field_params.get("TYPE") == "CELL"
    assert report.reformatted["type"] == 1


# --- 3b: Swiss national format ---

def test_format_swiss_national():
    """Swiss numbers should be formatted in national format."""
    formatted = _format_phone("+41791234567")
    assert formatted.startswith("079")
    assert " " in formatted  # has spaces


def test_format_international():
    """Non-Swiss numbers should be in international format."""
    formatted = _format_phone("+491701234567")
    assert formatted.startswith("+49")


def test_format_invalid_passthrough():
    """Invalid numbers should pass through unchanged."""
    assert _format_phone("not a number") == "not a number"
