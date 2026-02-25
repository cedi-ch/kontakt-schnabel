"""Tests for vCard CATEGORIES support: parse, export, sanitize, roundtrip."""

from schnabel.export import contact_to_vcard
from schnabel.model import Contact, ContactField
from schnabel.reader import parse_vcard


def test_parse_single_categories_line():
    vcard_text = (
        "BEGIN:VCARD\nVERSION:3.0\nFN:Test\nN:Test;;;;\n"
        "CATEGORIES:Armee,Pfadi,WG\nEND:VCARD"
    )
    contact = parse_vcard(vcard_text)
    assert contact is not None
    assert sorted(contact.categories) == ["Armee", "Pfadi", "WG"]


def test_parse_no_categories():
    vcard_text = (
        "BEGIN:VCARD\nVERSION:3.0\nFN:Test\nN:Test;;;;\n"
        "EMAIL:test@test.com\nEND:VCARD"
    )
    contact = parse_vcard(vcard_text)
    assert contact is not None
    assert contact.categories == []


def test_parse_single_category():
    vcard_text = (
        "BEGIN:VCARD\nVERSION:3.0\nFN:Test\nN:Test;;;;\n"
        "CATEGORIES:Armee\nEND:VCARD"
    )
    contact = parse_vcard(vcard_text)
    assert contact is not None
    assert contact.categories == ["Armee"]


def test_export_categories():
    c = Contact(fn="Test", family_name="Test")
    c.fields.append(ContactField("categories", "Armee"))
    c.fields.append(ContactField("categories", "Pfadi"))

    vcard = contact_to_vcard(c)
    assert "CATEGORIES:Armee,Pfadi" in vcard


def test_export_categories_dedup():
    """Duplicate categories (case-insensitive) should be collapsed on export."""
    c = Contact(fn="Test", family_name="Test")
    c.fields.append(ContactField("categories", "Armee"))
    c.fields.append(ContactField("categories", "armee"))
    c.fields.append(ContactField("categories", "Pfadi"))

    vcard = contact_to_vcard(c)
    # Should have only one Armee (the first one wins)
    assert "CATEGORIES:Armee,Pfadi" in vcard
    assert vcard.count("CATEGORIES:") == 1


def test_export_no_categories():
    """Contacts without categories should not have a CATEGORIES line."""
    c = Contact(fn="Test", family_name="Test")
    c.fields.append(ContactField("email", "test@test.com"))

    vcard = contact_to_vcard(c)
    assert "CATEGORIES" not in vcard


def test_roundtrip_categories():
    """Parse -> export -> parse should preserve categories."""
    c = Contact(fn="Test", family_name="Test")
    c.fields.append(ContactField("categories", "Armee"))
    c.fields.append(ContactField("categories", "Pfadi"))

    vcard_str = contact_to_vcard(c)
    reparsed = parse_vcard(vcard_str)
    assert reparsed is not None
    assert sorted(reparsed.categories) == ["Armee", "Pfadi"]


def test_fallback_parse_categories():
    """Fallback parser should also extract CATEGORIES."""
    # Use a vCard that will trigger fallback by being slightly malformed
    # but still have recognizable fields
    vcard_text = (
        "BEGIN:VCARD\nVERSION:3.0\nFN:Test\nN:Test;;;;\n"
        "CATEGORIES:WG,Pfadi\nEND:VCARD"
    )
    # Force fallback by using _fallback_parse directly
    from schnabel.reader import _fallback_parse
    contact = _fallback_parse(vcard_text, "test.vcf")
    assert contact is not None
    assert sorted(contact.categories) == ["Pfadi", "WG"]


def test_categories_property():
    """The categories property should filter only category fields."""
    c = Contact(fn="Test")
    c.fields.append(ContactField("email", "test@test.com"))
    c.fields.append(ContactField("categories", "Armee"))
    c.fields.append(ContactField("tel", "+41791234567"))
    c.fields.append(ContactField("categories", "Pfadi"))

    assert c.categories == ["Armee", "Pfadi"]
    assert c.emails == ["test@test.com"]
    assert c.phones == ["+41791234567"]
