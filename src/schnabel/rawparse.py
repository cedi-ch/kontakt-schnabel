"""Raw text parser — extract contacts from unstructured text files.

Implements a subtract-and-classify pipeline: each recognized field is removed
from the remaining text before the next extraction step, so nothing gets
double-matched.
"""

import re
from dataclasses import dataclass, field

import phonenumbers

from schnabel.config import DEFAULT_PHONE_REGION
from schnabel.model import Contact, ContactField


# ── Data structures ────────────────────────────────────────────────────────


@dataclass
class ParsedField:
    """A single field extracted from raw text."""
    field_type: str           # fn, email, tel, adr, org, url, note, unknown
    value: str                # extracted / cleaned value
    confidence: str           # high, medium, low
    original_fragment: str    # what was matched in the source text


@dataclass
class ParsedContact:
    """A contact assembled from raw text."""
    raw_text: str
    fields: list[ParsedField] = field(default_factory=list)
    status: str = "pending"   # pending / accepted / rejected


# ── Regex patterns ─────────────────────────────────────────────────────────

_EMAIL_RE = re.compile(r"[\w.+\-]+@[\w.\-]+\.\w{2,}", re.UNICODE)

# Phone patterns: +41 xx xxx xx xx, 0041 xx, 079 xxx xx xx, +49 xxx ..., etc.
# Broad pattern — we rely on phonenumbers.parse() for validation.
_PHONE_RE = re.compile(
    r"(?<!\d)"                         # not preceded by digit
    r"(?:\+|00)?\d[\d\s/\-().]{6,18}\d"  # digit core
    r"(?!\d)",                          # not followed by digit
)

_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)

# Swiss PLZ (4 digits) + place name
_PLZ_RE = re.compile(r"\b(\d{4})\s+([A-ZÄÖÜ][a-zäöüéèà]+(?:\s+[A-ZÄÖÜ][a-zäöüéèà]+)*)\b")

# Street suffixes common in CH/DE/AT
_STREET_SUFFIXES = (
    "strasse", "str.", "gasse", "weg", "platz", "allee", "ring",
    "rain", "graben", "matte", "matt", "feld", "halde", "bühl",
    "wil", "dorf", "berg", "stein", "au", "egg", "ried", "acker",
)

_STREET_RE = re.compile(
    r"(?:[\wäöüéèà]+(?:[-\s][\wäöüéèà]+)*"
    r"(?:" + "|".join(re.escape(s) for s in _STREET_SUFFIXES) + r"))"
    r"\s*\d{1,4}\s*[a-zA-Z]?",
    re.IGNORECASE | re.UNICODE,
)

# Organization suffixes
_ORG_SUFFIXES = (
    "AG", "GmbH", "SA", "Sàrl", "Ltd", "Ltd.", "Inc", "Inc.",
    "LLC", "Verein", "Stiftung", "Co.", "KG", "OHG", "SE",
    "& Co", "& Cie", "e.V.",
)

_ORG_RE = re.compile(
    r"(?:[\wäöüéèà][\wäöüéèà&\s.\-]*?"
    r"(?:" + "|".join(re.escape(s) for s in _ORG_SUFFIXES) + r"))"
    r"\.?",
    re.UNICODE,
)

# Birthday patterns: "Geburtstag 20.01.26", "Geburi: 03.12", "geburi 11.05"
_BDAY_RE = re.compile(
    r"(?:Geburtstag|Geburi)\s*:?\s*(\d{1,2})\.(\d{1,2})\.?(\d{2,4})?",
    re.IGNORECASE,
)

# Phone label prefixes that should be stripped from remaining text
_PHONE_LABEL_RE = re.compile(
    r"\b(?:Mobil|Mobile|Tel|Telefon|Festnetz|Handy|Arbeit|Geschäft|Privat)e?\s*:?\s*(?=\s|$)",
    re.IGNORECASE,
)

# Name prefixes (salutations / titles)
_NAME_PREFIXES = {"dr.", "dr", "prof.", "prof", "ing.", "dipl.", "herr", "frau", "hr.", "fr."}


# ── Extraction helpers ─────────────────────────────────────────────────────


def _extract_and_remove(pattern: re.Pattern, text: str) -> tuple[list[re.Match], str]:
    """Find all matches, return them, and remove them from text."""
    matches = list(pattern.finditer(text))
    for m in reversed(matches):  # reverse to keep positions valid
        text = text[:m.start()] + " " + text[m.end():]
    return matches, text


def _format_bday(day: str, month: str, year: str | None) -> str:
    """Convert DD.MM[.YY] to vCard date format."""
    dd = day.zfill(2)
    mm = month.zfill(2)
    if year:
        if len(year) == 2:
            yy = int(year)
            yyyy = 1900 + yy if yy > 30 else 2000 + yy
        else:
            yyyy = int(year)
        return f"{yyyy}-{mm}-{dd}"
    # No year — partial date
    return f"--{mm}-{dd}"


def _validate_phone(raw: str) -> str | None:
    """Try to parse a raw phone string. Return E.164 if valid, else None."""
    cleaned = raw.strip()
    try:
        parsed = phonenumbers.parse(cleaned, DEFAULT_PHONE_REGION)
        if phonenumbers.is_valid_number(parsed):
            return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    except phonenumbers.NumberParseException:
        pass
    return None


def _split_name(name_str: str) -> dict:
    """Split a name string into given/family/prefix components.

    Returns dict with keys: given, family, prefix.
    """
    name_str = name_str.strip()
    if not name_str:
        return {"given": "", "family": "", "prefix": ""}

    # Strip prefix (Dr., Prof., etc.)
    prefix = ""
    words = name_str.split()
    while words and words[0].lower().rstrip(".") + "." in {p + "." for p in _NAME_PREFIXES}:
        prefix = (prefix + " " + words.pop(0)).strip()
    name_str = " ".join(words)

    # "Nachname, Vorname" pattern
    if "," in name_str:
        parts = [p.strip() for p in name_str.split(",", 1)]
        if len(parts) == 2 and parts[0] and parts[1]:
            return {"given": parts[1], "family": parts[0], "prefix": prefix}

    # "Vorname [Mittelnamen] Nachname" — last word is family name
    words = name_str.split()
    if len(words) >= 2:
        return {"given": " ".join(words[:-1]), "family": words[-1], "prefix": prefix}
    elif len(words) == 1:
        return {"given": "", "family": words[0], "prefix": prefix}

    return {"given": "", "family": name_str, "prefix": prefix}


# ── Block splitting ────────────────────────────────────────────────────────


def _split_into_blocks(text: str) -> list[str]:
    """Split raw text into candidate contact blocks.

    Uses blank-line splitting. If there are no blank lines but most non-empty
    lines look like standalone contacts (contain an email or phone), treat
    each line as its own block.
    """
    # Normalize line endings
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Try blank-line split first
    blocks = re.split(r"\n\s*\n", text)
    blocks = [b.strip() for b in blocks if b.strip()]

    if len(blocks) > 1:
        return blocks

    # Single block — check if individual lines are standalone contacts
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if len(lines) <= 1:
        return blocks  # truly just one entry

    # Heuristic: if >50% of lines contain email or phone, treat each as a contact
    contact_lines = sum(
        1 for l in lines
        if _EMAIL_RE.search(l) or _PHONE_RE.search(l)
    )
    if contact_lines > len(lines) * 0.75:
        return lines

    # Otherwise keep as single block
    return blocks


# ── Per-block field extraction ─────────────────────────────────────────────


def _extract_fields(block: str) -> list[ParsedField]:
    """Extract fields from a single text block using subtract-and-classify."""
    fields: list[ParsedField] = []
    remaining = block

    # 1. Emails (high confidence)
    matches, remaining = _extract_and_remove(_EMAIL_RE, remaining)
    for m in matches:
        fields.append(ParsedField(
            field_type="email",
            value=m.group().lower().strip(),
            confidence="high",
            original_fragment=m.group(),
        ))

    # 2. URLs (high confidence) — before phone, since URLs contain digits
    matches, remaining = _extract_and_remove(_URL_RE, remaining)
    for m in matches:
        fields.append(ParsedField(
            field_type="url",
            value=m.group().strip(),
            confidence="high",
            original_fragment=m.group(),
        ))

    # 3. Birthdays — extract before phones so dates don't interfere
    bday_matches = list(_BDAY_RE.finditer(remaining))
    for m in reversed(bday_matches):
        remaining = remaining[:m.start()] + " " + remaining[m.end():]
    for m in bday_matches:
        day, month, year = m.group(1), m.group(2), m.group(3)
        bday_val = _format_bday(day, month, year)
        fields.append(ParsedField(
            field_type="bday",
            value=bday_val,
            confidence="high",
            original_fragment=m.group().strip(),
        ))

    # 4. Phones (high if valid, otherwise skip)
    matches, remaining_after = _extract_and_remove(_PHONE_RE, remaining)
    valid_phones = []
    for m in matches:
        e164 = _validate_phone(m.group())
        if e164:
            valid_phones.append((m, e164))
    # Only remove valid phone matches from remaining text
    if valid_phones:
        remaining = remaining_after if len(valid_phones) == len(matches) else remaining
        if len(valid_phones) != len(matches):
            # Re-do removal with only valid matches
            remaining_temp = block
            # Remove emails and URLs first
            _, remaining_temp = _extract_and_remove(_EMAIL_RE, remaining_temp)
            _, remaining_temp = _extract_and_remove(_URL_RE, remaining_temp)
            for m, _ in valid_phones:
                remaining_temp = remaining_temp[:m.start()] + " " + remaining_temp[m.end():]
            remaining = remaining_temp
    for m, e164 in valid_phones:
        fields.append(ParsedField(
            field_type="tel",
            value=e164,
            confidence="high",
            original_fragment=m.group().strip(),
        ))

    # 5. Strip phone labels (Mobil:, Tel:, Festnetz:, etc.) from remaining text
    remaining = _PHONE_LABEL_RE.sub(" ", remaining)

    # 6. Addresses — PLZ + street
    plz_matches = list(_PLZ_RE.finditer(remaining))
    street_matches = list(_STREET_RE.finditer(remaining))

    if plz_matches or street_matches:
        # Build address from components
        addr_parts = []
        fragments = []

        for sm in street_matches:
            addr_parts.append(sm.group().strip())
            fragments.append(sm.group())

        for pm in plz_matches:
            addr_parts.append(pm.group().strip())
            fragments.append(pm.group())

        if addr_parts:
            # Remove matched address components from remaining
            for sm in reversed(street_matches):
                remaining = remaining[:sm.start()] + " " + remaining[sm.end():]
            for pm in reversed(plz_matches):
                remaining = remaining[:pm.start()] + " " + remaining[pm.end():]

            addr_value = ", ".join(addr_parts)
            confidence = "medium" if (plz_matches and street_matches) else "low"
            fields.append(ParsedField(
                field_type="adr",
                value=addr_value,
                confidence=confidence,
                original_fragment="; ".join(fragments),
            ))

    # 7. Organization
    matches, remaining = _extract_and_remove(_ORG_RE, remaining)
    for m in matches:
        org_val = m.group().strip()
        if len(org_val) >= 3:  # skip tiny false positives
            fields.append(ParsedField(
                field_type="org",
                value=org_val,
                confidence="medium",
                original_fragment=m.group(),
            ))

    # 8. Name — whatever meaningful text remains
    # Clean up remaining text
    leftover = remaining.strip()
    # Remove stray punctuation and extra whitespace
    leftover = re.sub(r"[,;:\-/]+$", "", leftover)
    leftover = re.sub(r"^[,;:\-/]+", "", leftover)
    leftover = re.sub(r"\s+", " ", leftover).strip()
    # Remove isolated single characters (stray separators)
    leftover = re.sub(r"\b[,;:\-/]\b", " ", leftover).strip()
    leftover = re.sub(r"\s+", " ", leftover).strip()

    # Check if leftover contains at least one letter (not just punctuation/symbols)
    if leftover and len(leftover) >= 2 and re.search(r"[a-zA-ZäöüéèàÄÖÜÉÈÀ]", leftover):
        # Determine confidence based on word count and structure
        words = leftover.split()
        if 2 <= len(words) <= 4:
            conf = "high"
        elif len(words) == 1:
            conf = "medium"
        else:
            conf = "low"

        fields.append(ParsedField(
            field_type="fn",
            value=leftover,
            confidence=conf,
            original_fragment=leftover,
        ))

    return fields


# ── Public API ─────────────────────────────────────────────────────────────


def parse_raw_text(text: str) -> list[ParsedContact]:
    """Parse unstructured text into a list of ParsedContact objects."""
    blocks = _split_into_blocks(text)
    contacts = []

    for block in blocks:
        fields = _extract_fields(block)
        if fields:  # skip empty blocks
            contacts.append(ParsedContact(
                raw_text=block,
                fields=fields,
            ))

    return contacts


def parse_raw_file(filepath: str) -> list[ParsedContact]:
    """Read a text file and parse it into contacts."""
    from schnabel.config import ENCODING_CHAIN

    for enc in ENCODING_CHAIN:
        try:
            with open(filepath, encoding=enc) as f:
                text = f.read()
            return parse_raw_text(text)
        except (UnicodeDecodeError, UnicodeError):
            continue

    # Last resort: read as latin-1 (never fails)
    with open(filepath, encoding="latin-1") as f:
        text = f.read()
    return parse_raw_text(text)


def parsed_to_contact(parsed: ParsedContact) -> Contact:
    """Convert a ParsedContact into a Contact model object."""
    contact = Contact()
    contact_fields = []

    for pf in parsed.fields:
        if pf.field_type == "fn":
            name_parts = _split_name(pf.value)
            contact.given_name = name_parts["given"]
            contact.family_name = name_parts["family"]
            contact.prefix = name_parts["prefix"]
            contact.fn = pf.value
        elif pf.field_type in ("email", "tel", "adr", "org", "url", "note", "bday"):
            contact_fields.append(ContactField(
                field_type=pf.field_type,
                field_value=pf.value,
            ))

    contact.fields = contact_fields

    # Build FN from name parts if not already set
    if not contact.fn and (contact.given_name or contact.family_name):
        contact.fn = f"{contact.given_name} {contact.family_name}".strip()

    return contact
