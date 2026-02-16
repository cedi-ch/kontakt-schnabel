"""Tests for contact classification."""

from schnabel.classify import classify_contact, is_spam_email
from schnabel.model import Contact, ContactField


def test_is_spam_email():
    assert is_spam_email("newsletter@bigshop.com")
    assert is_spam_email("noreply@company.ch")
    assert is_spam_email("no-reply@test.com")
    assert not is_spam_email("hans@gmail.com")
    assert not is_spam_email("peter.meier@bluewin.ch")


def test_classify_real():
    c = Contact(fn="Hans Mueller", family_name="Mueller", given_name="Hans")
    c.fields.append(ContactField("email", "hans@gmail.com"))
    c.fields.append(ContactField("tel", "079 123 45 67"))
    assert classify_contact(c) == "real"


def test_classify_stub():
    c = Contact(fn="someone@randomdomain.com")
    c.fields.append(ContactField("email", "someone@randomdomain.com"))
    assert classify_contact(c) == "stub"


def test_classify_spam():
    c = Contact(fn="newsletter@bigshop.com")
    c.fields.append(ContactField("email", "newsletter@bigshop.com"))
    assert classify_contact(c) == "spam"


def test_classify_real_with_name_only():
    """Contact with structured name but no email/phone is still real."""
    c = Contact(fn="Hans Mueller", family_name="Mueller", given_name="Hans")
    assert classify_contact(c) == "real"


def test_classify_empty_stub():
    """Contact with nothing is a stub."""
    c = Contact()
    assert classify_contact(c) == "stub"
