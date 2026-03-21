"""4-stage normalization pipeline for contact matching."""

import re

import phonenumbers
from unidecode import unidecode

from schnabel.config import DEFAULT_PHONE_REGION
from schnabel.db import Database
from schnabel.model import Contact


def normalize_email(email: str) -> str:
    """Normalize email: lowercase, strip, googlemail→gmail."""
    email = email.lower().strip()
    email = re.sub(r"@googlemail\.com$", "@gmail.com", email)
    return email


def normalize_phone(phone: str, region: str = DEFAULT_PHONE_REGION) -> str | None:
    """Normalize phone to E.164 format. Returns None if unparseable."""
    try:
        parsed = phonenumbers.parse(phone, region)
        if phonenumbers.is_valid_number(parsed):
            return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    except phonenumbers.NumberParseException:
        pass
    return None


def phone_last7(phone: str) -> str | None:
    """Extract last 7 digits from a phone number (fallback matching)."""
    digits = re.sub(r"\D", "", phone)
    if len(digits) >= 7:
        return digits[-7:]
    return None


def name_key(contact: Contact) -> str:
    """Create a normalized name key: lowercase, stripped."""
    parts = [contact.given_name, contact.family_name]
    return " ".join(p.strip().lower() for p in parts if p.strip())


def name_simplified(contact: Contact) -> str:
    """Create a simplified name: unidecoded, no punctuation, sorted tokens.

    'Hans Müller' → 'hans muller'
    'Müller, Hans' → 'hans muller'
    """
    parts = [contact.given_name, contact.family_name]
    combined = " ".join(p.strip() for p in parts if p.strip())
    # Remove punctuation
    combined = re.sub(r"[^\w\s]", " ", combined)
    # Unidecode for accent-free matching
    combined = unidecode(combined).lower()
    # Normalize whitespace and sort tokens
    tokens = sorted(combined.split())
    return " ".join(tokens)


def normalize_bday(bday: str) -> str | None:
    """Normalize BDAY to YYYY-MM-DD. Returns None if unparseable."""
    bday = bday.strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", bday)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.match(r"(\d{4})(\d{2})(\d{2})$", bday)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return None


def fn_simplified(fn: str) -> str:
    """Simplified version of FN field (for contacts without structured N)."""
    fn = re.sub(r"[^\w\s]", " ", fn)
    fn = unidecode(fn).lower()
    tokens = sorted(fn.split())
    return " ".join(tokens)


def normalize_contacts(db: Database, progress_callback=None):
    """Run full normalization pipeline on all active contacts."""
    db.clear_normalized()
    contact_ids = db.get_active_contact_ids()
    total = len(contact_ids)

    for i, cid in enumerate(contact_ids):
        contact = db.get_contact(cid)
        if not contact:
            continue

        # Emails
        for email in contact.emails:
            norm = normalize_email(email)
            if norm:
                db.insert_normalized(cid, "email", norm)

        # Phones — E.164 and last-7
        for phone in contact.phones:
            e164 = normalize_phone(phone)
            if e164:
                db.insert_normalized(cid, "phone_e164", e164)
            last7 = phone_last7(phone)
            if last7:
                db.insert_normalized(cid, "phone_last7", last7)

        # Name key (lowercased)
        nk = name_key(contact)
        if nk:
            db.insert_normalized(cid, "name_key", nk)

        # Name simplified (unidecoded, sorted)
        ns = name_simplified(contact)
        if ns:
            db.insert_normalized(cid, "name_simplified", ns)

        # BDAY (birthday blocking key)
        for bday in contact.bdays:
            norm_bday = normalize_bday(bday)
            if norm_bday:
                db.insert_normalized(cid, "bday", norm_bday)

        # FN simplified (fallback for contacts without structured name)
        if contact.fn and not contact.has_structured_name:
            fs = fn_simplified(contact.fn)
            if fs:
                db.insert_normalized(cid, "name_simplified", fs)

        if progress_callback and (i % 200 == 0 or i == total - 1):
            progress_callback(i + 1, total)

    db.commit()
