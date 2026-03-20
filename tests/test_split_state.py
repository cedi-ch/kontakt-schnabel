"""Tests for split TUI state serialization (Bug #10 fix)."""

import json

from schnabel.model import Contact, ContactField, Photo
from schnabel.splittui import _serialize_contact, _deserialize_contact, SplitTarget, save_split_state, load_split_state


def test_photo_data_survives_roundtrip():
    """Photo data must survive serialize → deserialize (Bug #10 fix)."""
    photo_data = b"\xff\xd8\xff\xe0" + b"\x00" * 200 + b"\xff\xd9"
    contact = Contact(
        fn="Photo Test",
        family_name="Test",
        given_name="Photo",
    )
    contact.photos.append(Photo(
        photo_data=photo_data,
        photo_format="JPEG",
        byte_hash="abc123",
        width=100,
        height=100,
    ))

    serialized = _serialize_contact(contact)
    restored = _deserialize_contact(serialized)

    assert len(restored.photos) == 1
    assert restored.photos[0].photo_data == photo_data
    assert restored.photos[0].photo_format == "JPEG"
    assert restored.photos[0].byte_hash == "abc123"
    assert restored.photos[0].width == 100


def test_contact_fields_survive_roundtrip():
    """All contact fields must survive serialize → deserialize."""
    contact = Contact(
        fn="Hans Müller",
        family_name="Müller",
        given_name="Hans",
        uid="test-uid-123",
    )
    contact.fields.append(ContactField("email", "hans@test.com", {"TYPE": "HOME"}))
    contact.fields.append(ContactField("tel", "+41791234567", {"TYPE": "CELL"}))
    contact.fields.append(ContactField("categories", "Friends"))

    serialized = _serialize_contact(contact)
    restored = _deserialize_contact(serialized)

    assert restored.fn == "Hans Müller"
    assert restored.family_name == "Müller"
    assert restored.uid == "test-uid-123"
    assert len(restored.fields) == 3
    assert restored.emails == ["hans@test.com"]
    assert restored.phones == ["+41791234567"]


def test_legacy_format_without_photo_data():
    """Old state files without photo data should still load (degraded)."""
    legacy_data = {
        "fn": "Legacy Contact",
        "family_name": "Contact",
        "given_name": "Legacy",
        "fields": [],
        "photos_meta": [
            {"photo_format": "JPEG", "width": 200, "height": 200},
        ],
    }

    restored = _deserialize_contact(legacy_data)
    assert restored.fn == "Legacy Contact"
    assert len(restored.photos) == 1
    assert restored.photos[0].photo_data == b""  # no data, but photo exists
    assert restored.photos[0].photo_format == "JPEG"


def test_full_state_save_load(tmp_path):
    """Full state save → load roundtrip including photos."""
    photo_data = b"\xff\xd8\xff\xe0" + b"\x42" * 500
    c1 = Contact(fn="Alice", family_name="Smith", given_name="Alice")
    c1.fields.append(ContactField("email", "alice@test.com"))
    c1.photos.append(Photo(photo_data=photo_data, photo_format="JPEG", width=50, height=50))

    c2 = Contact(fn="Bob", family_name="Jones", given_name="Bob")
    c2.fields.append(ContactField("tel", "+41791234567"))

    contacts = [c1, c2]
    targets = [SplitTarget(name="file1", key="1")]
    assignments = {0: 0}
    deleted = {1}

    state_path = tmp_path / "state.json"
    save_split_state(contacts, targets, assignments, deleted, "input.vcf", state_path)

    loaded = load_split_state(state_path)
    assert loaded is not None
    assert len(loaded["contacts"]) == 2
    assert loaded["contacts"][0].fn == "Alice"
    assert loaded["contacts"][0].photos[0].photo_data == photo_data
    assert loaded["assignments"] == {0: 0}
    assert loaded["deleted"] == {1}
