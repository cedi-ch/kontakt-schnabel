"""Tests for vCard export."""

import re
import uuid

from schnabel.export import contact_to_vcard, _fold_line, _format_phone
from schnabel.model import Contact, ContactField, Photo
from schnabel.reader import parse_vcard


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


# --- Audit BUG-01: FN must NOT escape commas/semicolons ---

def test_fn_comma_not_escaped():
    """FN is a free-text field — commas must NOT be escaped."""
    c = Contact(fn="Smith, Jr.", family_name="Smith", given_name="John")
    vcard = contact_to_vcard(c)
    assert "FN:Smith, Jr." in vcard
    assert "FN:Smith\\," not in vcard


def test_fn_semicolon_not_escaped():
    """FN is a free-text field — semicolons must NOT be escaped."""
    c = Contact(fn="O;Brien", family_name="O;Brien", given_name="Test")
    vcard = contact_to_vcard(c)
    assert "FN:O;Brien" in vcard
    assert "FN:O\\;" not in vcard


def test_fn_newline_escaped():
    """FN must escape newlines."""
    c = Contact(fn="Line1\nLine2", family_name="Test", given_name="NL")
    vcard = contact_to_vcard(c)
    assert "FN:Line1\\nLine2" in vcard


# --- Bug #1: N-field escaping ---

def test_n_field_semicolon_escaping():
    """Semicolons in name parts must be escaped to avoid breaking N field structure."""
    c = Contact(fn="O;Brien", family_name="O;Brien", given_name="Conan")
    vcard = contact_to_vcard(c)
    assert "N:O\\;Brien;Conan;;;" in vcard


def test_n_field_comma_preserved():
    """Commas in N components are multi-value separators per RFC 2426 — NOT escaped."""
    c = Contact(fn="Smith, Jr.", family_name="Smith, Jr.", given_name="John")
    vcard = contact_to_vcard(c)
    # Comma in N component should NOT be escaped (it's a valid multi-value separator)
    assert "N:Smith, Jr.;John;;;" in vcard


# --- Bug #2 + #3: Empty/None fields ---

def test_empty_field_skipped():
    """Fields with empty string value should not appear in output."""
    c = Contact(fn="Test User", family_name="User", given_name="Test")
    c.fields.append(ContactField("email", ""))
    c.fields.append(ContactField("tel", ""))
    vcard = contact_to_vcard(c)
    assert "EMAIL" not in vcard
    assert "TEL" not in vcard


def test_whitespace_field_skipped():
    """Fields with only whitespace should not appear in output."""
    c = Contact(fn="Test User", family_name="User", given_name="Test")
    c.fields.append(ContactField("email", "   "))
    c.fields.append(ContactField("note", "  \n  "))
    vcard = contact_to_vcard(c)
    assert "EMAIL" not in vcard
    assert "NOTE" not in vcard


def test_none_field_skipped():
    """Fields with None value should not produce 'None' in output."""
    c = Contact(fn="Test User", family_name="User", given_name="Test")
    c.fields.append(ContactField("email", None))
    vcard = contact_to_vcard(c)
    assert "EMAIL" not in vcard
    assert "None" not in vcard


# --- Bug #20: PHOTO ENCODING ---

def test_photo_encoding_base64():
    """PHOTO must use ENCODING=BASE64, not ENCODING=b."""
    c = Contact(fn="Photo Test", family_name="Test", given_name="Photo")
    c.photos.append(Photo(
        photo_data=b"\xff\xd8\xff\xe0" + b"\x00" * 200,
        photo_format="JPEG",
    ))
    vcard = contact_to_vcard(c)
    assert "ENCODING=BASE64" in vcard
    assert "ENCODING=b" not in vcard


# --- Bug #8: ADR with commas ---

def test_adr_with_comma_in_street():
    """Address with commas in street name must survive export (semicolon-separated storage)."""
    c = Contact(fn="Addr Test", family_name="Test", given_name="Addr")
    # 7-component format: PO;Extended;Street;City;Region;Code;Country
    c.fields.append(ContactField("adr", ";;Hauptstrasse 1, Apt 3;Zürich;;8001;CH"))
    vcard = contact_to_vcard(c)
    # The comma in the street should be escaped in ADR (structured field)
    assert "Hauptstrasse 1\\, Apt 3" in vcard
    assert "8001" in vcard


def test_adr_7_components():
    """ADR must output all 7 RFC 2426 components."""
    c = Contact(fn="ADR7 Test", family_name="Test", given_name="ADR7")
    c.fields.append(ContactField("adr", "PO Box 1;Suite 2;Bahnhofstr 3;Zürich;ZH;8001;CH"))
    vcard = contact_to_vcard(c)
    assert "ADR" in vcard
    # Find the ADR line
    for line in vcard.split("\r\n"):
        if line.startswith("ADR"):
            adr_value = line.split(":", 1)[1]
            parts = adr_value.split(";")
            assert len(parts) == 7
            assert parts[0] == "PO Box 1"
            assert parts[2] == "Bahnhofstr 3"
            assert parts[5] == "8001"
            break
    else:
        assert False, "No ADR line found"


def test_adr_legacy_5_parts_gets_padded():
    """Legacy 5-part comma-separated addresses should be padded to 7 components."""
    c = Contact(fn="Legacy ADR", family_name="Test", given_name="Legacy")
    # Old comma-separated format
    c.fields.append(ContactField("adr", "Strasse 1, Zürich, , 8001, CH"))
    vcard = contact_to_vcard(c)
    for line in vcard.split("\r\n"):
        if line.startswith("ADR"):
            adr_value = line.split(":", 1)[1]
            parts = adr_value.split(";")
            assert len(parts) == 7
            break


# --- UID tests ---

def test_uid_generated_when_empty():
    """A contact without UID should get a generated UUID v4."""
    c = Contact(fn="No UID", family_name="UID", given_name="No")
    vcard = contact_to_vcard(c)
    # Find the UID line
    match = re.search(r"UID:(.+)", vcard)
    assert match is not None
    uid_value = match.group(1).strip()
    # Validate it's a UUID
    parsed = uuid.UUID(uid_value)
    assert parsed.version == 4


def test_uid_preserved_when_set():
    """A contact with existing UID should keep it."""
    fixed_uid = "12345678-1234-4321-abcd-123456789abc"
    c = Contact(fn="Has UID", family_name="UID", given_name="Has", uid=fixed_uid)
    vcard = contact_to_vcard(c)
    assert f"UID:{fixed_uid}" in vcard


def test_uid_is_valid_uuid4():
    """Generated UIDs should be valid UUID v4."""
    c = Contact(fn="UUID Test", family_name="Test", given_name="UUID")
    vcard = contact_to_vcard(c)
    match = re.search(r"UID:(.+)", vcard)
    assert match
    uid_str = match.group(1).strip()
    parsed = uuid.UUID(uid_str)
    assert parsed.version == 4


# --- Roundtrip tests ---

def _roundtrip(contact: Contact) -> Contact:
    """Export a contact to vCard, then re-import it."""
    vcard_text = contact_to_vcard(contact)
    # Convert CRLF to LF for parser compatibility
    reimported = parse_vcard(vcard_text)
    assert reimported is not None, f"Re-import failed for:\n{vcard_text}"
    return reimported


def test_roundtrip_n_escaping():
    """Semicolons in names must survive export→re-import."""
    c = Contact(fn="O'Brien, Conan", family_name="O;Brien", given_name="Conan")
    rt = _roundtrip(c)
    assert rt.family_name == "O;Brien"
    assert rt.given_name == "Conan"


def test_roundtrip_adr_preserved():
    """Address data must survive export→re-import with all 7 components."""
    c = Contact(fn="Addr RT", family_name="RT", given_name="Addr")
    # 7-component: PO;Extended;Street;City;Region;Code;Country
    c.fields.append(ContactField("adr", ";;Bahnhofstrasse 1;Zürich;;8001;Schweiz"))
    rt = _roundtrip(c)
    addrs = rt.addresses
    assert len(addrs) == 1
    assert "Bahnhofstrasse 1" in addrs[0]
    assert "Zürich" in addrs[0]
    assert "8001" in addrs[0]


def test_roundtrip_uid_preserved():
    """UID must survive export→re-import."""
    fixed_uid = "aaaaaaaa-bbbb-4ccc-dddd-eeeeeeeeeeee"
    c = Contact(fn="UID RT", family_name="RT", given_name="UID", uid=fixed_uid)
    rt = _roundtrip(c)
    assert rt.uid == fixed_uid


def test_roundtrip_categories():
    """CATEGORIES must survive export→re-import."""
    c = Contact(fn="Cat RT", family_name="RT", given_name="Cat")
    c.fields.append(ContactField("categories", "Friends"))
    c.fields.append(ContactField("categories", "Work"))
    rt = _roundtrip(c)
    cats = sorted(rt.categories)
    assert "Friends" in cats
    assert "Work" in cats


def test_roundtrip_bday_with_year():
    """BDAY with full date must survive roundtrip."""
    c = Contact(fn="Bday RT", family_name="RT", given_name="Bday")
    c.fields.append(ContactField("bday", "1990-05-15"))
    rt = _roundtrip(c)
    bdays = [f.field_value for f in rt.fields if f.field_type == "bday"]
    assert len(bdays) == 1
    assert "1990-05-15" in bdays[0]


def test_roundtrip_swiss_chars():
    """Swiss special characters (äöüéè) must survive roundtrip in all fields."""
    c = Contact(
        fn="Müller Résumé",
        family_name="Müller",
        given_name="René",
    )
    c.fields.append(ContactField("org", "Zürich Café"))
    c.fields.append(ContactField("note", "Grüezi, ça va?"))
    rt = _roundtrip(c)
    assert rt.family_name == "Müller"
    assert rt.given_name == "René"
    orgs = [f.field_value for f in rt.fields if f.field_type == "org"]
    assert any("Zürich" in o for o in orgs)
