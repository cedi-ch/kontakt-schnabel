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

    Spam: all emails match spam patterns, no phone/photo/address
    Real: has phone, photo, or address (contactable beyond just email)
    Stub: everything else (name-only, email-only, empty)
    """
    emails = contact.emails
    phones = contact.phones
    addresses = contact.addresses
    has_photo = len(contact.photos) > 0

    # Spam: has email(s) and ALL are spam-like, no other useful data
    if emails and all(is_spam_email(e) for e in emails):
        if not phones and not has_photo and not addresses:
            return "spam"

    # Real: has at least one contactable field (phone, photo, or address)
    if phones or has_photo or addresses:
        return "real"

    # Everything else is a stub (name-only, name+email, email-only, empty)
    return "stub"
