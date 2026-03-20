"""Tests for merge engine."""

from schnabel.model import Contact, ContactField, Photo
from schnabel.merge import determine_survivor, merge_contacts, undo_merge


def test_determine_survivor_richer(tmp_db):
    c1 = Contact(fn="Hans", family_name="Mueller", given_name="Hans", category="real")
    c1.fields.append(ContactField("email", "hans@gmail.com"))

    c2 = Contact(fn="Hans Mueller", family_name="Mueller", given_name="Hans", category="real")
    c2.fields.append(ContactField("email", "hans@gmail.com"))
    c2.fields.append(ContactField("tel", "079 123 45 67"))

    id1 = tmp_db.insert_contact(c1)
    id2 = tmp_db.insert_contact(c2)
    tmp_db.commit()

    survivor, absorbed = determine_survivor(tmp_db, id1, id2)
    assert survivor == id2  # c2 has more fields


def test_merge_adds_missing_fields(tmp_db):
    c1 = Contact(fn="Hans", family_name="Mueller", given_name="Hans", category="real")
    c1.fields.append(ContactField("email", "hans@gmail.com"))

    c2 = Contact(fn="Hans Mueller", family_name="Mueller", given_name="Hans", category="real")
    c2.fields.append(ContactField("email", "hans@gmail.com"))
    c2.fields.append(ContactField("tel", "079 123 45 67"))

    id1 = tmp_db.insert_contact(c1)
    id2 = tmp_db.insert_contact(c2)
    tmp_db.commit()

    merge_id = merge_contacts(tmp_db, survivor_id=id2, absorbed_id=id1)

    # Survivor should still be active
    survivor = tmp_db.get_contact(id2)
    assert survivor.is_active is True

    # Absorbed should be inactive
    absorbed = tmp_db.get_contact(id1)
    assert absorbed.is_active is False


def test_undo_merge(tmp_db):
    c1 = Contact(fn="A", category="real")
    c2 = Contact(fn="B", category="real")
    id1 = tmp_db.insert_contact(c1)
    id2 = tmp_db.insert_contact(c2)
    tmp_db.commit()

    merge_id = merge_contacts(tmp_db, id1, id2)
    assert tmp_db.get_contact(id2).is_active is False

    result = undo_merge(tmp_db, merge_id)
    assert result is True
    assert tmp_db.get_contact(id2).is_active is True


# --- Bug #4: Undo removes merged fields ---

def test_undo_removes_merged_fields(tmp_db):
    """Undo must remove fields that were added to the survivor during merge."""
    c1 = Contact(fn="Hans Mueller", family_name="Mueller", given_name="Hans", category="real")
    c1.fields.append(ContactField("email", "hans@gmail.com"))
    c1.fields.append(ContactField("tel", "+41791234567"))

    c2 = Contact(fn="Hans Mueller", family_name="Mueller", given_name="Hans", category="real")
    c2.fields.append(ContactField("email", "hans@work.ch"))
    c2.fields.append(ContactField("tel", "+41761112233"))

    id1 = tmp_db.insert_contact(c1)
    id2 = tmp_db.insert_contact(c2)
    tmp_db.commit()

    # Merge c2 into c1 (c1 is survivor)
    merge_id = merge_contacts(tmp_db, survivor_id=id1, absorbed_id=id2)

    # Verify fields were added
    survivor = tmp_db.get_contact(id1)
    assert len(survivor.emails) == 2
    assert len(survivor.phones) == 2

    # Undo
    undo_merge(tmp_db, merge_id)

    # Survivor should have original fields only
    survivor_after = tmp_db.get_contact(id1)
    assert len(survivor_after.emails) == 1
    assert "hans@gmail.com" in survivor_after.emails
    assert "hans@work.ch" not in survivor_after.emails
    assert len(survivor_after.phones) == 1


def test_undo_full_restore(tmp_db):
    """After undo, both contacts should be active with their original data."""
    c1 = Contact(fn="Alice", family_name="Smith", given_name="Alice", category="real")
    c1.fields.append(ContactField("email", "alice@example.com"))

    c2 = Contact(fn="Alice S", family_name="Smith", given_name="Alice", category="real")
    c2.fields.append(ContactField("email", "alice@work.com"))
    c2.fields.append(ContactField("org", "ACME Corp"))

    id1 = tmp_db.insert_contact(c1)
    id2 = tmp_db.insert_contact(c2)
    tmp_db.commit()

    merge_id = merge_contacts(tmp_db, survivor_id=id1, absorbed_id=id2)

    # Undo
    undo_merge(tmp_db, merge_id)

    # Both should be active
    c1_after = tmp_db.get_contact(id1)
    c2_after = tmp_db.get_contact(id2)
    assert c1_after.is_active is True
    assert c2_after.is_active is True

    # Survivor should have only original email
    assert len(c1_after.emails) == 1
    assert "alice@example.com" in c1_after.emails

    # Absorbed should still have its fields
    assert "alice@work.com" in c2_after.emails


# --- Bug #13: Phone comparison normalized ---

def test_merge_union_phones_normalized(tmp_db):
    """Phone dedup during merge should use E.164 normalization."""
    c1 = Contact(fn="Hans Mueller", family_name="Mueller", given_name="Hans", category="real")
    c1.fields.append(ContactField("tel", "079 123 45 67"))

    c2 = Contact(fn="Hans Mueller", family_name="Mueller", given_name="Hans", category="real")
    c2.fields.append(ContactField("tel", "+41791234567"))  # same number, different format

    id1 = tmp_db.insert_contact(c1)
    id2 = tmp_db.insert_contact(c2)
    tmp_db.commit()

    merge_contacts(tmp_db, survivor_id=id1, absorbed_id=id2)

    survivor = tmp_db.get_contact(id1)
    # Should NOT have both — they're the same number
    assert len(survivor.phones) == 1


# --- Bug #17: Photos without byte_hash ---

def test_merge_union_photos_no_hash(tmp_db):
    """Photos without byte_hash must still be copied during merge."""
    c1 = Contact(fn="Photo Test", family_name="Test", given_name="Photo", category="real")

    c2 = Contact(fn="Photo Test", family_name="Test", given_name="Photo", category="real")
    c2.photos.append(Photo(
        photo_data=b"\xff\xd8\xff\xe0" + b"\x00" * 200,
        photo_format="JPEG",
        byte_hash="",  # no hash!
    ))

    id1 = tmp_db.insert_contact(c1)
    id2 = tmp_db.insert_contact(c2)
    tmp_db.commit()

    merge_contacts(tmp_db, survivor_id=id1, absorbed_id=id2)

    survivor = tmp_db.get_contact(id1)
    assert len(survivor.photos) == 1  # photo was copied despite empty hash


# --- Union tests ---

def test_merge_union_emails(tmp_db):
    """Merge should combine emails from both contacts without duplicates."""
    c1 = Contact(fn="Test", family_name="User", given_name="Test", category="real")
    c1.fields.append(ContactField("email", "test@gmail.com"))
    c1.fields.append(ContactField("email", "test@work.ch"))

    c2 = Contact(fn="Test", family_name="User", given_name="Test", category="real")
    c2.fields.append(ContactField("email", "test@gmail.com"))  # duplicate
    c2.fields.append(ContactField("email", "test@private.ch"))  # new

    id1 = tmp_db.insert_contact(c1)
    id2 = tmp_db.insert_contact(c2)
    tmp_db.commit()

    merge_contacts(tmp_db, survivor_id=id1, absorbed_id=id2)

    survivor = tmp_db.get_contact(id1)
    assert len(survivor.emails) == 3
    assert "test@gmail.com" in survivor.emails
    assert "test@work.ch" in survivor.emails
    assert "test@private.ch" in survivor.emails


def test_merge_union_categories(tmp_db):
    """Merge should combine CATEGORIES from both contacts."""
    c1 = Contact(fn="Cat Test", family_name="Test", given_name="Cat", category="real")
    c1.fields.append(ContactField("categories", "Friends"))

    c2 = Contact(fn="Cat Test", family_name="Test", given_name="Cat", category="real")
    c2.fields.append(ContactField("categories", "Work"))
    c2.fields.append(ContactField("categories", "Friends"))  # duplicate

    id1 = tmp_db.insert_contact(c1)
    id2 = tmp_db.insert_contact(c2)
    tmp_db.commit()

    merge_contacts(tmp_db, survivor_id=id1, absorbed_id=id2)

    survivor = tmp_db.get_contact(id1)
    cats = survivor.categories
    assert "Friends" in cats
    assert "Work" in cats
    # "Friends" duplicate should not be added twice
    assert cats.count("Friends") == 1


def test_merge_name_adoption(tmp_db):
    """Merge should adopt structured name from absorbed if survivor has none."""
    c1 = Contact(fn="hans@gmail.com", category="real")
    c1.fields.append(ContactField("email", "hans@gmail.com"))

    c2 = Contact(fn="Hans Mueller", family_name="Mueller", given_name="Hans", category="real")
    c2.fields.append(ContactField("email", "hans@gmail.com"))

    id1 = tmp_db.insert_contact(c1)
    id2 = tmp_db.insert_contact(c2)
    tmp_db.commit()

    merge_contacts(tmp_db, survivor_id=id1, absorbed_id=id2)

    survivor = tmp_db.get_contact(id1)
    assert survivor.family_name == "Mueller"
    assert survivor.given_name == "Hans"


# --- Bug #5: Pairs reassigned ---

def test_pairs_reassigned_not_deleted(tmp_db):
    """Pairs involving absorbed contact should be reassigned to survivor, not deleted."""
    c1 = Contact(fn="A", category="real")
    c2 = Contact(fn="B", category="real")
    c3 = Contact(fn="C", category="real")

    id1 = tmp_db.insert_contact(c1)
    id2 = tmp_db.insert_contact(c2)
    id3 = tmp_db.insert_contact(c3)
    tmp_db.commit()

    # Create pairs: (A,B) and (B,C)
    tmp_db.insert_similarity_pair(id1, id2, 0.9, 0.9, 0.0, 0.0, 0.0, 0.0)
    tmp_db.insert_similarity_pair(id2, id3, 0.8, 0.8, 0.0, 0.0, 0.0, 0.0)
    tmp_db.commit()

    # Merge B into A
    merge_contacts(tmp_db, survivor_id=id1, absorbed_id=id2)

    # The pair (B,C) should now be (A,C) — not deleted
    pending = tmp_db.get_pending_pairs()
    # We should have a pair involving A and C
    pair_contacts = set()
    for p in pending:
        pair_contacts.add(p["contact_a_id"])
        pair_contacts.add(p["contact_b_id"])

    assert id3 in pair_contacts, "Pair with C should still exist"
    assert id1 in pair_contacts, "Pair should now reference survivor A"
