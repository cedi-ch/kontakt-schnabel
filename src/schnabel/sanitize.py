"""Within-contact field cleanup: dedup phones, emails, addresses, URLs, text fields, BDAY."""

import re
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlparse, urlunparse

import phonenumbers

from schnabel.config import DEFAULT_PHONE_REGION
from schnabel.db import Database
from schnabel.model import ContactField
from schnabel.normalize import normalize_email, normalize_phone


@dataclass
class SanitizeReport:
    """Tracks removed/reformatted counts per field type."""
    removed: dict[str, int] = field(default_factory=lambda: {
        "empty": 0, "tel": 0, "email": 0, "adr": 0, "url": 0, "text": 0,
    })
    reformatted: dict[str, int] = field(default_factory=lambda: {
        "tel": 0, "adr": 0, "url": 0, "bday": 0, "type": 0,
    })
    ambiguous_bdays: list = field(default_factory=list)  # list[BdayAmbiguous]

    @property
    def total_removed(self) -> int:
        return sum(self.removed.values())

    @property
    def total_reformatted(self) -> int:
        return sum(self.reformatted.values())


def _clean_address_value(addr: str) -> str:
    """Clean up raw address value: fix escape artifacts, newlines.

    Preserves semicolon structure for 7-component addresses.
    """
    # Strip bleeding END:VCARD artifacts
    result = re.sub(r"END:VCARD\s*$", "", addr)
    # Unescape vCard escape sequences that survived into stored values
    result = result.replace("\\;", ";").replace("\\n", " ").replace("\\N", " ")
    # Replace actual newlines/CRs
    result = result.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    # Collapse multiple spaces (but not semicolons — those are structural)
    result = re.sub(r"\s{2,}", " ", result)
    return result.strip()


def _clean_backslash_address(addr: str) -> str:
    """Fix backslash-separated addresses by splitting into proper components.

    Handles: backslash-space, backslash-comma, over-escaped sequences.
    Called by _normalize_address on the street component when it contains
    backslashes, preserving the 7-component structure.
    """
    # Collapse over-escaped sequences: \\\, or \\\\\\, → single comma
    result = re.sub(r"(?:\\){2,},", ",", addr)
    # Replace backslash-comma with comma (normalized separator)
    result = result.replace("\\,", ",")
    # Replace backslash-space or backslash-before-digit with comma
    result = re.sub(r"\\\s+", ", ", result)
    result = re.sub(r"\\(?=\d)", ", ", result)
    # Strip any remaining stray backslashes
    result = result.replace("\\", " ")
    # Clean up whitespace
    result = re.sub(r"\s{2,}", " ", result)
    result = re.sub(r"\s*,\s*", ", ", result)
    return result.strip(", ")


def _address_key(addr: str) -> str:
    """Normalize address for comparison: clean, lowercase, sorted tokens."""
    key = _clean_address_value(addr).lower()
    # Remove all separators and punctuation for comparison
    key = re.sub(r"[;,.\-/\\]", " ", key)
    # Collapse whitespace and sort tokens for order-independent matching
    tokens = sorted(key.split())
    return " ".join(tokens)


def _split_street_to_components(street: str) -> tuple[str, str, str, str]:
    """Try to extract (street, city, code, country) from a comma-separated street string.

    Handles patterns like:
      'Hafnerstrasse 60, 8005 Zürich' → ('Hafnerstrasse 60', 'Zürich', '8005', '')
      'Brombergstr. 7A, D-79102 Freiburg' → ('Brombergstr. 7A', 'Freiburg', 'D-79102', '')
      'Strasse 1, 8000 Zürich, DEUTSCHLAND' → ('Strasse 1', 'Zürich', '8000', 'DEUTSCHLAND')
    """
    parts = [p.strip() for p in street.split(",") if p.strip()]
    if len(parts) < 2:
        return (street, "", "", "")

    street_part = parts[0]
    city = ""
    code = ""
    country = ""

    for p in parts[1:]:
        # Match postal code + city: "8005 Zürich" or "D-79102 Freiburg im Breisgau"
        m = re.match(r"^([A-Z]{0,2}-?\d{4,6})\s+(.+)$", p)
        if m and not code:
            code = m.group(1)
            city = m.group(2)
        elif re.match(r"^[A-ZÄÖÜ]{2,}$", p.upper()) and len(p) <= 20 and not country:
            # Likely a country: DEUTSCHLAND, SWITZERLAND, CH, etc.
            country = p
        elif not city:
            city = p
        else:
            # Extra parts — append to city
            city += ", " + p

    return (street_part, city, code, country)


def _normalize_address(addr: str) -> str:
    """Normalize address: clean escape artifacts, fix separators, structure components."""
    result = _clean_address_value(addr)

    # If it's semicolon-separated (structured format), clean each component
    parts = result.split(";")
    if len(parts) >= 5:
        cleaned = [p.strip() for p in parts]
        while len(cleaned) < 7:
            cleaned.append("")

        # Fix backslash-laden street component: split into proper fields
        street = cleaned[2]
        if "\\" in street:
            street_cleaned = _clean_backslash_address(street)
            s_street, s_city, s_code, s_country = _split_street_to_components(street_cleaned)
            cleaned[2] = s_street
            if s_city and not cleaned[3]:
                cleaned[3] = s_city
            if s_code and not cleaned[5]:
                cleaned[5] = s_code
            if s_country and not cleaned[6]:
                cleaned[6] = s_country

        return ";".join(cleaned[:7])

    # Free-form address: strip leading commas, normalize separators
    result = result.strip(",; ").strip()
    result = re.sub(r"\s*,\s*", ", ", result)
    result = re.sub(r"\s{2,}", " ", result)
    return result


def format_address_display(addr: str) -> str:
    """Convert a stored address to a human-readable one-liner.

    Input: 7-component semicolon string like ';;Hauptstrasse 5;Bern;;3006;CH'
    Output: 'Hauptstrasse 5, 3006 Bern, CH'
    """
    cleaned = _clean_address_value(addr)
    parts = cleaned.split(";")
    if len(parts) >= 5:
        # Structured: [PO, Extended, Street, City, Region, Code, Country]
        while len(parts) < 7:
            parts.append("")
        po, extended, street, city, region, code, country = [p.strip() for p in parts[:7]]
        pieces = []
        if po:
            pieces.append(po)
        if extended:
            pieces.append(extended)
        if street:
            pieces.append(street)
        # Code + City together
        city_part = " ".join(p for p in [code, city] if p)
        if city_part:
            pieces.append(city_part)
        if region:
            pieces.append(region)
        if country:
            pieces.append(country)
        return ", ".join(pieces) if pieces else cleaned
    # Free-form: just clean it up
    return cleaned


def find_contacts_with_multi_addresses(db: Database, contact_ids: list[int] | None = None) -> list[int]:
    """Find contacts that have 2+ genuinely distinct addresses."""
    if contact_ids is None:
        contact_ids = db.get_active_contact_ids()

    result = []
    for cid in contact_ids:
        contact = db.get_contact(cid)
        if not contact:
            continue
        addr_fields = [f for f in contact.fields if f.field_type == "adr"]
        if len(addr_fields) < 2:
            continue
        # Count distinct by address key
        keys = {_address_key(f.field_value) for f in addr_fields}
        if len(keys) >= 2:
            result.append(cid)
    return result


def resolve_multi_addresses(db: Database, contact_id: int, keep_index: int) -> int:
    """Keep one address, archive the rest to NOTE fields.

    keep_index: 0-based index into the list of ADR fields.
    Returns count of archived addresses.
    """
    contact = db.get_contact(contact_id)
    if not contact:
        return 0

    addr_fields = [(f.id, f.field_value) for f in contact.fields if f.field_type == "adr"]
    if len(addr_fields) < 2:
        return 0

    archived = 0
    for i, (fid, val) in enumerate(addr_fields):
        if i == keep_index:
            continue
        display = format_address_display(val)
        note_text = f"Alte Adresse: {display}"
        db.add_contact_field(contact_id, ContactField(field_type="note", field_value=note_text))
        db.delete_contact_field(fid)
        archived += 1

    db.commit()
    return archived


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


def repair_n_field(family_name: str, given_name: str, fn: str) -> tuple[str, str, str] | None:
    """Heuristic repair for broken N fields.

    Detects:
    - Parentheses in names: "Mueller (née Schmidt)" → family="Mueller", note about née
    - Company name in family_name: "ACME GmbH" with no given_name
    - Swapped given/family: "Mueller" as given, "Hans" as family (if FN is "Hans Mueller")

    Returns (family_name, given_name, fn) if repaired, None if no repair needed.
    """
    # Strip parenthesized suffixes from names
    paren_match = re.match(r'^(.+?)\s*\(.*\)\s*$', family_name)
    if paren_match:
        cleaned = paren_match.group(1).strip()
        if cleaned:
            new_fn = fn or f"{given_name} {cleaned}".strip()
            return cleaned, given_name, new_fn

    paren_match = re.match(r'^(.+?)\s*\(.*\)\s*$', given_name)
    if paren_match:
        cleaned = paren_match.group(1).strip()
        if cleaned:
            new_fn = fn or f"{cleaned} {family_name}".strip()
            return family_name, cleaned, new_fn

    # Detect swapped given/family using FN as reference
    if fn and given_name and family_name:
        fn_parts = fn.strip().split()
        if len(fn_parts) == 2:
            # FN is "Given Family" but N has them swapped
            if fn_parts[0].lower() == family_name.lower() and fn_parts[1].lower() == given_name.lower():
                return given_name, family_name, fn

    return None


# Swiss mobile prefixes (national format, after 0)
_CH_MOBILE_PREFIXES = {"74", "75", "76", "77", "78", "79"}


def auto_detect_phone_type(phone: str, region: str = DEFAULT_PHONE_REGION) -> str | None:
    """Auto-detect TYPE for a Swiss phone number.

    Returns "CELL" for mobile, "HOME" for landline, or None for
    non-Swiss / unparseable numbers.
    """
    try:
        parsed = phonenumbers.parse(phone, region)
        if not phonenumbers.is_valid_number(parsed):
            return None
        country = phonenumbers.region_code_for_number(parsed)
        if country != "CH":
            return None
        national = phonenumbers.format_number(
            parsed, phonenumbers.PhoneNumberFormat.NATIONAL
        )
        # National format: "079 123 45 67" → prefix is digits 1-2 (after leading 0)
        digits = national.replace(" ", "")
        if len(digits) >= 3 and digits[0] == "0":
            prefix = digits[1:3]
            if prefix in _CH_MOBILE_PREFIXES:
                return "CELL"
            return "HOME"
    except phonenumbers.NumberParseException:
        pass
    return None


@dataclass
class BdayAmbiguous:
    """A BDAY field that could not be auto-resolved."""
    contact_id: int
    contact_fn: str
    field_id: int
    raw_value: str
    option_a: str  # e.g. "1985-04-03" (DD.MM interpretation)
    option_b: str  # e.g. "1985-03-04" (MM.DD interpretation)
    label_a: str   # e.g. "3. April 1985"
    label_b: str   # e.g. "4. März 1985"


# Month names for display
_MONTH_NAMES_DE = {
    1: "Januar", 2: "Februar", 3: "März", 4: "April",
    5: "Mai", 6: "Juni", 7: "Juli", 8: "August",
    9: "September", 10: "Oktober", 11: "November", 12: "Dezember",
}


def _parse_bday(raw: str) -> str | BdayAmbiguous | None:
    """Try to normalize a BDAY value to ISO 8601 (YYYY-MM-DD or --MM-DD).

    Returns:
      - str: normalized date (auto-resolved)
      - BdayAmbiguous: needs user decision (returned with placeholder contact info)
      - None: unparseable / already clean
    """
    val = raw.strip()
    if not val:
        return None

    # Already ISO 8601: YYYY-MM-DD
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", val)
    if m:
        return None  # already clean

    # Partial date: --MM-DD (vCard format, already clean)
    m = re.match(r"^--(\d{2})-(\d{2})$", val)
    if m:
        return None  # already clean

    # Compact ISO: YYYYMMDD
    m = re.match(r"^(\d{4})(\d{2})(\d{2})$", val)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if _valid_date(y, mo, d):
            return f"{y:04d}-{mo:02d}-{d:02d}"

    # DateTime: YYYY-MM-DDT... (strip time portion)
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})T", val)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if _valid_date(y, mo, d):
            return f"{y:04d}-{mo:02d}-{d:02d}"

    # DD.MM.YYYY or DD.MM.YY (Swiss/German) — unambiguous when day > 12
    m = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{2,4})$", val)
    if m:
        a, b, y_raw = int(m.group(1)), int(m.group(2)), m.group(3)
        y = _expand_year(y_raw)
        if a > 12 and b <= 12 and _valid_date(y, b, a):
            return f"{y:04d}-{b:02d}-{a:02d}"
        if b > 12 and a <= 12 and _valid_date(y, a, b):
            # Unusual: MM.DD.YYYY
            return f"{y:04d}-{a:02d}-{b:02d}"
        if a <= 12 and b <= 12 and _valid_date(y, b, a):
            # Ambiguous but we assume DD.MM for Swiss locale
            return f"{y:04d}-{b:02d}-{a:02d}"
        return None  # invalid

    # DD.MM (no year, Swiss format)
    m = re.match(r"^(\d{1,2})\.(\d{1,2})\.?$", val)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if a > 12 and b <= 12:
            return f"--{b:02d}-{a:02d}"
        if b > 12 and a <= 12:
            return f"--{a:02d}-{b:02d}"
        if a <= 12 and b <= 12 and 1 <= a <= 31 and 1 <= b <= 12:
            return f"--{b:02d}-{a:02d}"  # assume DD.MM
        return None

    # MM/DD/YYYY or DD/MM/YYYY (slash-separated) — ambiguous!
    m = re.match(r"^(\d{1,2})[/\-](\d{1,2})[/\-](\d{2,4})$", val)
    if m:
        a, b, y_raw = int(m.group(1)), int(m.group(2)), m.group(3)
        y = _expand_year(y_raw)

        # Unambiguous cases
        if a > 12 and b <= 12 and _valid_date(y, b, a):
            # a must be day: DD/MM/YYYY
            return f"{y:04d}-{b:02d}-{a:02d}"
        if b > 12 and a <= 12 and _valid_date(y, a, b):
            # b must be day: MM/DD/YYYY
            return f"{y:04d}-{a:02d}-{b:02d}"

        # Both ≤ 12 → ambiguous
        if a <= 12 and b <= 12:
            # Option A: DD/MM (European)
            iso_a = f"{y:04d}-{b:02d}-{a:02d}" if _valid_date(y, b, a) else None
            # Option B: MM/DD (US)
            iso_b = f"{y:04d}-{a:02d}-{b:02d}" if _valid_date(y, a, b) else None

            if iso_a and iso_b and iso_a != iso_b:
                label_a = f"{a}. {_MONTH_NAMES_DE.get(b, '?')} {y}"
                label_b = f"{b}. {_MONTH_NAMES_DE.get(a, '?')} {y}"
                return BdayAmbiguous(
                    contact_id=0, contact_fn="", field_id=0,
                    raw_value=val,
                    option_a=iso_a, option_b=iso_b,
                    label_a=label_a, label_b=label_b,
                )
            if iso_a:
                return iso_a
            if iso_b:
                return iso_b
        return None

    # Text month formats: "March 15, 1985" / "15. März 1985" / "15 Mar 1985"
    text_result = _parse_text_date(val)
    if text_result:
        return text_result

    return None  # unparseable, leave as-is


def _expand_year(y_raw: str) -> int:
    """Expand 2-digit year: >30 → 19xx, ≤30 → 20xx."""
    if len(y_raw) == 2:
        yy = int(y_raw)
        return 1900 + yy if yy > 30 else 2000 + yy
    return int(y_raw)


def _valid_date(year: int, month: int, day: int) -> bool:
    """Check if a date is valid."""
    try:
        datetime(year, month, day)
        return True
    except ValueError:
        return False


_TEXT_MONTHS = {
    # English
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4,
    "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    # German
    "januar": 1, "februar": 2, "märz": 3, "mai": 5,
    "juni": 6, "juli": 7, "oktober": 10, "dezember": 12,
    "mär": 3, "okt": 10, "dez": 12,
}


def _parse_text_date(val: str) -> str | None:
    """Try to parse text date formats like 'March 15, 1985' or '15. März 1985'."""
    # "15. März 1985" / "15 March 1985"
    m = re.match(r"(\d{1,2})\.?\s+(\w+)\s+(\d{4})", val)
    if m:
        day, month_str, year = int(m.group(1)), m.group(2).lower().rstrip("."), int(m.group(3))
        month = _TEXT_MONTHS.get(month_str)
        if month and _valid_date(year, month, day):
            return f"{year:04d}-{month:02d}-{day:02d}"

    # "March 15, 1985" / "Mar 15 1985"
    m = re.match(r"(\w+)\s+(\d{1,2}),?\s+(\d{4})", val)
    if m:
        month_str, day, year = m.group(1).lower().rstrip("."), int(m.group(2)), int(m.group(3))
        month = _TEXT_MONTHS.get(month_str)
        if month and _valid_date(year, month, day):
            return f"{year:04d}-{month:02d}-{day:02d}"

    return None


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

        # Step 4: Address clean + normalize + dedup
        addr_fields = [(f.id, f.field_value) for f in contact.fields if f.field_type == "adr"]
        if addr_fields:
            # First pass: clean all address values (fix newlines, escapes, etc.)
            cleaned_fields = []
            for fid, val in addr_fields:
                cleaned = _normalize_address(val)
                if cleaned != val:
                    db.update_contact_field(fid, cleaned)
                    report.reformatted["adr"] += 1
                cleaned_fields.append((fid, cleaned))

            # Second pass: dedup by normalized key
            addr_groups: dict[str, list[tuple[int, str]]] = {}
            for fid, val in cleaned_fields:
                key = _address_key(val)
                if not key:  # empty after cleaning
                    db.delete_contact_field(fid)
                    report.removed["adr"] += 1
                    continue
                addr_groups.setdefault(key, []).append((fid, val))

            for key, members in addr_groups.items():
                if len(members) > 1:
                    # Keep the richest (longest) version
                    best_id, best_val = max(members, key=lambda x: len(x[1]))
                    for fid, val in members:
                        if fid != best_id:
                            db.delete_contact_field(fid)
                            report.removed["adr"] += 1

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

        # Step 6: Simple text dedup (ORG, TITLE, NICKNAME, ROLE, NOTE, CATEGORIES)
        for ftype in ("org", "title", "nickname", "role", "note", "categories"):
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

        # Reload contact after dedup steps (field list is stale)
        contact = db.get_contact(cid)
        if not contact:
            continue

        # Step 7: BDAY normalize
        bday_fields = [(f.id, f.field_value) for f in contact.fields if f.field_type == "bday"]
        for fid, val in bday_fields:
            result = _parse_bday(val)
            if result is None:
                continue  # already clean or unparseable
            if isinstance(result, str):
                db.update_contact_field(fid, result)
                report.reformatted["bday"] += 1
            elif isinstance(result, BdayAmbiguous):
                result.contact_id = cid
                result.contact_fn = contact.fn
                result.field_id = fid
                report.ambiguous_bdays.append(result)

        # Step 8: Repair broken N fields
        repaired = repair_n_field(contact.family_name, contact.given_name, contact.fn)
        if repaired:
            new_family, new_given, new_fn = repaired
            db.update_contact_name(cid, new_fn, new_family, new_given)

        # Step 9: Auto-detect phone TYPE for Swiss numbers
        for f in contact.fields:
            if f.field_type != "tel":
                continue
            # Skip if TYPE is already set
            if f.field_params.get("TYPE"):
                continue
            detected = auto_detect_phone_type(f.field_value)
            if detected:
                params = dict(f.field_params)
                params["TYPE"] = detected
                db.update_contact_field_params(f.id, params)
                report.reformatted["type"] += 1

        db.commit()

        if progress_callback and (i % 100 == 0 or i == total - 1):
            progress_callback(i + 1, total)

    return report
