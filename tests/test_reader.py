"""Tests for vCard parser."""

from schnabel.reader import parse_vcard, parse_vcf_file, split_vcards


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
