"""Tests for matching engine."""

from schnabel.model import Contact, ContactField
from schnabel.match import score_pair


def _insert_test_contacts(db, contacts):
    """Insert contacts and return their IDs."""
    ids = []
    for c in contacts:
        cid = db.insert_contact(c)
        ids.append(cid)
    db.commit()
    return ids


def test_same_email_high_score(tmp_db):
    c1 = Contact(fn="Hans Mueller", family_name="Mueller", given_name="Hans", category="real")
    c1.fields.append(ContactField("email", "hans@gmail.com"))

    c2 = Contact(fn="Hans Müller", family_name="Müller", given_name="Hans", category="real")
    c2.fields.append(ContactField("email", "hans@gmail.com"))

    ids = _insert_test_contacts(tmp_db, [c1, c2])
    scores = score_pair(tmp_db, ids[0], ids[1])

    assert scores["email_score"] == 1.0
    assert scores["confidence"] >= 0.70  # anchor rule


def test_same_phone_high_score(tmp_db):
    c1 = Contact(fn="Hans", family_name="Mueller", given_name="Hans", category="real")
    c1.fields.append(ContactField("tel", "079 123 45 67"))

    c2 = Contact(fn="Hans", family_name="Müller", given_name="Hans", category="real")
    c2.fields.append(ContactField("tel", "+41791234567"))

    ids = _insert_test_contacts(tmp_db, [c1, c2])
    scores = score_pair(tmp_db, ids[0], ids[1])

    assert scores["phone_score"] == 1.0
    assert scores["confidence"] >= 0.70


def test_name_only_capped(tmp_db):
    c1 = Contact(fn="Hans Mueller", family_name="Mueller", given_name="Hans", category="real")
    c2 = Contact(fn="Hans Muller", family_name="Muller", given_name="Hans", category="real")

    ids = _insert_test_contacts(tmp_db, [c1, c2])
    scores = score_pair(tmp_db, ids[0], ids[1])

    # Name-only match should be capped at 0.60
    assert scores["confidence"] <= 0.60


def test_email_and_phone_very_high(tmp_db):
    c1 = Contact(fn="Hans", family_name="Mueller", given_name="Hans", category="real")
    c1.fields.append(ContactField("email", "hans@gmail.com"))
    c1.fields.append(ContactField("tel", "079 123 45 67"))

    c2 = Contact(fn="Hans", family_name="Müller", given_name="Hans", category="real")
    c2.fields.append(ContactField("email", "hans@gmail.com"))
    c2.fields.append(ContactField("tel", "+41791234567"))

    ids = _insert_test_contacts(tmp_db, [c1, c2])
    scores = score_pair(tmp_db, ids[0], ids[1])

    assert scores["confidence"] >= 0.95  # anchor rule: shared email AND phone
