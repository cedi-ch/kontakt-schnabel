"""Tests for all audit P2/P3 bug fixes."""

from schnabel.model import Contact, ContactField, Photo
from schnabel.merge import merge_contacts, undo_merge
from schnabel.export import contact_to_vcard, _escape_text_value, _escape_n_component
from schnabel.reader import parse_vcard
from schnabel.match import score_contacts


# --- BUG-06: Undo reverses name adoption ---

def test_undo_reverses_name(tmp_db):
    """Undo must restore the survivor's original name."""
    c1 = Contact(fn="email@only.com", category="real")
    c1.fields.append(ContactField("email", "email@only.com"))

    c2 = Contact(fn="Hans Mueller", family_name="Mueller", given_name="Hans", category="real")
    c2.fields.append(ContactField("email", "hans@other.com"))

    id1 = tmp_db.insert_contact(c1)
    id2 = tmp_db.insert_contact(c2)
    tmp_db.commit()

    merge_id = merge_contacts(tmp_db, survivor_id=id1, absorbed_id=id2)

    # Survivor should have adopted the name
    survivor = tmp_db.get_contact(id1)
    assert survivor.family_name == "Mueller"

    # Undo
    undo_merge(tmp_db, merge_id)

    # Survivor's name should be restored
    survivor_after = tmp_db.get_contact(id1)
    assert survivor_after.family_name == ""
    assert survivor_after.fn == "email@only.com"


# --- BUG-07: Undo reverses photo additions ---

def test_undo_reverses_photo(tmp_db):
    """Undo must remove photos that were added during merge."""
    c1 = Contact(fn="No Photo", family_name="Test", given_name="A", category="real")
    c1.fields.append(ContactField("email", "a@test.com"))

    c2 = Contact(fn="Has Photo", family_name="Test", given_name="B", category="real")
    c2.fields.append(ContactField("email", "b@test.com"))
    c2.photos.append(Photo(
        photo_data=b"\xff\xd8" + b"\x00" * 200,
        photo_format="JPEG",
        byte_hash="unique_hash_123",
    ))

    id1 = tmp_db.insert_contact(c1)
    id2 = tmp_db.insert_contact(c2)
    tmp_db.commit()

    merge_id = merge_contacts(tmp_db, survivor_id=id1, absorbed_id=id2)

    survivor = tmp_db.get_contact(id1)
    assert len(survivor.photos) == 1

    undo_merge(tmp_db, merge_id)

    survivor_after = tmp_db.get_contact(id1)
    assert len(survivor_after.photos) == 0


# --- BUG-09/10: Multiple URLs, ORGs, NOTEs parsed ---

def test_multiple_urls_parsed():
    """All URLs should be parsed, not just the first."""
    vcard_text = (
        "BEGIN:VCARD\n"
        "VERSION:3.0\n"
        "FN:Multi URL\n"
        "N:URL;Multi;;;\n"
        "URL:https://example.com\n"
        "URL:https://other.com\n"
        "END:VCARD"
    )
    contact = parse_vcard(vcard_text)
    assert contact is not None
    urls = [f.field_value for f in contact.fields if f.field_type == "url"]
    assert len(urls) == 2
    assert "https://example.com" in urls
    assert "https://other.com" in urls


def test_multiple_notes_parsed():
    """All NOTEs should be parsed."""
    vcard_text = (
        "BEGIN:VCARD\n"
        "VERSION:3.0\n"
        "FN:Multi Note\n"
        "N:Note;Multi;;;\n"
        "NOTE:First note\n"
        "NOTE:Second note\n"
        "END:VCARD"
    )
    contact = parse_vcard(vcard_text)
    assert contact is not None
    notes = [f.field_value for f in contact.fields if f.field_type == "note"]
    assert len(notes) == 2


# --- BUG-12: Shared scoring function ---

def test_score_contacts_matches_score_pair(tmp_db):
    """score_contacts (direct) and score_pair (via DB) should give same results."""
    from schnabel.match import score_pair

    c1 = Contact(fn="Hans Mueller", family_name="Mueller", given_name="Hans", category="real")
    c1.fields.append(ContactField("email", "hans@gmail.com"))
    c1.fields.append(ContactField("tel", "+41791234567"))

    c2 = Contact(fn="Hans Müller", family_name="Müller", given_name="Hans", category="real")
    c2.fields.append(ContactField("email", "hans@gmail.com"))
    c2.fields.append(ContactField("tel", "+41791234567"))

    # Direct scoring
    direct = score_contacts(c1, c2)

    # DB-backed scoring
    id1 = tmp_db.insert_contact(c1)
    id2 = tmp_db.insert_contact(c2)
    tmp_db.commit()
    db_based = score_pair(tmp_db, id1, id2)

    assert direct["confidence"] == db_based["confidence"]
    assert direct["email_score"] == db_based["email_score"]
    assert direct["phone_score"] == db_based["phone_score"]


# --- BUG-14: CATEGORIES escaped ---

def test_categories_comma_escaped():
    """Category names with commas must be escaped in export."""
    c = Contact(fn="Cat Test", family_name="Test", given_name="Cat")
    c.fields.append(ContactField("categories", "Friends, Family"))
    vcard = contact_to_vcard(c)
    assert "Friends\\, Family" in vcard


# --- BUG-25: repair_n_field wired into sanitize ---

def test_repair_n_field_runs_during_sanitize(tmp_db):
    """Sanitize should fix swapped given/family names."""
    from schnabel.sanitize import sanitize_contacts

    c = Contact(fn="Hans Mueller", family_name="Hans", given_name="Mueller", category="real")
    cid = tmp_db.insert_contact(c)
    tmp_db.commit()

    sanitize_contacts(tmp_db)

    fixed = tmp_db.get_contact(cid)
    assert fixed.given_name == "Hans"
    assert fixed.family_name == "Mueller"


# --- Escaping functions ---

def test_escape_text_value_preserves_semicolon():
    """Free-text escaping must NOT escape semicolons."""
    assert _escape_text_value("a;b") == "a;b"


def test_escape_text_value_preserves_comma():
    """Free-text escaping must NOT escape commas."""
    assert _escape_text_value("a,b") == "a,b"


def test_escape_text_value_escapes_backslash():
    assert _escape_text_value("a\\b") == "a\\\\b"


def test_escape_text_value_escapes_newline():
    assert _escape_text_value("a\nb") == "a\\nb"


def test_escape_n_component_escapes_semicolon():
    """N component escaping must escape semicolons."""
    assert _escape_n_component("O;Brien") == "O\\;Brien"


def test_escape_n_component_preserves_comma():
    """N component escaping must NOT escape commas (multi-value separator)."""
    assert _escape_n_component("Hans,Peter") == "Hans,Peter"


# --- ADR 7-component roundtrip ---

def test_adr_7_component_roundtrip():
    """All 7 ADR components must survive export→re-import."""
    c = Contact(fn="ADR RT", family_name="Test", given_name="ADR")
    c.fields.append(ContactField("adr", "PO Box 1;Apt 2;Strasse 3;Zürich;ZH;8001;CH"))
    vcard = contact_to_vcard(c)

    rt = parse_vcard(vcard)
    assert rt is not None
    addrs = rt.addresses
    assert len(addrs) == 1
    parts = addrs[0].split(";")
    assert len(parts) == 7
    assert parts[0] == "PO Box 1"
    assert parts[1] == "Apt 2"
    assert parts[2] == "Strasse 3"
    assert parts[5] == "8001"
    assert parts[6] == "CH"
