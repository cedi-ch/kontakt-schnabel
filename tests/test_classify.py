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


# --- Bug #7: False spam positives from character stripping ---

def test_no_false_positive_normal_dots():
    """Email with dots in local part should NOT be classified as spam.

    e.g. 'no.reply.peter@bluewin.ch' should not match 'noreply'
    because the dots are word separators, not noise.
    """
    # This was the actual bug: dots stripped → "noreplypeter" → startswith("noreply") → spam
    assert not is_spam_email("no.reply.peter@bluewin.ch")


def test_no_false_positive_info_prefix():
    """Email like 'info.mueller@company.ch' should NOT be spam.

    'info' is a spam local part, but 'info.mueller' is a real person.
    """
    assert not is_spam_email("info.mueller@company.ch")


def test_spam_with_separators():
    """Actual spam addresses with various separators should still be caught."""
    assert is_spam_email("no-reply@company.ch")
    assert is_spam_email("no_reply@company.ch")
    assert is_spam_email("no.reply@company.ch")
    assert is_spam_email("newsletter@shop.com")


def test_spam_with_suffix():
    """Addresses with spam-like prefix but additional words are NOT spam.

    This is the tradeoff: we prefer false negatives (missing some spam)
    over false positives (classifying real contacts as spam).
    'noreply-bounces' could be spam, but the same pattern matches
    'info.mueller' which is a real person — so we require exact match.
    """
    # These are edge cases — better to let them through than block real contacts
    assert not is_spam_email("noreply-bounces@company.ch")
    assert not is_spam_email("newsletter-weekly@shop.com")


def test_real_email_not_spam():
    """Regular email addresses should never be spam."""
    assert not is_spam_email("hans.mueller@gmail.com")
    assert not is_spam_email("peter@bluewin.ch")
    assert not is_spam_email("anna.info@company.ch")  # 'info' is suffix, not local part
