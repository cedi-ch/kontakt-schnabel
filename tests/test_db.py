"""Tests for database layer."""

from schnabel.model import Contact, ContactField, Photo


def test_insert_and_get_contact(tmp_db):
    c = Contact(
        fn="Hans Mueller",
        family_name="Mueller",
        given_name="Hans",
        category="real",
    )
    c.fields.append(ContactField("email", "hans@test.com"))
    c.fields.append(ContactField("tel", "079 123 45 67"))

    cid = tmp_db.insert_contact(c)
    tmp_db.commit()

    loaded = tmp_db.get_contact(cid)
    assert loaded is not None
    assert loaded.fn == "Hans Mueller"
    assert loaded.family_name == "Mueller"
    assert len(loaded.emails) == 1
    assert len(loaded.phones) == 1


def test_stats(tmp_db):
    c1 = Contact(fn="Real", category="real")
    c2 = Contact(fn="Stub", category="stub")
    tmp_db.insert_contact(c1)
    tmp_db.insert_contact(c2)
    tmp_db.commit()

    stats = tmp_db.get_stats()
    assert stats["total"] == 2
    assert stats["real"] == 1
    assert stats["stub"] == 1


def test_deactivate_and_reactivate(tmp_db):
    c1 = Contact(fn="A", category="real")
    c2 = Contact(fn="B", category="real")
    id1 = tmp_db.insert_contact(c1)
    id2 = tmp_db.insert_contact(c2)
    tmp_db.commit()

    tmp_db.deactivate_contact(id2, id1)
    tmp_db.commit()

    assert tmp_db.get_contact(id2).is_active is False
    assert len(tmp_db.get_active_contact_ids()) == 1

    tmp_db.reactivate_contact(id2)
    tmp_db.commit()
    assert tmp_db.get_contact(id2).is_active is True


def test_import_source_tracking(tmp_db):
    src_id = tmp_db.add_import_source("/tmp/test.vcf", "abc123", "utf-8")
    tmp_db.update_import_count(src_id, 42)
    assert "abc123" in tmp_db.get_imported_hashes()
