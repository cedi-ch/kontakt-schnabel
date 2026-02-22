"""Tests for within-contact field sanitization."""

from schnabel.model import Contact, ContactField
from schnabel.sanitize import sanitize_contacts, _parse_bday, BdayAmbiguous


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


# ── BDAY parsing unit tests ──────────────────────────────────────────────


def test_bday_already_iso():
    """Already clean ISO date → None (no change)."""
    assert _parse_bday("1985-04-03") is None


def test_bday_partial_date_clean():
    """Already clean partial date --MM-DD → None."""
    assert _parse_bday("--04-03") is None


def test_bday_compact_iso():
    """Compact YYYYMMDD → normalized."""
    assert _parse_bday("19850403") == "1985-04-03"


def test_bday_datetime_strip_time():
    """DateTime YYYY-MM-DDT... → strip time portion."""
    assert _parse_bday("1985-04-03T12:00:00Z") == "1985-04-03"


def test_bday_swiss_dd_mm_yyyy_unambiguous():
    """DD.MM.YYYY with day > 12 → unambiguous Swiss format."""
    assert _parse_bday("25.03.1985") == "1985-03-25"


def test_bday_swiss_dd_mm_yy():
    """DD.MM.YY with 2-digit year expansion."""
    assert _parse_bday("25.03.85") == "1985-03-25"


def test_bday_swiss_dd_mm_yy_2000s():
    """DD.MM.YY with year ≤ 30 → 20xx."""
    assert _parse_bday("15.06.05") == "2005-06-15"


def test_bday_dd_mm_no_year():
    """DD.MM without year → partial date --MM-DD."""
    assert _parse_bday("25.03.") == "--03-25"
    assert _parse_bday("25.03") == "--03-25"


def test_bday_dd_mm_both_le_12_assumes_swiss():
    """DD.MM.YYYY with both ≤ 12 assumes DD.MM (Swiss locale)."""
    assert _parse_bday("03.04.1985") == "1985-04-03"


def test_bday_slash_unambiguous():
    """Slash-separated with day > 12 → unambiguous."""
    assert _parse_bday("25/03/1985") == "1985-03-25"


def test_bday_slash_ambiguous():
    """Slash-separated with both ≤ 12 → BdayAmbiguous."""
    result = _parse_bday("03/04/1985")
    assert isinstance(result, BdayAmbiguous)
    assert result.option_a == "1985-04-03"  # DD/MM (European)
    assert result.option_b == "1985-03-04"  # MM/DD (US)
    assert "April" in result.label_a
    assert "März" in result.label_b


def test_bday_text_german():
    """German text date: '15. März 1985'."""
    assert _parse_bday("15. März 1985") == "1985-03-15"


def test_bday_text_english():
    """English text date: 'March 15, 1985'."""
    assert _parse_bday("March 15, 1985") == "1985-03-15"


def test_bday_sanitize_integration(tmp_db):
    """BDAY normalization through the full sanitize pipeline."""
    c = _make_contact([
        ("bday", "19850403"),
        ("email", "test@example.com"),
    ])
    cid = tmp_db.insert_contact(c)
    tmp_db.commit()

    report = sanitize_contacts(tmp_db)

    contact = tmp_db.get_contact(cid)
    bdays = [f for f in contact.fields if f.field_type == "bday"]
    assert len(bdays) == 1
    assert bdays[0].field_value == "1985-04-03"
    assert report.reformatted["bday"] == 1


def test_bday_ambiguous_collected(tmp_db):
    """Ambiguous BDAY is collected, not auto-resolved."""
    c = _make_contact([
        ("bday", "03/04/1985"),
    ], fn="Hans Muster")
    cid = tmp_db.insert_contact(c)
    tmp_db.commit()

    report = sanitize_contacts(tmp_db)

    assert len(report.ambiguous_bdays) == 1
    amb = report.ambiguous_bdays[0]
    assert amb.contact_id == cid
    assert amb.contact_fn == "Hans Muster"
    assert amb.option_a == "1985-04-03"
    assert amb.option_b == "1985-03-04"
    # Field should NOT have been changed yet
    contact = tmp_db.get_contact(cid)
    bdays = [f for f in contact.fields if f.field_type == "bday"]
    assert bdays[0].field_value == "03/04/1985"
