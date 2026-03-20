"""Additional tests to increase test coverage for match, db, classify, export modules."""

from schnabel.model import Contact, ContactField, Photo
from schnabel.match import score_pair, _token_sort_ratio, _hamming_distance
from schnabel.classify import classify_contact, is_spam_email
from schnabel.export import contact_to_vcard, _fold_line, _escape_vcard_value


# --- match.py coverage ---

def _insert(db, contacts):
    ids = []
    for c in contacts:
        ids.append(db.insert_contact(c))
    db.commit()
    return ids


def test_score_domain_match(tmp_db):
    """Non-generic shared domain should give partial email score."""
    c1 = Contact(fn="Alice", family_name="Smith", given_name="Alice", category="real")
    c1.fields.append(ContactField("email", "alice@mycompany.ch"))

    c2 = Contact(fn="Alice", family_name="Smith", given_name="Alice", category="real")
    c2.fields.append(ContactField("email", "a.smith@mycompany.ch"))

    ids = _insert(tmp_db, [c1, c2])
    scores = score_pair(tmp_db, ids[0], ids[1])
    assert scores["email_score"] == 0.3  # shared non-generic domain


def test_score_generic_domain_no_match(tmp_db):
    """Generic domain (gmail) should NOT give domain match score."""
    c1 = Contact(fn="Alice", family_name="Smith", given_name="Alice", category="real")
    c1.fields.append(ContactField("email", "alice@gmail.com"))

    c2 = Contact(fn="Bob", family_name="Jones", given_name="Bob", category="real")
    c2.fields.append(ContactField("email", "bob@gmail.com"))

    ids = _insert(tmp_db, [c1, c2])
    scores = score_pair(tmp_db, ids[0], ids[1])
    assert scores["email_score"] == 0.0


def test_score_last7_phone_match(tmp_db):
    """Last-7-digits phone matching should give 0.7 score."""
    c1 = Contact(fn="Test", family_name="User", given_name="Test", category="real")
    c1.fields.append(ContactField("tel", "1234567"))  # raw digits

    c2 = Contact(fn="Test", family_name="User", given_name="Test", category="real")
    c2.fields.append(ContactField("tel", "0001234567"))

    ids = _insert(tmp_db, [c1, c2])
    scores = score_pair(tmp_db, ids[0], ids[1])
    assert scores["phone_score"] == 0.7


def test_score_no_contacts(tmp_db):
    """Scoring non-existent contacts should return 0."""
    scores = score_pair(tmp_db, 999, 998)
    assert scores["confidence"] == 0.0


def test_score_shared_email_and_name(tmp_db):
    """Shared email + high name score should hit anchor rule."""
    c1 = Contact(fn="Hans Mueller", family_name="Mueller", given_name="Hans", category="real")
    c1.fields.append(ContactField("email", "hans.mueller@company.ch"))

    c2 = Contact(fn="Hans Müller", family_name="Müller", given_name="Hans", category="real")
    c2.fields.append(ContactField("email", "hans.mueller@company.ch"))

    ids = _insert(tmp_db, [c1, c2])
    scores = score_pair(tmp_db, ids[0], ids[1])
    assert scores["confidence"] >= 0.85  # shared email + name → anchor


def test_score_near_identical_names(tmp_db):
    """Near-identical names (>0.98) should get 0.80 confidence."""
    c1 = Contact(fn="Hans Mueller", family_name="Mueller", given_name="Hans", category="real")
    c2 = Contact(fn="Hans Mueller", family_name="Mueller", given_name="Hans", category="real")

    ids = _insert(tmp_db, [c1, c2])
    scores = score_pair(tmp_db, ids[0], ids[1])
    assert scores["confidence"] >= 0.80  # near-identical names anchor


def test_token_sort_ratio():
    assert _token_sort_ratio("hans mueller", "mueller hans") > 0.95
    assert _token_sort_ratio("alice smith", "bob jones") < 0.7
    assert _token_sort_ratio("", "test") == 0.0


def test_hamming_distance():
    assert _hamming_distance("ff", "ff") == 0
    assert _hamming_distance("ff", "00") == 8
    assert _hamming_distance("0f", "f0") == 8


# --- db.py coverage ---

def test_uid_roundtrip(tmp_db):
    """UID should survive insert → get."""
    c = Contact(fn="UID Test", family_name="Test", given_name="UID",
                category="real", uid="test-uuid-abc")
    cid = tmp_db.insert_contact(c)
    tmp_db.commit()

    loaded = tmp_db.get_contact(cid)
    assert loaded.uid == "test-uuid-abc"


def test_get_nonexistent_contact(tmp_db):
    """Getting non-existent contact should return None."""
    assert tmp_db.get_contact(9999) is None


def test_metadata(tmp_db):
    """Metadata set/get should work."""
    tmp_db.set_metadata("test_key", "test_value")
    assert tmp_db.get_metadata("test_key") == "test_value"
    assert tmp_db.get_metadata("nonexistent") is None


def test_metadata_upsert(tmp_db):
    """Metadata should update on conflict."""
    tmp_db.set_metadata("key", "value1")
    tmp_db.set_metadata("key", "value2")
    assert tmp_db.get_metadata("key") == "value2"


def test_contact_field_operations(tmp_db):
    """Add, update, delete contact fields."""
    c = Contact(fn="Field Test", category="real")
    cid = tmp_db.insert_contact(c)
    tmp_db.commit()

    # Add field
    from schnabel.model import ContactField
    tmp_db.add_contact_field(cid, ContactField("email", "test@test.com"))
    tmp_db.commit()

    contact = tmp_db.get_contact(cid)
    assert len(contact.emails) == 1

    # Update field
    fid = contact.fields[0].id
    tmp_db.update_contact_field(fid, "new@test.com")
    tmp_db.commit()

    contact = tmp_db.get_contact(cid)
    assert contact.emails[0] == "new@test.com"

    # Delete field
    tmp_db.delete_contact_field(fid)
    tmp_db.commit()

    contact = tmp_db.get_contact(cid)
    assert len(contact.emails) == 0


def test_photo_insert_and_retrieve(tmp_db):
    """Photos should survive insert → get."""
    c = Contact(fn="Photo DB", category="real")
    c.photos.append(Photo(
        photo_data=b"\xff\xd8\xff\xe0" + b"\x00" * 100,
        photo_format="JPEG",
        byte_hash="testhash",
        width=200,
        height=150,
    ))
    cid = tmp_db.insert_contact(c)
    tmp_db.commit()

    loaded = tmp_db.get_contact(cid)
    assert len(loaded.photos) == 1
    assert loaded.photos[0].photo_format == "JPEG"
    assert loaded.photos[0].byte_hash == "testhash"
    assert loaded.photos[0].width == 200


def test_session_stats(tmp_db):
    """Session stats should return correct counts."""
    stats = tmp_db.get_session_stats()
    assert stats["imports"] == 0
    assert stats["auto_merges"] == 0


def test_delete_contact(tmp_db):
    """delete_contact should set category=deleted and is_active=0."""
    c = Contact(fn="Delete Me", category="real")
    cid = tmp_db.insert_contact(c)
    tmp_db.commit()

    tmp_db.delete_contact(cid)
    tmp_db.commit()

    loaded = tmp_db.get_contact(cid)
    assert loaded.category == "deleted"
    assert loaded.is_active is False


# --- classify.py 100% coverage ---

def test_classify_phone_no_name_real():
    """Contact with phone but no name should still be real."""
    c = Contact(fn="")
    c.fields.append(ContactField("tel", "+41791234567"))
    assert classify_contact(c) == "real"


def test_classify_photo_only_real():
    """Contact with only a photo should be real."""
    c = Contact(fn="Photo Only", family_name="Only", given_name="Photo")
    c.photos.append(Photo(photo_data=b"\xff" * 200, photo_format="JPEG"))
    assert classify_contact(c) == "real"


def test_classify_address_only_real():
    """Contact with name + address should be real."""
    c = Contact(fn="Addr Only", family_name="Only", given_name="Addr")
    c.fields.append(ContactField("adr", "Street;City;;1234;CH"))
    assert classify_contact(c) == "real"


def test_spam_from_domain():
    """Email from a known spam domain should be spam."""
    assert is_spam_email("anything@noreply.com")
    assert is_spam_email("user@example.com")


# --- export.py coverage ---

def test_escape_vcard_value():
    assert _escape_vcard_value("hello") == "hello"
    assert _escape_vcard_value("a;b") == "a\\;b"
    assert _escape_vcard_value("a,b") == "a\\,b"
    assert _escape_vcard_value("a\\b") == "a\\\\b"
    assert _escape_vcard_value("a\nb") == "a\\nb"


def test_fold_line_multibyte():
    """Folding should respect UTF-8 byte length, not char count."""
    # 75 bytes is the limit. Each ü is 2 bytes in UTF-8
    line = "NOTE:" + "ü" * 40  # 5 + 80 = 85 bytes
    folded = _fold_line(line)
    assert "\r\n " in folded


def test_export_multiple_emails():
    """Multiple emails with TYPE params should all appear."""
    c = Contact(fn="Multi", family_name="Email", given_name="Multi")
    c.fields.append(ContactField("email", "home@test.com", {"TYPE": "HOME"}))
    c.fields.append(ContactField("email", "work@test.com", {"TYPE": "WORK"}))
    vcard = contact_to_vcard(c)
    assert "EMAIL;TYPE=HOME:home@test.com" in vcard
    assert "EMAIL;TYPE=WORK:work@test.com" in vcard


def test_export_multiple_type_params():
    """TYPE with list of values should be serialized correctly."""
    c = Contact(fn="Type Test", family_name="Test", given_name="Type")
    c.fields.append(ContactField("tel", "+41791234567", {"TYPE": ["CELL", "VOICE"]}))
    vcard = contact_to_vcard(c)
    assert "TYPE=CELL" in vcard
    assert "TYPE=VOICE" in vcard


def test_export_org_text_escaped():
    """ORG is a free-text field — only backslash and newline are escaped."""
    c = Contact(fn="Org Test", family_name="Test", given_name="Org")
    c.fields.append(ContactField("org", "ACME; Inc."))
    vcard = contact_to_vcard(c)
    # Semicolons in ORG are NOT escaped (free-text field per RFC 2426)
    assert "ORG:ACME; Inc." in vcard


def test_export_note_escaped():
    """NOTE with newlines should be escaped."""
    c = Contact(fn="Note Test", family_name="Test", given_name="Note")
    c.fields.append(ContactField("note", "Line1\nLine2"))
    vcard = contact_to_vcard(c)
    assert "NOTE:Line1\\nLine2" in vcard


def test_export_url_not_escaped():
    """URLs should NOT be escaped (they contain valid colons etc)."""
    c = Contact(fn="URL Test", family_name="Test", given_name="URL")
    c.fields.append(ContactField("url", "https://example.com"))
    vcard = contact_to_vcard(c)
    assert "URL:https://example.com" in vcard


def test_export_rev_present():
    """REV timestamp should be present in output."""
    c = Contact(fn="Rev Test", family_name="Test", given_name="Rev")
    vcard = contact_to_vcard(c)
    assert "REV:" in vcard


def test_export_crlf_everywhere():
    """Every line in the export should end with CRLF."""
    c = Contact(fn="CRLF Test", family_name="Test", given_name="CRLF")
    c.fields.append(ContactField("email", "test@test.com"))
    vcard = contact_to_vcard(c)
    # Split by CRLF should give us all lines
    lines = vcard.split("\r\n")
    assert len(lines) > 3  # at least BEGIN, VERSION, FN, N, UID, EMAIL, REV, END
    # No bare LF should exist
    assert "\n" not in vcard.replace("\r\n", "")
