"""Tests for Schritt 5 polish features."""

import re

from schnabel.model import Contact, ContactField
from schnabel.sanitize import repair_n_field, auto_detect_phone_type
from schnabel.reader import parse_vcard


# --- N-Feld-Reparatur ---

def test_repair_n_parentheses_in_family():
    """Parenthesized suffix in family_name should be stripped."""
    result = repair_n_field("Mueller (née Schmidt)", "Anna", "Anna Mueller")
    assert result is not None
    family, given, fn = result
    assert family == "Mueller"
    assert given == "Anna"


def test_repair_n_parentheses_in_given():
    """Parenthesized suffix in given_name should be stripped."""
    result = repair_n_field("Mueller", "Hans (Junior)", "Hans Mueller")
    assert result is not None
    family, given, fn = result
    assert given == "Hans"
    assert family == "Mueller"


def test_repair_n_swapped_names():
    """Swapped given/family names should be detected via FN."""
    result = repair_n_field("Hans", "Mueller", "Hans Mueller")
    assert result is not None
    family, given, fn = result
    assert given == "Hans"
    assert family == "Mueller"


def test_repair_n_no_fix_needed():
    """Normal names should not be changed."""
    result = repair_n_field("Mueller", "Hans", "Hans Mueller")
    assert result is None


# --- X-ANNIVERSARY → BDAY ---

def test_x_anniversary_parsed_as_bday():
    """X-ANNIVERSARY should be imported as BDAY if no BDAY present."""
    vcard_text = (
        "BEGIN:VCARD\n"
        "VERSION:3.0\n"
        "FN:Test User\n"
        "N:User;Test;;;\n"
        "X-ANNIVERSARY:1990-06-15\n"
        "END:VCARD"
    )
    contact = parse_vcard(vcard_text)
    assert contact is not None
    bdays = [f.field_value for f in contact.fields if f.field_type == "bday"]
    assert len(bdays) == 1
    assert "1990-06-15" in bdays[0]


def test_x_anniversary_not_overriding_bday():
    """X-ANNIVERSARY should NOT override existing BDAY."""
    vcard_text = (
        "BEGIN:VCARD\n"
        "VERSION:3.0\n"
        "FN:Test User\n"
        "N:User;Test;;;\n"
        "BDAY:1985-03-20\n"
        "X-ANNIVERSARY:1990-06-15\n"
        "END:VCARD"
    )
    contact = parse_vcard(vcard_text)
    assert contact is not None
    bdays = [f.field_value for f in contact.fields if f.field_type == "bday"]
    assert len(bdays) == 1
    assert "1985-03-20" in bdays[0]


# --- DB backup ---

def test_db_backup(tmp_db, tmp_path):
    """Database backup should create a working copy."""
    c = Contact(fn="Backup Test", family_name="Test", given_name="Backup", category="real")
    c.fields.append(ContactField("email", "backup@test.com"))
    tmp_db.insert_contact(c)
    tmp_db.commit()

    backup_path = tmp_db.create_backup("test")
    assert backup_path.exists()

    # Open backup and verify data
    from schnabel.db import Database
    backup_db = Database(backup_path)
    stats = backup_db.get_stats()
    assert stats["total"] == 1
    assert stats["real"] == 1
    backup_db.close()


# --- DB constraints ---

def test_cascade_delete_fields(tmp_db):
    """Deleting a contact should cascade-delete its fields."""
    c = Contact(fn="Cascade", family_name="Test", given_name="Cascade", category="real")
    c.fields.append(ContactField("email", "cascade@test.com"))
    c.fields.append(ContactField("tel", "+41791234567"))
    cid = tmp_db.insert_contact(c)
    tmp_db.commit()

    # Verify fields exist
    contact = tmp_db.get_contact(cid)
    assert len(contact.fields) == 2

    # Hard-delete the contact
    tmp_db.conn.execute("DELETE FROM contacts WHERE id = ?", (cid,))
    tmp_db.commit()

    # Fields should be gone too
    rows = tmp_db.conn.execute(
        "SELECT COUNT(*) as n FROM contact_fields WHERE contact_id = ?", (cid,)
    ).fetchone()
    assert rows["n"] == 0


def test_resolution_check_constraint(tmp_db):
    """Resolution column should only accept valid values."""
    c1 = Contact(fn="A", category="real")
    c2 = Contact(fn="B", category="real")
    id1 = tmp_db.insert_contact(c1)
    id2 = tmp_db.insert_contact(c2)
    tmp_db.commit()

    # Valid resolution
    tmp_db.insert_similarity_pair(id1, id2, 0.8, 0.8, 0.0, 0.0, 0.0, 0.0)
    tmp_db.commit()

    # Invalid resolution should fail
    import sqlite3
    try:
        tmp_db.conn.execute(
            "UPDATE similarity_pairs SET resolution = 'invalid_value' WHERE contact_a_id = ?",
            (id1,),
        )
        tmp_db.commit()
        assert False, "Should have raised an error for invalid resolution"
    except sqlite3.IntegrityError:
        tmp_db.conn.rollback()


# --- Atomic state writes ---

def test_atomic_state_write(tmp_path):
    """State save should use atomic write (no partial files on crash)."""
    from schnabel.splittui import save_split_state, load_split_state, SplitTarget

    contacts = [Contact(fn="Test")]
    targets = [SplitTarget(name="file1", key="1")]
    state_path = tmp_path / "state.json"

    save_split_state(contacts, targets, {}, set(), "input.vcf", state_path)

    # File should exist and be valid JSON
    assert state_path.exists()
    loaded = load_split_state(state_path)
    assert loaded is not None
    assert len(loaded["contacts"]) == 1

    # No .tmp file should remain
    assert not state_path.with_suffix(".tmp").exists()
