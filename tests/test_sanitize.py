"""Tests for within-contact field sanitization."""

from schnabel.model import Contact, ContactField
from schnabel.sanitize import sanitize_contacts


def _make_contact(fields: list[tuple[str, str]], fn: str = "Test") -> Contact:
    """Helper to create a Contact with the given fields."""
    c = Contact(fn=fn, category="real")
    for ftype, fval in fields:
        c.fields.append(ContactField(field_type=ftype, field_value=fval))
    return c


def test_phone_dedup_same_number_different_format(tmp_db):
    """Two phone fields with different formatting of the same number → 1 left."""
    c = _make_contact([
        ("tel", "+41791234567"),
        ("tel", "079 123 45 67"),
    ])
    cid = tmp_db.insert_contact(c)
    tmp_db.commit()

    report = sanitize_contacts(tmp_db)

    contact = tmp_db.get_contact(cid)
    phones = [f for f in contact.fields if f.field_type == "tel"]
    assert len(phones) == 1
    assert report.removed["tel"] == 1


def test_email_dedup_case_insensitive(tmp_db):
    """Two email fields differing only in case → 1 left."""
    c = _make_contact([
        ("email", "Hans@Gmail.com"),
        ("email", "hans@gmail.com"),
    ])
    cid = tmp_db.insert_contact(c)
    tmp_db.commit()

    report = sanitize_contacts(tmp_db)

    contact = tmp_db.get_contact(cid)
    emails = [f for f in contact.fields if f.field_type == "email"]
    assert len(emails) == 1
    assert report.removed["email"] == 1


def test_address_dedup_separator(tmp_db):
    """Two addresses differing only in separators → 1 left."""
    c = _make_contact([
        ("adr", "Bahnhofstr 1; 8001; Zürich"),
        ("adr", "Bahnhofstr 1, 8001, Zürich"),
    ])
    cid = tmp_db.insert_contact(c)
    tmp_db.commit()

    report = sanitize_contacts(tmp_db)

    contact = tmp_db.get_contact(cid)
    addrs = [f for f in contact.fields if f.field_type == "adr"]
    assert len(addrs) == 1
    assert report.removed["adr"] == 1


def test_url_dedup_trailing_slash(tmp_db):
    """Two URLs differing only in trailing slash → 1 left."""
    c = _make_contact([
        ("url", "https://example.com/"),
        ("url", "https://example.com"),
    ])
    cid = tmp_db.insert_contact(c)
    tmp_db.commit()

    report = sanitize_contacts(tmp_db)

    contact = tmp_db.get_contact(cid)
    urls = [f for f in contact.fields if f.field_type == "url"]
    assert len(urls) == 1
    assert report.removed["url"] == 1


def test_remove_empty_fields(tmp_db):
    """Empty and whitespace-only fields get removed."""
    c = _make_contact([
        ("email", "valid@example.com"),
        ("tel", ""),
        ("adr", "   "),
        ("note", "  \n  "),
    ])
    cid = tmp_db.insert_contact(c)
    tmp_db.commit()

    report = sanitize_contacts(tmp_db)

    contact = tmp_db.get_contact(cid)
    assert len(contact.fields) == 1
    assert contact.fields[0].field_type == "email"
    assert report.removed["empty"] == 3


def test_sanitize_no_changes_on_clean_contact(tmp_db):
    """A clean contact with no duplicates should not be altered."""
    c = _make_contact([
        ("email", "alice@example.com"),
        ("tel", "+41791234567"),
        ("adr", "Hauptstrasse 1, 3000, Bern"),
        ("org", "ACME Corp"),
    ])
    cid = tmp_db.insert_contact(c)
    tmp_db.commit()

    report = sanitize_contacts(tmp_db)

    contact = tmp_db.get_contact(cid)
    assert len(contact.fields) == 4
    assert report.total_removed == 0


def test_text_dedup_case_insensitive(tmp_db):
    """Duplicate ORG fields differing only in case → 1 left."""
    c = _make_contact([
        ("org", "ACME Corp"),
        ("org", "acme corp"),
    ])
    cid = tmp_db.insert_contact(c)
    tmp_db.commit()

    report = sanitize_contacts(tmp_db)

    contact = tmp_db.get_contact(cid)
    orgs = [f for f in contact.fields if f.field_type == "org"]
    assert len(orgs) == 1
    assert report.removed["text"] == 1


def test_url_www_dedup(tmp_db):
    """URLs differing only by www. prefix → 1 left."""
    c = _make_contact([
        ("url", "https://www.example.com"),
        ("url", "https://example.com"),
    ])
    cid = tmp_db.insert_contact(c)
    tmp_db.commit()

    report = sanitize_contacts(tmp_db)

    contact = tmp_db.get_contact(cid)
    urls = [f for f in contact.fields if f.field_type == "url"]
    assert len(urls) == 1
    assert report.removed["url"] == 1
