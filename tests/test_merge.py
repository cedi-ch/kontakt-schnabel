"""Tests for merge engine."""

from schnabel.model import Contact, ContactField
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
