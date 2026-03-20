"""Tests for vCard parser."""

import base64
import tempfile
from pathlib import Path

from schnabel.reader import (
    _fallback_parse, _is_valid_field_value, _looks_like_base64,
    _sanitize_contact_fields, _unfold_lines, fix_broken_qp,
    parse_vcard, parse_vcf_file, split_vcards,
)
from schnabel.model import Contact, ContactField


def test_split_vcards():
    text = (
        "BEGIN:VCARD\nVERSION:3.0\nFN:Test\nEND:VCARD\n"
        "BEGIN:VCARD\nVERSION:3.0\nFN:Test2\nEND:VCARD\n"
    )
    cards = split_vcards(text)
    assert len(cards) == 2


def test_parse_simple_contact():
    vcard_text = "BEGIN:VCARD\nVERSION:3.0\nFN:Hans Mueller\nN:Mueller;Hans;;;\nEMAIL:hans@test.com\nTEL:079 123 45 67\nEND:VCARD"
    contact = parse_vcard(vcard_text)
    assert contact is not None
    assert contact.fn == "Hans Mueller"
    assert contact.family_name == "Mueller"
    assert contact.given_name == "Hans"
    assert len(contact.emails) == 1
    assert contact.emails[0] == "hans@test.com"
    assert len(contact.phones) == 1


def test_parse_stub_contact():
    vcard_text = "BEGIN:VCARD\nVERSION:3.0\nFN:someone@example.com\nN:;;;;\nEMAIL:someone@example.com\nEND:VCARD"
    contact = parse_vcard(vcard_text)
    assert contact is not None
    assert not contact.has_structured_name
    assert len(contact.emails) == 1


def test_parse_vcf_file(fixtures_dir):
    contacts, encoding = parse_vcf_file(fixtures_dir / "simple.vcf")
    assert len(contacts) == 5
    assert contacts[0].fn == "Hans Mueller"
    assert contacts[4].fn == "Peter Meier"


def test_parse_contact_with_org():
    vcard_text = "BEGIN:VCARD\nVERSION:3.0\nFN:Test\nN:Test;;;;\nORG:ACME Inc\nTITLE:CEO\nEND:VCARD"
    contact = parse_vcard(vcard_text)
    assert len(contact.orgs) == 1
    assert contact.orgs[0] == "ACME Inc"


# -- Photo handling: regression tests for continuation line bugs --

def test_unfold_lines_joins_continuations():
    """RFC 2426 continuation lines (starting with space) should be joined."""
    text = "PHOTO;ENCODING=b;TYPE=JPEG:/9j/4AAQ\n SkZJRgABAQEA\n SABY=\nTEL:+41791234567"
    lines = _unfold_lines(text)
    assert len(lines) == 2
    assert lines[0].startswith("PHOTO")
    assert "SkZJRgABAQEA" in lines[0]
    assert "SABY=" in lines[0]
    assert lines[1] == "TEL:+41791234567"


def test_fallback_parse_photo_continuation_not_extracted_as_tel():
    """PHOTO base64 continuation lines must not be extracted as TEL/EMAIL fields.

    Regression test: before the fix, if a PHOTO continuation line happened to
    contain 'TEL' in its base64 data, the fallback parser extracted it as a
    phone field, losing the photo and creating a bogus TEL entry.
    """
    # Simulate a PHOTO field with continuation lines containing 'TEL:'
    vcard_text = (
        "BEGIN:VCARD\n"
        "VERSION:3.0\n"
        "FN:Photo Test\n"
        "N:Test;Photo;;;\n"
        "PHOTO;ENCODING=b;TYPE=JPEG:/9j/4AAQ\n"
        " TEL:+41111111111\n"       # continuation line that looks like TEL
        " EMAIL:fake@data.com\n"    # continuation line that looks like EMAIL
        " ABCDEFGH\n"
        "TEL:+41791234567\n"
        "END:VCARD\n"
    )
    contact = _fallback_parse(vcard_text, "test.vcf")
    assert contact is not None
    # Should have exactly one TEL (the real one), not the PHOTO continuation
    assert len(contact.phones) == 1
    assert contact.phones[0] == "+41791234567"
    # Should have no emails (the PHOTO continuation is not a real EMAIL)
    assert len(contact.emails) == 0


def test_fallback_parse_photo_continuation_not_extracted_as_email():
    """Base64 data containing '@' must not become an email field."""
    vcard_text = (
        "BEGIN:VCARD\n"
        "VERSION:3.0\n"
        "FN:Test\n"
        "N:Test;;;;\n"
        "PHOTO;ENCODING=b;TYPE=JPEG:/9j/\n"
        " abc@def.ghijk\n"
        "EMAIL:real@example.com\n"
        "END:VCARD\n"
    )
    contact = _fallback_parse(vcard_text, "test.vcf")
    assert contact is not None
    assert len(contact.emails) == 1
    assert contact.emails[0] == "real@example.com"


def test_fix_broken_qp_protects_photo_continuation_lines():
    """fix_broken_qp must not decode =XX sequences inside PHOTO continuation lines.

    Regression test: before the fix, continuation lines of PHOTO fields
    were decoded as QP, corrupting the base64 data.
    """
    text = (
        "PHOTO;ENCODING=b;TYPE=JPEG:/9j/4AAQ=3D\n"
        " SkZJRg=3DABAQEA\n"   # continuation — =3D should NOT be decoded
        "FN:Hans =C3=BCber\n"   # regular field — =C3=BC should be decoded to ü
    )
    result = fix_broken_qp(text)
    lines = result.split("\n")
    # PHOTO header: preserved (already protected)
    assert "=3D" in lines[0]
    # PHOTO continuation: preserved (must NOT be decoded)
    assert "=3D" in lines[1]
    # FN: QP decoded
    assert "über" in lines[2] or "=C3=BC" not in lines[2]


def test_fix_broken_qp_non_photo_continuation_still_decoded():
    """Non-binary continuation lines should still get QP decoded."""
    text = (
        "NOTE:Dies ist eine =C3=BC\n"
        "FN:Test =C3=A4\n"
    )
    result = fix_broken_qp(text)
    # Both should be decoded
    assert "ü" in result or "=C3=BC" not in result
    assert "ä" in result or "=C3=A4" not in result


# -- Field validation: base64 detection and sanitization --

def test_looks_like_base64_detects_photo_data():
    """Long base64 strings should be detected."""
    b64 = base64.b64encode(b"\x00" * 300).decode("ascii")
    assert _looks_like_base64(b64)


def test_looks_like_base64_rejects_short_strings():
    """Short strings should not be flagged as base64."""
    assert not _looks_like_base64("+41 79 123 45 67")
    assert not _looks_like_base64("hans@example.com")
    assert not _looks_like_base64("")


def test_looks_like_base64_rejects_text_with_spaces():
    """Long text with spaces is not base64."""
    text = "This is a very long note " * 20
    assert not _looks_like_base64(text)


def test_is_valid_phone_rejects_base64():
    """Base64 data must not pass as a phone number."""
    b64 = base64.b64encode(b"\xff\xd8\xff\xe0" * 100).decode("ascii")
    assert not _is_valid_field_value("tel", b64)


def test_is_valid_phone_rejects_too_long():
    """Strings over 40 chars are not phone numbers."""
    assert not _is_valid_field_value("tel", "1" * 41)


def test_is_valid_phone_accepts_normal():
    """Normal phone numbers should pass."""
    assert _is_valid_field_value("tel", "+41 79 123 45 67")
    assert _is_valid_field_value("tel", "079 123 45 67")
    assert _is_valid_field_value("tel", "+49 170 1234567")
    assert _is_valid_field_value("tel", "(212) 555-1234")


def test_is_valid_email_rejects_base64():
    """Base64 data must not pass as an email address."""
    b64 = base64.b64encode(b"\x00" * 300).decode("ascii")
    assert not _is_valid_field_value("email", b64)


def test_is_valid_email_accepts_normal():
    """Normal email addresses should pass."""
    assert _is_valid_field_value("email", "hans@example.com")
    assert _is_valid_field_value("email", "user+tag@sub.domain.org")


def test_sanitize_contact_fields_removes_corrupt_tel():
    """_sanitize_contact_fields must strip base64 from tel fields."""
    b64 = base64.b64encode(b"\xff\xd8" * 200).decode("ascii")
    contact = Contact(fn="Test")
    contact.fields = [
        ContactField("tel", "+41791234567"),
        ContactField("tel", b64),  # corrupt — base64 photo data
        ContactField("email", "real@example.com"),
    ]
    _sanitize_contact_fields(contact)
    assert len(contact.phones) == 1
    assert contact.phones[0] == "+41791234567"
    assert len(contact.emails) == 1


def test_vobject_path_rejects_base64_in_tel():
    """Even if vobject puts base64 in tel_list, parse_vcard must reject it.

    This simulates the actual bug: vobject silently mis-parses PHOTO
    continuation lines and populates tel_list with base64 data.
    """
    # Create a real base64 photo blob (>200 chars)
    fake_photo = base64.b64encode(b"\xff\xd8\xff\xe0" * 100).decode("ascii")

    # Build a vCard where the PHOTO is properly formatted but long.
    # We add a TEL with the base64 value directly to test the validation layer
    # (we can't force vobject to mis-parse, but we can test the post-parse filter)
    vcard_text = (
        "BEGIN:VCARD\n"
        "VERSION:3.0\n"
        "FN:Test Contact\n"
        "N:Contact;Test;;;\n"
        "TEL:+41791234567\n"
        "EMAIL:test@example.com\n"
        "END:VCARD\n"
    )
    contact = parse_vcard(vcard_text)
    assert contact is not None

    # Simulate what vobject does wrong: inject base64 into tel field
    contact.fields.append(ContactField("tel", fake_photo))
    _sanitize_contact_fields(contact)

    # The real phone should survive, the fake should be gone
    assert len(contact.phones) == 1
    assert contact.phones[0] == "+41791234567"


def test_roundtrip_export_import_no_photo_bleed():
    """Roundtrip test: export a contact with photo, re-import, verify no bleed.

    This is the critical end-to-end test: write a vCard with PHOTO,
    read it back, and confirm no base64 data ends up in tel/email fields.
    """
    from schnabel.export import contact_to_vcard
    from schnabel.model import Photo

    # Create a contact with photo + real fields
    contact = Contact(
        fn="Regula Bochsler",
        family_name="Bochsler",
        given_name="Regula",
    )
    contact.fields = [
        ContactField("tel", "+41791234567"),
        ContactField("email", "regula@example.com"),
    ]
    # Create a fake but realistic-size photo (4KB of JPEG-like data)
    photo_data = b"\xff\xd8\xff\xe0" + b"\x00" * 4000 + b"\xff\xd9"
    contact.photos = [Photo(
        photo_data=photo_data,
        photo_format="JPEG",
        byte_hash="fakehash",
    )]

    # Export to VCF string
    vcard_str = contact_to_vcard(contact)

    # Verify export looks reasonable
    assert "PHOTO;ENCODING=BASE64;TYPE=JPEG:" in vcard_str
    assert "TEL:" in vcard_str
    assert "EMAIL:" in vcard_str

    # Write to temp file and re-import
    with tempfile.NamedTemporaryFile(mode="w", suffix=".vcf", delete=False,
                                      encoding="utf-8") as f:
        f.write(vcard_str)
        tmp_path = Path(f.name)

    try:
        contacts, _enc = parse_vcf_file(tmp_path)
        assert len(contacts) == 1
        reimported = contacts[0]

        # The critical assertions: no photo data in text fields
        assert len(reimported.phones) == 1, (
            f"Expected 1 phone, got {len(reimported.phones)}: "
            f"{[p[:50] for p in reimported.phones]}"
        )
        # Export formats Swiss numbers to national format
        assert "79" in reimported.phones[0] and "123" in reimported.phones[0]

        assert len(reimported.emails) == 1, (
            f"Expected 1 email, got {len(reimported.emails)}: "
            f"{[e[:50] for e in reimported.emails]}"
        )
        assert reimported.emails[0] == "regula@example.com"

        # No field should be longer than 300 chars
        for field in reimported.fields:
            assert len(field.field_value) < 300, (
                f"Field {field.field_type} has suspiciously long value "
                f"({len(field.field_value)} chars): {field.field_value[:80]}..."
            )
    finally:
        tmp_path.unlink()


def test_roundtrip_large_photo_no_bleed():
    """Roundtrip with a large photo (40KB+) — the size that triggered the original bug."""
    from schnabel.export import contact_to_vcard
    from schnabel.model import Photo

    contact = Contact(
        fn="Laura Thaqi",
        family_name="Thaqi",
        given_name="Laura",
    )
    contact.fields = [
        ContactField("tel", "+41761234567"),
        ContactField("tel", "+41441234567"),
        ContactField("email", "laura@example.com"),
    ]
    # Large photo: 40KB — this is what caused the original 6084pt table cell
    photo_data = b"\xff\xd8\xff\xe0" + bytes(range(256)) * 160 + b"\xff\xd9"
    contact.photos = [Photo(
        photo_data=photo_data,
        photo_format="JPEG",
        byte_hash="fakehash2",
    )]

    vcard_str = contact_to_vcard(contact)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".vcf", delete=False,
                                      encoding="utf-8") as f:
        f.write(vcard_str)
        tmp_path = Path(f.name)

    try:
        contacts, _enc = parse_vcf_file(tmp_path)
        assert len(contacts) == 1
        reimported = contacts[0]

        assert len(reimported.phones) == 2, (
            f"Expected 2 phones, got {len(reimported.phones)}"
        )
        assert len(reimported.emails) == 1

        for field in reimported.fields:
            assert len(field.field_value) < 300, (
                f"Field {field.field_type} corrupted: {len(field.field_value)} chars"
            )
    finally:
        tmp_path.unlink()
