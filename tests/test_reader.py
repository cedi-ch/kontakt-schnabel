"""Tests for vCard parser."""

from schnabel.reader import (
    _fallback_parse, _unfold_lines, fix_broken_qp,
    parse_vcard, parse_vcf_file, split_vcards,
)


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
