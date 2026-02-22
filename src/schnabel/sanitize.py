"""Within-contact field cleanup: dedup phones, emails, addresses, URLs, text fields."""

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse, urlunparse

import phonenumbers

from schnabel.config import DEFAULT_PHONE_REGION
from schnabel.db import Database
from schnabel.normalize import normalize_email, normalize_phone


@dataclass
class SanitizeReport:
    """Tracks removed/reformatted counts per field type."""
    removed: dict[str, int] = field(default_factory=lambda: {
        "empty": 0, "tel": 0, "email": 0, "adr": 0, "url": 0, "text": 0,
    })
    reformatted: dict[str, int] = field(default_factory=lambda: {
        "tel": 0, "adr": 0, "url": 0,
    })

    @property
    def total_removed(self) -> int:
        return sum(self.removed.values())

    @property
    def total_reformatted(self) -> int:
        return sum(self.reformatted.values())


def _address_key(addr: str) -> str:
    """Normalize address for comparison: lowercase, strip punctuation/separators."""
    key = addr.lower()
    key = re.sub(r"[;,.\-/]", " ", key)
    key = re.sub(r"\s+", " ", key).strip()
    return key


def _normalize_address(addr: str) -> str:
    """Normalize address separators: semicolons and multiple spaces → comma-space."""
    # Replace semicolons with commas
    result = addr.replace(";", ",")
    # Collapse multiple commas/spaces
    result = re.sub(r"\s*,\s*", ", ", result)
    # Collapse multiple spaces
    result = re.sub(r"\s{2,}", " ", result)
    return result.strip().strip(",").strip()


def _url_key(url: str) -> str:
    """Normalize URL for comparison: lowercase scheme/host, strip www., trailing slash."""
    try:
        parsed = urlparse(url)
        scheme = (parsed.scheme or "https").lower()
        netloc = (parsed.netloc or "").lower()
        # Strip www. for comparison
        if netloc.startswith("www."):
            netloc = netloc[4:]
        path = parsed.path.rstrip("/") or ""
        return f"{scheme}://{netloc}{path}"
    except Exception:
        return url.lower().rstrip("/")


def _normalize_url(url: str) -> str:
    """Clean up URL: lowercase scheme, strip trailing slash."""
    try:
        parsed = urlparse(url)
        scheme = (parsed.scheme or "https").lower()
        netloc = parsed.netloc or ""
        path = parsed.path.rstrip("/") or ""
        result = urlunparse((scheme, netloc, path, parsed.params, parsed.query, parsed.fragment))
        return result
    except Exception:
        return url


def _best_phone_format(phones: list[tuple[int, str]], e164: str) -> tuple[int, str]:
    """Among phones that map to the same E.164, pick the best formatted one.

    Preference: international format > national format > raw.
    Returns (field_id, best_value).
    """
    if len(phones) == 1:
        return phones[0]

    # Try to format via phonenumbers for the ideal version
    try:
        parsed = phonenumbers.parse(e164, DEFAULT_PHONE_REGION)
        country = phonenumbers.region_code_for_number(parsed)
        if country == DEFAULT_PHONE_REGION:
            ideal = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.NATIONAL)
        else:
            ideal = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL)
    except phonenumbers.NumberParseException:
        ideal = e164

    # Pick the one closest to ideal, or the longest
    best = phones[0]
    for fid, val in phones:
        if val == ideal:
            return fid, val
        if len(val) > len(best[1]):
            best = (fid, val)
    return best


def sanitize_contacts(db: Database, progress_callback=None) -> SanitizeReport:
    """Run within-contact field sanitization on all active contacts."""
    report = SanitizeReport()
    contact_ids = db.get_active_contact_ids()
    total = len(contact_ids)

    for i, cid in enumerate(contact_ids):
        contact = db.get_contact(cid)
        if not contact:
            continue

        # Step 1: Remove empty fields
        for f in contact.fields:
            if not f.field_value or not f.field_value.strip():
                db.delete_contact_field(f.id)
                report.removed["empty"] += 1

        # Reload contact after removing empty fields (field list is stale)
        contact = db.get_contact(cid)
        if not contact:
            continue

        # Step 2: Phone dedup via E.164
        phone_fields = [(f.id, f.field_value) for f in contact.fields if f.field_type == "tel"]
        if len(phone_fields) > 1:
            groups: dict[str, list[tuple[int, str]]] = {}
            ungrouped: list[tuple[int, str]] = []
            for fid, val in phone_fields:
                e164 = normalize_phone(val)
                if e164:
                    groups.setdefault(e164, []).append((fid, val))
                else:
                    ungrouped.append((fid, val))

            for e164, members in groups.items():
                if len(members) > 1:
                    best_id, best_val = _best_phone_format(members, e164)
                    for fid, val in members:
                        if fid != best_id:
                            db.delete_contact_field(fid)
                            report.removed["tel"] += 1
                    # Reformat the surviving phone
                    try:
                        parsed = phonenumbers.parse(best_val, DEFAULT_PHONE_REGION)
                        country = phonenumbers.region_code_for_number(parsed)
                        if country == DEFAULT_PHONE_REGION:
                            formatted = phonenumbers.format_number(
                                parsed, phonenumbers.PhoneNumberFormat.NATIONAL)
                        else:
                            formatted = phonenumbers.format_number(
                                parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL)
                        if formatted != best_val:
                            db.update_contact_field(best_id, formatted)
                            report.reformatted["tel"] += 1
                    except phonenumbers.NumberParseException:
                        pass

        # Step 3: Email dedup (case-insensitive)
        email_fields = [(f.id, f.field_value) for f in contact.fields if f.field_type == "email"]
        if len(email_fields) > 1:
            seen: dict[str, int] = {}
            for fid, val in email_fields:
                key = normalize_email(val)
                if key in seen:
                    db.delete_contact_field(fid)
                    report.removed["email"] += 1
                else:
                    seen[key] = fid

        # Step 4: Address normalize + dedup
        addr_fields = [(f.id, f.field_value) for f in contact.fields if f.field_type == "adr"]
        if addr_fields:
            addr_groups: dict[str, list[tuple[int, str]]] = {}
            for fid, val in addr_fields:
                key = _address_key(val)
                addr_groups.setdefault(key, []).append((fid, val))

            for key, members in addr_groups.items():
                # Keep the richest (longest) version
                best_id, best_val = max(members, key=lambda x: len(x[1]))
                for fid, val in members:
                    if fid != best_id:
                        db.delete_contact_field(fid)
                        report.removed["adr"] += 1
                # Normalize separators on the surviving address
                normalized = _normalize_address(best_val)
                if normalized != best_val:
                    db.update_contact_field(best_id, normalized)
                    report.reformatted["adr"] += 1

        # Step 5: URL normalize + dedup
        url_fields = [(f.id, f.field_value) for f in contact.fields if f.field_type == "url"]
        if url_fields:
            url_groups: dict[str, list[tuple[int, str]]] = {}
            for fid, val in url_fields:
                key = _url_key(val)
                url_groups.setdefault(key, []).append((fid, val))

            for key, members in url_groups.items():
                best_id, best_val = members[0]
                for fid, val in members[1:]:
                    db.delete_contact_field(fid)
                    report.removed["url"] += 1
                # Normalize the surviving URL
                normalized = _normalize_url(best_val)
                if normalized != best_val:
                    db.update_contact_field(best_id, normalized)
                    report.reformatted["url"] += 1

        # Step 6: Simple text dedup (ORG, TITLE, NICKNAME, ROLE, NOTE)
        for ftype in ("org", "title", "nickname", "role", "note"):
            text_fields = [(f.id, f.field_value) for f in contact.fields if f.field_type == ftype]
            if len(text_fields) > 1:
                seen_text: dict[str, int] = {}
                for fid, val in text_fields:
                    key = val.lower().strip()
                    if key in seen_text:
                        db.delete_contact_field(fid)
                        report.removed["text"] += 1
                    else:
                        seen_text[key] = fid

        db.commit()

        if progress_callback and (i % 100 == 0 or i == total - 1):
            progress_callback(i + 1, total)

    return report
