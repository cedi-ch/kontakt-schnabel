"""Tests for the raw text parser."""

import pytest

from schnabel.rawparse import (
    ParsedContact,
    ParsedField,
    _extract_fields,
    _split_into_blocks,
    _split_name,
    parse_raw_text,
    parsed_to_contact,
)


# ── Block splitting ────────────────────────────────────────────────────────


class TestSplitIntoBlocks:
    def test_blank_line_separated(self):
        text = "Hans Müller\nhans@example.com\n\nAnna Schmidt\nanna@test.ch"
        blocks = _split_into_blocks(text)
        assert len(blocks) == 2
        assert "Hans" in blocks[0]
        assert "Anna" in blocks[1]

    def test_single_block(self):
        text = "Hans Müller\nhans@example.com\n079 123 45 67"
        blocks = _split_into_blocks(text)
        assert len(blocks) == 1

    def test_single_line_per_contact(self):
        """When most lines have email/phone, each line = one contact."""
        text = "hans@a.com 079 111 22 33\nanna@b.com 079 444 55 66\nbob@c.com 079 777 88 99"
        blocks = _split_into_blocks(text)
        assert len(blocks) == 3

    def test_empty_text(self):
        blocks = _split_into_blocks("")
        assert blocks == []

    def test_only_whitespace(self):
        blocks = _split_into_blocks("   \n\n   \n  ")
        assert blocks == []

    def test_crlf_handling(self):
        text = "Hans Müller\r\nhans@example.com\r\n\r\nAnna Schmidt\r\nanna@test.ch"
        blocks = _split_into_blocks(text)
        assert len(blocks) == 2


# ── Name splitting ─────────────────────────────────────────────────────────


class TestSplitName:
    def test_two_words(self):
        result = _split_name("Hans Müller")
        assert result["given"] == "Hans"
        assert result["family"] == "Müller"
        assert result["prefix"] == ""

    def test_comma_format(self):
        result = _split_name("Müller, Hans")
        assert result["given"] == "Hans"
        assert result["family"] == "Müller"

    def test_prefix(self):
        result = _split_name("Dr. Hans Peter Müller")
        assert result["prefix"] == "Dr."
        assert result["given"] == "Hans Peter"
        assert result["family"] == "Müller"

    def test_single_word(self):
        result = _split_name("Müller")
        assert result["family"] == "Müller"
        assert result["given"] == ""

    def test_empty(self):
        result = _split_name("")
        assert result["given"] == ""
        assert result["family"] == ""


# ── Field extraction ───────────────────────────────────────────────────────


class TestExtractFields:
    def test_email_extraction(self):
        fields = _extract_fields("hans@example.com")
        emails = [f for f in fields if f.field_type == "email"]
        assert len(emails) == 1
        assert emails[0].value == "hans@example.com"
        assert emails[0].confidence == "high"

    def test_phone_extraction(self):
        fields = _extract_fields("079 123 45 67")
        phones = [f for f in fields if f.field_type == "tel"]
        assert len(phones) == 1
        assert phones[0].value == "+41791234567"
        assert phones[0].confidence == "high"

    def test_international_phone(self):
        fields = _extract_fields("+41 44 123 45 67")
        phones = [f for f in fields if f.field_type == "tel"]
        assert len(phones) == 1
        assert phones[0].value == "+41441234567"

    def test_url_extraction(self):
        fields = _extract_fields("https://example.com/page")
        urls = [f for f in fields if f.field_type == "url"]
        assert len(urls) == 1
        assert urls[0].value == "https://example.com/page"

    def test_org_extraction(self):
        fields = _extract_fields("Müller AG")
        orgs = [f for f in fields if f.field_type == "org"]
        assert len(orgs) == 1
        assert "Müller AG" in orgs[0].value

    def test_org_gmbh(self):
        fields = _extract_fields("Beispiel GmbH")
        orgs = [f for f in fields if f.field_type == "org"]
        assert len(orgs) == 1

    def test_swiss_address_plz(self):
        fields = _extract_fields("8001 Zürich")
        addrs = [f for f in fields if f.field_type == "adr"]
        assert len(addrs) == 1
        assert "8001" in addrs[0].value
        assert "Zürich" in addrs[0].value

    def test_street_with_plz(self):
        fields = _extract_fields("Bahnhofstrasse 12, 8001 Zürich")
        addrs = [f for f in fields if f.field_type == "adr"]
        assert len(addrs) == 1
        assert "Bahnhofstrasse 12" in addrs[0].value
        assert "8001" in addrs[0].value

    def test_name_as_remainder(self):
        fields = _extract_fields("Hans Müller hans@example.com")
        names = [f for f in fields if f.field_type == "fn"]
        assert len(names) == 1
        assert "Hans" in names[0].value
        assert "Müller" in names[0].value

    def test_birthday_with_year(self):
        fields = _extract_fields("Geburtstag 20.01.26")
        bdays = [f for f in fields if f.field_type == "bday"]
        assert len(bdays) == 1
        assert bdays[0].value == "2026-01-20"

    def test_birthday_old_year(self):
        fields = _extract_fields("Geburtstag 09.05.83")
        bdays = [f for f in fields if f.field_type == "bday"]
        assert len(bdays) == 1
        assert bdays[0].value == "1983-05-09"

    def test_birthday_no_year(self):
        fields = _extract_fields("geburi 03.12")
        bdays = [f for f in fields if f.field_type == "bday"]
        assert len(bdays) == 1
        assert bdays[0].value == "--12-03"

    def test_birthday_stripped_from_name(self):
        fields = _extract_fields("Numa Aurelio Saputelli Geburtstag 20.01.26")
        names = [f for f in fields if f.field_type == "fn"]
        bdays = [f for f in fields if f.field_type == "bday"]
        assert len(bdays) == 1
        assert len(names) == 1
        assert "Geburtstag" not in names[0].value

    def test_phone_labels_stripped(self):
        fields = _extract_fields("Patrick Wasem\nMobil: +41 76 502 21 34\nArbeit: +4152 632 71 38")
        names = [f for f in fields if f.field_type == "fn"]
        phones = [f for f in fields if f.field_type == "tel"]
        assert len(phones) == 2
        assert len(names) == 1
        assert "Mobil" not in names[0].value
        assert "Arbeit" not in names[0].value

    def test_phone_label_tel(self):
        fields = _extract_fields("Andrea baumann GR\nTel: 081 257 38 96")
        names = [f for f in fields if f.field_type == "fn"]
        assert len(names) == 1
        assert "Tel" not in names[0].value


# ── Full parsing ───────────────────────────────────────────────────────────


class TestParseRawText:
    def test_single_line_contact(self):
        text = "Hans Müller hans@example.com 079 123 45 67"
        contacts = parse_raw_text(text)
        assert len(contacts) == 1
        c = contacts[0]
        types = {f.field_type for f in c.fields}
        assert "email" in types
        assert "tel" in types

    def test_multiline_block_with_address(self):
        text = """Anna Schmidt
anna.schmidt@gmail.com
+41 44 123 45 67
Musterstrasse 5, 3000 Bern"""
        contacts = parse_raw_text(text)
        assert len(contacts) == 1
        c = contacts[0]
        types = {f.field_type for f in c.fields}
        assert "email" in types
        assert "tel" in types
        assert "adr" in types

    def test_email_only(self):
        text = "test@example.com"
        contacts = parse_raw_text(text)
        assert len(contacts) == 1
        assert contacts[0].fields[0].field_type == "email"

    def test_phone_only(self):
        text = "079 987 65 43"
        contacts = parse_raw_text(text)
        assert len(contacts) == 1
        assert contacts[0].fields[0].field_type == "tel"

    def test_org_with_details(self):
        text = "Müller AG, info@mueller-ag.ch, 044 987 65 43"
        contacts = parse_raw_text(text)
        assert len(contacts) == 1
        c = contacts[0]
        types = {f.field_type for f in c.fields}
        assert "org" in types
        assert "email" in types

    def test_multiple_contacts_blank_separated(self):
        text = """Hans Müller, Bahnhofstr. 12, 8001 Zürich, 079 123 45 67, hans@example.com

Anna Schmidt
anna.schmidt@gmail.com
+41 44 123 45 67
Musterstrasse 5, 3000 Bern

Müller AG, info@mueller-ag.ch, 044 987 65 43"""
        contacts = parse_raw_text(text)
        assert len(contacts) == 3

    def test_empty_input(self):
        contacts = parse_raw_text("")
        assert contacts == []

    def test_garbage_text(self):
        """Lines with no recognizable fields → no contacts."""
        text = "--- --- ---\n=== ===\n!!!"
        contacts = parse_raw_text(text)
        assert contacts == []

    def test_mixed_content(self):
        """Some lines are contacts, some are garbage."""
        text = "hans@example.com 079 123 45 67\n\n--- separator ---\n\nanna@test.ch"
        contacts = parse_raw_text(text)
        # Should produce at least the email/phone contacts
        emails = []
        for c in contacts:
            for f in c.fields:
                if f.field_type == "email":
                    emails.append(f.value)
        assert "hans@example.com" in emails
        assert "anna@test.ch" in emails

    def test_nachname_vorname_format(self):
        text = "Müller, Hans hans@test.ch"
        contacts = parse_raw_text(text)
        assert len(contacts) == 1
        names = [f for f in contacts[0].fields if f.field_type == "fn"]
        assert len(names) == 1
        assert "Müller" in names[0].value
        assert "Hans" in names[0].value


# ── Conversion to Contact model ───────────────────────────────────────────


class TestParsedToContact:
    def test_basic_conversion(self):
        parsed = ParsedContact(
            raw_text="Hans Müller hans@example.com",
            fields=[
                ParsedField("fn", "Hans Müller", "high", "Hans Müller"),
                ParsedField("email", "hans@example.com", "high", "hans@example.com"),
                ParsedField("tel", "+41791234567", "high", "079 123 45 67"),
            ],
        )
        contact = parsed_to_contact(parsed)
        assert contact.given_name == "Hans"
        assert contact.family_name == "Müller"
        assert contact.fn == "Hans Müller"
        assert len(contact.emails) == 1
        assert contact.emails[0] == "hans@example.com"
        assert len(contact.phones) == 1

    def test_comma_name_conversion(self):
        parsed = ParsedContact(
            raw_text="Müller, Hans",
            fields=[
                ParsedField("fn", "Müller, Hans", "high", "Müller, Hans"),
            ],
        )
        contact = parsed_to_contact(parsed)
        assert contact.given_name == "Hans"
        assert contact.family_name == "Müller"

    def test_no_name(self):
        parsed = ParsedContact(
            raw_text="test@example.com",
            fields=[
                ParsedField("email", "test@example.com", "high", "test@example.com"),
            ],
        )
        contact = parsed_to_contact(parsed)
        assert contact.fn == ""
        assert len(contact.emails) == 1

    def test_org_field(self):
        parsed = ParsedContact(
            raw_text="Müller AG",
            fields=[
                ParsedField("org", "Müller AG", "medium", "Müller AG"),
            ],
        )
        contact = parsed_to_contact(parsed)
        assert len(contact.orgs) == 1
        assert contact.orgs[0] == "Müller AG"
