"""3-tier contact classification: real / stub / spam."""

from schnabel.config import SPAM_DOMAINS, SPAM_LOCAL_PARTS
from schnabel.model import Contact


def is_spam_email(email: str) -> bool:
    """Check if an email address looks like spam/noreply."""
    email = email.lower().strip()
    if not email or "@" not in email:
        return False

    local, domain = email.rsplit("@", 1)

    if domain in SPAM_DOMAINS:
        return True

    # Word-boundary matching: normalize separators, then check if local part
    # exactly matches a spam pattern. This avoids false positives where a
    # real name happens to contain a spam word (e.g. "no.reply.peter" or "info.mueller").
    local_normalized = local.replace(".", "-").replace("_", "-")

    for spam_part in SPAM_LOCAL_PARTS:
        spam_normalized = spam_part.replace(".", "-").replace("_", "-")
        # Exact match
        if local_normalized == spam_normalized:
            return True

    return False


def classify_contact(contact: Contact) -> str:
    """Classify a contact as real, stub, or spam.

    Spam: all emails match spam patterns
    Stub: only email(s), no structured name, no phone, no photo, no address
    Real: everything else
    """
    emails = contact.emails
    phones = contact.phones
    addresses = contact.addresses
    has_photo = len(contact.photos) > 0
    has_name = contact.has_structured_name

    # Spam: has email(s) and ALL are spam-like, no other useful data
    if emails and all(is_spam_email(e) for e in emails):
        if not phones and not has_photo and not addresses:
            return "spam"

    # Stub: email-only contact with no real identity
    if not has_name and not phones and not has_photo and not addresses:
        if emails:
            return "stub"
        # Nothing at all — also a stub
        if not emails and not contact.fn:
            return "stub"

    return "real"
