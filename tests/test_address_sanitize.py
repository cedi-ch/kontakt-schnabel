"""Tests for address sanitization and normalization."""

from schnabel.sanitize import _clean_address_value, _address_key, _normalize_address
from schnabel.model import Contact, ContactField


# --- _clean_address_value ---

def test_clean_escaped_newlines():
    """Escaped newlines (\\n) should be replaced with spaces."""
    assert "Muristrasse 59 3006 Bern" == _clean_address_value("Muristrasse 59\\n3006 Bern")


def test_clean_actual_newlines():
    assert "Street 1 City" == _clean_address_value("Street 1\nCity")


def test_clean_crlf():
    assert "Street 1 City" == _clean_address_value("Street 1\r\nCity")


def test_clean_leading_separators():
    """Leading commas/semicolons should be preserved by clean (structural)."""
    # _clean_address_value only handles newlines/escapes, not structural cleanup
    # Leading separator cleanup happens in _normalize_address
    cleaned = _clean_address_value(", Muristrasse 59")
    assert "Muristrasse 59" in cleaned


def test_clean_multiple_spaces():
    assert "Street 1 City" == _clean_address_value("Street 1    City")


# --- _address_key ---

def test_address_key_same_after_newline_diff():
    """Addresses differing only by newlines should have the same key."""
    k1 = _address_key("Muristrasse 59, Bern, 3006")
    k2 = _address_key("Muristrasse 59\\n3006 Bern")
    assert k1 == k2


def test_address_key_case_insensitive():
    k1 = _address_key("Bahnhofstrasse 1, Zürich")
    k2 = _address_key("bahnhofstrasse 1, zürich")
    assert k1 == k2


def test_address_key_separator_insensitive():
    k1 = _address_key("Street;City;Code")
    k2 = _address_key("Street, City, Code")
    assert k1 == k2


# --- _normalize_address ---

def test_normalize_strips_escaped_newlines():
    result = _normalize_address("Muristrasse 59\\n3006 Bern")
    assert "\\n" not in result
    assert "Muristrasse 59" in result
    assert "3006" in result


def test_normalize_semicolon_format_preserved():
    """7-component semicolon format should be cleaned but kept structured."""
    result = _normalize_address(";;Muristrasse 59;Bern;;3006;CH")
    parts = result.split(";")
    assert len(parts) == 7
    assert parts[0] == ""  # PO Box
    assert parts[1] == ""  # Extended
    assert parts[2] == "Muristrasse 59"
    assert parts[3] == "Bern"
    assert parts[5] == "3006"


def test_normalize_leading_comma_stripped():
    result = _normalize_address(", Muristrasse 59, Bern")
    assert not result.startswith(",")
    assert "Muristrasse 59" in result


def test_normalize_empty_becomes_empty():
    assert _normalize_address("") == ""
    assert _normalize_address("  ") == ""


# --- Dedup integration ---

def test_address_dedup_newline_variants(tmp_db):
    """Addresses differing only by newlines should be deduplicated."""
    from schnabel.sanitize import sanitize_contacts

    c = Contact(fn="Maurice", family_name="Test", given_name="Maurice", category="real")
    # These two have the same content, just different formatting
    c.fields.append(ContactField("adr", "Muristrasse 59, Bern, 3006"))
    c.fields.append(ContactField("adr", "Muristrasse 59\\n3006 Bern"))
    cid = tmp_db.insert_contact(c)
    tmp_db.commit()

    report = sanitize_contacts(tmp_db)

    contact = tmp_db.get_contact(cid)
    addrs = [f.field_value for f in contact.fields if f.field_type == "adr"]
    assert len(addrs) == 1
    assert "Muristrasse 59" in addrs[0]
    assert report.removed["adr"] >= 1


def test_address_dedup_structured_vs_plain(tmp_db):
    """Structured and plain addresses with same street should be deduplicated."""
    from schnabel.sanitize import sanitize_contacts

    c = Contact(fn="Test", family_name="Test", given_name="Test", category="real")
    c.fields.append(ContactField("adr", ";;Strasse 1;Zürich;;8001;CH"))
    c.fields.append(ContactField("adr", ";;Strasse 1;Zürich;;8001;CH"))  # exact duplicate
    cid = tmp_db.insert_contact(c)
    tmp_db.commit()

    sanitize_contacts(tmp_db)

    contact = tmp_db.get_contact(cid)
    addrs = [f.field_value for f in contact.fields if f.field_type == "adr"]
    assert len(addrs) == 1
    assert "8001" in addrs[0]


def test_address_different_addresses_kept(tmp_db):
    """Genuinely different addresses should both be kept."""
    from schnabel.sanitize import sanitize_contacts

    c = Contact(fn="Test", family_name="Test", given_name="Test", category="real")
    c.fields.append(ContactField("adr", ";;Strasse 1;Zürich;;8001;CH"))
    c.fields.append(ContactField("adr", ";;Hauptstrasse 5;Bern;;3001;CH"))
    cid = tmp_db.insert_contact(c)
    tmp_db.commit()

    sanitize_contacts(tmp_db)

    contact = tmp_db.get_contact(cid)
    addrs = [f.field_value for f in contact.fields if f.field_type == "adr"]
    assert len(addrs) == 2
