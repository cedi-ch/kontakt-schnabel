"""Tests for category-based export and export preview."""

from schnabel.model import Contact, ContactField
from schnabel.export import export_by_category, get_export_preview


def test_export_by_category_creates_files(tmp_db, tmp_path):
    """Export by category should create one VCF per category."""
    c1 = Contact(fn="Alice", family_name="Smith", given_name="Alice", category="real")
    c1.fields.append(ContactField("categories", "Friends"))
    c1.fields.append(ContactField("email", "alice@test.com"))

    c2 = Contact(fn="Bob", family_name="Jones", given_name="Bob", category="real")
    c2.fields.append(ContactField("categories", "Work"))
    c2.fields.append(ContactField("email", "bob@test.com"))

    c3 = Contact(fn="Charlie", family_name="Brown", given_name="Charlie", category="real")
    c3.fields.append(ContactField("email", "charlie@test.com"))
    # no category

    tmp_db.insert_contact(c1)
    tmp_db.insert_contact(c2)
    tmp_db.insert_contact(c3)
    tmp_db.commit()

    output_dir = tmp_path / "export"
    counts = export_by_category(tmp_db, output_dir, normalize_photos=False)

    assert "Friends" in counts
    assert "Work" in counts
    assert "unsortiert" in counts
    assert counts["Friends"] == 1
    assert counts["Work"] == 1
    assert counts["unsortiert"] == 1

    # Check files exist
    assert (output_dir / "kontakte-friends.vcf").exists()
    assert (output_dir / "kontakte-work.vcf").exists()
    assert (output_dir / "kontakte-unsortiert.vcf").exists()


def test_export_by_category_still_writes_stubs_spam(tmp_db, tmp_path):
    """Stubs and spam should still get their own files."""
    c1 = Contact(fn="Real", family_name="Person", given_name="Real", category="real")
    c1.fields.append(ContactField("email", "real@test.com"))

    c2 = Contact(fn="stub@test.com", category="stub")
    c2.fields.append(ContactField("email", "stub@test.com"))

    tmp_db.insert_contact(c1)
    tmp_db.insert_contact(c2)
    tmp_db.commit()

    output_dir = tmp_path / "export"
    counts = export_by_category(tmp_db, output_dir, normalize_photos=False)

    assert (output_dir / "stubs.vcf").exists()
    assert (output_dir / "spam.vcf").exists()


def test_export_by_category_multi_category_contact(tmp_db, tmp_path):
    """A contact with multiple categories should appear in each category file."""
    c = Contact(fn="Multi Cat", family_name="Cat", given_name="Multi", category="real")
    c.fields.append(ContactField("categories", "Friends"))
    c.fields.append(ContactField("categories", "Family"))
    c.fields.append(ContactField("email", "multi@test.com"))

    tmp_db.insert_contact(c)
    tmp_db.commit()

    output_dir = tmp_path / "export"
    counts = export_by_category(tmp_db, output_dir, normalize_photos=False)

    assert counts["Friends"] == 1
    assert counts["Family"] == 1

    # Contact appears in both files
    friends = (output_dir / "kontakte-friends.vcf").read_text()
    family = (output_dir / "kontakte-family.vcf").read_text()
    assert "Multi Cat" in friends
    assert "Multi Cat" in family


# --- Export preview ---

def test_export_preview(tmp_db):
    """Preview should return correct counts."""
    c1 = Contact(fn="Alice", family_name="Smith", given_name="Alice", category="real")
    c1.fields.append(ContactField("categories", "Friends"))

    c2 = Contact(fn="Bob", family_name="Jones", given_name="Bob", category="real")
    # no category

    c3 = Contact(fn="stub@test.com", category="stub")
    c3.fields.append(ContactField("email", "stub@test.com"))

    c4 = Contact(fn="spam@test.com", category="spam")
    c4.fields.append(ContactField("email", "spam@test.com"))

    tmp_db.insert_contact(c1)
    tmp_db.insert_contact(c2)
    tmp_db.insert_contact(c3)
    tmp_db.insert_contact(c4)
    tmp_db.commit()

    preview = get_export_preview(tmp_db)

    assert preview["real"] == 2
    assert preview["stubs"] == 1
    assert preview["spam"] == 1
    assert preview["uncategorized"] == 1
    assert preview["categories"]["Friends"] == 1


def test_export_preview_empty_db(tmp_db):
    """Preview on empty DB should return zeros."""
    preview = get_export_preview(tmp_db)
    assert preview["real"] == 0
    assert preview["stubs"] == 0
    assert preview["spam"] == 0
