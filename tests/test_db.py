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


def test_stats_extended_fields(tmp_db):
    """get_stats returns field presence and per-category counts."""
    c = Contact(fn="Full Contact", category="real")
    c.fields.append(ContactField("email", "test@test.com"))
    c.fields.append(ContactField("tel", "+41791234567"))
    c.fields.append(ContactField("bday", "1990-01-15"))
    c.fields.append(ContactField("adr", ";;Street;City;;1234;CH"))
    c.fields.append(ContactField("org", "ACME Corp"))
    c.fields.append(ContactField("note", "Some note"))
    c.fields.append(ContactField("url", "https://example.com"))
    c.fields.append(ContactField("categories", "Friends"))
    tmp_db.insert_contact(c)

    stub = Contact(fn="Stub", category="stub")
    stub.fields.append(ContactField("email", "stub@test.com"))
    tmp_db.insert_contact(stub)
    tmp_db.commit()

    stats = tmp_db.get_stats()
    assert stats["with_bday"] == 1
    assert stats["with_adr"] == 1
    assert stats["with_org"] == 1
    assert stats["with_note"] == 1
    assert stats["with_url"] == 1
    assert stats["real_with_email"] == 1
    assert stats["real_with_tel"] == 1
    assert stats["real_with_bday"] == 1
    assert stats["real_with_adr"] == 1
    assert stats["stub_with_email"] == 1
    assert stats["stub_with_tel"] == 0


def test_pipeline_run_roundtrip(tmp_db):
    """log_pipeline_run and get_pipeline_runs roundtrip."""
    tmp_db.log_pipeline_run("import", {"files": 3, "contacts": 42})
    tmp_db.log_pipeline_run("match", {"total": 10, "high": 5})

    runs = tmp_db.get_pipeline_runs()
    assert "import" in runs
    assert runs["import"]["files"] == 3
    assert runs["import"]["contacts"] == 42
    assert "timestamp" in runs["import"]
    assert "match" in runs
    assert runs["match"]["total"] == 10


def test_delete_category_from_all(tmp_db):
    """delete_category_from_all removes a specific category from all contacts."""
    c1 = Contact(fn="A", category="real")
    c1.fields.append(ContactField("categories", "Friends"))
    c1.fields.append(ContactField("categories", "importation-05-01-2025"))
    tmp_db.insert_contact(c1)

    c2 = Contact(fn="B", category="real")
    c2.fields.append(ContactField("categories", "importation-05-01-2025"))
    tmp_db.insert_contact(c2)

    c3 = Contact(fn="C", category="real")
    c3.fields.append(ContactField("categories", "Work"))
    tmp_db.insert_contact(c3)
    tmp_db.commit()

    deleted = tmp_db.delete_category_from_all("importation-05-01-2025")
    assert deleted == 2

    # Friends and Work should remain
    cats = tmp_db.get_all_category_values()
    assert "Friends" in cats
    assert "Work" in cats
    assert "importation-05-01-2025" not in cats

    # c1 should still have Friends
    c1_loaded = tmp_db.get_contact(1)
    cat_values = [f.field_value for f in c1_loaded.fields if f.field_type == "categories"]
    assert cat_values == ["Friends"]


def test_search_contacts(tmp_db):
    """search_contacts finds by name, email, phone."""
    c1 = Contact(fn="Hans Müller", family_name="Müller", given_name="Hans", category="real")
    c1.fields.append(ContactField("email", "hans@test.com"))
    tmp_db.insert_contact(c1)

    c2 = Contact(fn="Anna Schmidt", family_name="Schmidt", given_name="Anna", category="real")
    c2.fields.append(ContactField("tel", "+41791234567"))
    tmp_db.insert_contact(c2)

    c3 = Contact(fn="Peter Weber", family_name="Weber", given_name="Peter", category="real")
    tmp_db.insert_contact(c3)
    tmp_db.commit()

    # Search by name
    results = tmp_db.search_contacts("müller")
    assert len(results) == 1
    assert results[0].fn == "Hans Müller"

    # Search by email
    results = tmp_db.search_contacts("hans@test")
    assert len(results) == 1

    # Search by phone
    results = tmp_db.search_contacts("1234567")
    assert len(results) == 1
    assert results[0].fn == "Anna Schmidt"

    # No match
    results = tmp_db.search_contacts("zzzzz")
    assert len(results) == 0


def test_category_breakdown(tmp_db):
    """get_category_breakdown counts per CATEGORIES value."""
    c1 = Contact(fn="A", category="real")
    c1.fields.append(ContactField("categories", "Friends"))
    tmp_db.insert_contact(c1)

    c2 = Contact(fn="B", category="real")
    c2.fields.append(ContactField("categories", "Friends"))
    tmp_db.insert_contact(c2)

    c3 = Contact(fn="C", category="real")
    c3.fields.append(ContactField("categories", "Work"))
    tmp_db.insert_contact(c3)
    tmp_db.commit()

    breakdown = tmp_db.get_category_breakdown()
    assert breakdown["Friends"] == 2
    assert breakdown["Work"] == 1
