"""vCard parser with encoding fallback chain."""

import base64
import hashlib
import io
import re
from pathlib import Path

import chardet
import vobject

from schnabel.config import ENCODING_CHAIN
from schnabel.model import Contact, ContactField, Photo


def detect_encoding(raw_bytes: bytes) -> str:
    """Detect encoding using chardet, with fallback chain."""
    result = chardet.detect(raw_bytes)
    if result["encoding"] and result["confidence"] > 0.7:
        return result["encoding"].lower()
    return "utf-8"


def read_file_with_fallback(file_path: Path) -> tuple[str, str]:
    """Read a file trying multiple encodings. Returns (text, encoding_used)."""
    raw_bytes = file_path.read_bytes()
    if not raw_bytes:
        return "", "utf-8"

    # Try chardet first
    detected = detect_encoding(raw_bytes)
    chain = [detected] + [e for e in ENCODING_CHAIN if e != detected]

    for encoding in chain:
        try:
            text = raw_bytes.decode(encoding)
            # Verify by re-encoding — catches mojibake
            text.encode("utf-8")
            return text, encoding
        except (UnicodeDecodeError, UnicodeEncodeError):
            continue

    # Last resort: lossy decode
    return raw_bytes.decode("utf-8", errors="replace"), "utf-8-lossy"


def normalize_line_endings(text: str) -> str:
    """Normalize to LF for parsing (vobject handles both, but be safe)."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def split_vcards(text: str) -> list[str]:
    """Split a multi-vCard file into individual vCard strings.

    Handles malformed files where END:VCARD is missing — when a new
    BEGIN:VCARD is encountered while already inside a card, the previous
    card is auto-closed.
    """
    cards = []
    current = []
    in_card = False

    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.upper() == "BEGIN:VCARD":
            if in_card and current:
                # Auto-close previous unclosed card
                current.append("END:VCARD")
                cards.append("\n".join(current))
            in_card = True
            current = [line]
        elif stripped.upper() == "END:VCARD":
            if in_card:
                current.append(line)
                cards.append("\n".join(current))
                current = []
                in_card = False
        elif in_card:
            # Skip duplicate VERSION lines
            if stripped.upper().startswith("VERSION:") and any(
                l.strip().upper().startswith("VERSION:") for l in current
            ):
                continue
            current.append(line)

    # Handle last unclosed card
    if in_card and current:
        current.append("END:VCARD")
        cards.append("\n".join(current))

    return cards


def _extract_photo(vcard) -> Photo | None:
    """Extract photo from a vobject vCard component."""
    if not hasattr(vcard, "photo"):
        return None

    try:
        photo = vcard.photo
        # Get the raw value
        value = photo.value

        # Determine format
        params = getattr(photo, "params", {})
        photo_format = "JPEG"  # default
        if "TYPE" in params:
            type_val = params["TYPE"]
            if isinstance(type_val, list):
                type_val = type_val[0]
            type_val = type_val.upper()
            if "PNG" in type_val:
                photo_format = "PNG"
            elif "GIF" in type_val:
                photo_format = "GIF"

        # Get binary data
        if isinstance(value, str):
            # Might be base64 encoded
            encoding = params.get("ENCODING", [""])[0] if "ENCODING" in params else ""
            if isinstance(encoding, list):
                encoding = encoding[0]
            if encoding.upper() in ("B", "BASE64") or not isinstance(value, bytes):
                try:
                    photo_data = base64.b64decode(value)
                except Exception:
                    photo_data = value.encode("latin-1")
            else:
                photo_data = value.encode("latin-1")
        elif isinstance(value, bytes):
            photo_data = value
        else:
            return None

        if len(photo_data) < 100:  # too small to be a real photo
            return None

        byte_hash = hashlib.sha256(photo_data).hexdigest()

        # Try to get dimensions
        width, height = 0, 0
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(photo_data))
            width, height = img.size
            if img.format:
                photo_format = img.format.upper()
        except Exception:
            pass

        return Photo(
            photo_data=photo_data,
            photo_format=photo_format,
            byte_hash=byte_hash,
            width=width,
            height=height,
        )
    except Exception:
        return None


def _str_field(value) -> str:
    """Convert a vobject name field to string, handling lists."""
    if isinstance(value, list):
        return " ".join(str(v).strip() for v in value if str(v).strip())
    return str(value).strip() if value else ""


def _fallback_parse(vcard_text: str, source_file: str) -> Contact | None:
    """Regex-based fallback parser for vCards that vobject can't handle."""
    contact = Contact(source_file=source_file, raw_vcard=vcard_text)

    for line in vcard_text.split("\n"):
        line = line.strip()
        if not line or line.upper() in ("BEGIN:VCARD", "END:VCARD"):
            continue

        if line.upper().startswith("FN:"):
            contact.fn = line[3:].strip()
        elif line.upper().startswith("N:") or line.upper().startswith("N;"):
            # Parse N:family;given;additional;prefix;suffix
            val = line.split(":", 1)[1] if ":" in line else ""
            parts = val.split(";")
            if len(parts) >= 1:
                contact.family_name = parts[0].strip()
            if len(parts) >= 2:
                contact.given_name = parts[1].strip()
            if len(parts) >= 3:
                contact.additional_names = parts[2].strip()
            if len(parts) >= 4:
                contact.prefix = parts[3].strip()
            if len(parts) >= 5:
                contact.suffix = parts[4].strip()
        elif "EMAIL" in line.upper() and ":" in line:
            val = line.split(":", 1)[1].strip()
            if val and "@" in val:
                contact.fields.append(ContactField("email", val))
        elif "TEL" in line.upper() and ":" in line:
            val = line.split(":", 1)[1].strip()
            if val:
                contact.fields.append(ContactField("tel", val))
        elif line.upper().startswith("ORG:"):
            val = line[4:].strip()
            if val:
                contact.fields.append(ContactField("org", val))
        elif line.upper().startswith("TITLE:"):
            val = line[6:].strip()
            if val:
                contact.fields.append(ContactField("title", val))
        elif line.upper().startswith("BDAY"):
            val = line.split(":", 1)[1].strip() if ":" in line else ""
            if val:
                contact.fields.append(ContactField("bday", val))
        elif line.upper().startswith("URL:"):
            val = line[4:].strip()
            if val:
                contact.fields.append(ContactField("url", val))
        # Photos handled via PHOTO;ENCODING=b — skip in fallback (too messy)

    # Construct FN from N if missing
    if not contact.fn and (contact.given_name or contact.family_name):
        contact.fn = f"{contact.given_name} {contact.family_name}".strip()

    # Only return if we got something useful
    if contact.fn or contact.emails or contact.phones:
        return contact
    return None


def parse_vcard(vcard_text: str, source_file: str = "") -> Contact | None:
    """Parse a single vCard text into a Contact object."""
    try:
        vcard = vobject.readOne(vcard_text)
    except Exception:
        return _fallback_parse(vcard_text, source_file)

    contact = Contact(source_file=source_file, raw_vcard=vcard_text)

    # FN (formatted name)
    if hasattr(vcard, "fn"):
        contact.fn = vcard.fn.value.strip()

    # N (structured name)
    if hasattr(vcard, "n"):
        n = vcard.n.value
        contact.family_name = _str_field(getattr(n, "family", ""))
        contact.given_name = _str_field(getattr(n, "given", ""))
        contact.additional_names = _str_field(getattr(n, "additional", ""))
        contact.prefix = _str_field(getattr(n, "prefix", ""))
        contact.suffix = _str_field(getattr(n, "suffix", ""))

    # If no FN, construct from N
    if not contact.fn and (contact.given_name or contact.family_name):
        parts = [contact.prefix, contact.given_name, contact.additional_names,
                 contact.family_name, contact.suffix]
        contact.fn = " ".join(p for p in parts if p)

    # EMAIL
    if hasattr(vcard, "email_list"):
        for email in vcard.email_list:
            params = {}
            if hasattr(email, "params") and "TYPE" in email.params:
                params["TYPE"] = email.params["TYPE"]
            value = email.value.strip()
            if value:
                contact.fields.append(ContactField("email", value, params))

    # TEL
    if hasattr(vcard, "tel_list"):
        for tel in vcard.tel_list:
            params = {}
            if hasattr(tel, "params") and "TYPE" in tel.params:
                params["TYPE"] = tel.params["TYPE"]
            value = tel.value.strip()
            if value:
                contact.fields.append(ContactField("tel", value, params))

    # ADR
    if hasattr(vcard, "adr_list"):
        for adr in vcard.adr_list:
            params = {}
            if hasattr(adr, "params") and "TYPE" in adr.params:
                params["TYPE"] = adr.params["TYPE"]
            # Serialize address components
            a = adr.value
            parts = [
                _str_field(getattr(a, "street", "")),
                _str_field(getattr(a, "city", "")),
                _str_field(getattr(a, "region", "")),
                _str_field(getattr(a, "code", "")),
                _str_field(getattr(a, "country", "")),
            ]
            value = ", ".join(p for p in parts if p)
            if value:
                contact.fields.append(ContactField("adr", value, params))

    # ORG
    if hasattr(vcard, "org"):
        org_val = vcard.org.value
        if isinstance(org_val, list):
            org_str = ";".join(org_val).strip(";").strip()
        else:
            org_str = str(org_val).strip()
        if org_str:
            contact.fields.append(ContactField("org", org_str))

    # TITLE
    if hasattr(vcard, "title"):
        title = vcard.title.value.strip()
        if title:
            contact.fields.append(ContactField("title", title))

    # NOTE
    if hasattr(vcard, "note"):
        note = vcard.note.value.strip()
        if note:
            contact.fields.append(ContactField("note", note))

    # URL
    if hasattr(vcard, "url"):
        url = vcard.url.value.strip()
        if url:
            contact.fields.append(ContactField("url", url))

    # BDAY
    if hasattr(vcard, "bday"):
        bday = vcard.bday.value
        if isinstance(bday, str):
            bday_str = bday.strip()
        else:
            bday_str = str(bday)
        if bday_str:
            contact.fields.append(ContactField("bday", bday_str))

    # PHOTO
    photo = _extract_photo(vcard)
    if photo:
        contact.photos.append(photo)

    return contact


def parse_vcf_file(file_path: Path) -> tuple[list[Contact], str]:
    """Parse a VCF file into a list of Contacts. Returns (contacts, encoding_used)."""
    text, encoding = read_file_with_fallback(file_path)
    if not text.strip():
        return [], encoding

    text = normalize_line_endings(text)
    vcard_texts = split_vcards(text)

    contacts = []
    for vcard_text in vcard_texts:
        contact = parse_vcard(vcard_text, source_file=str(file_path))
        if contact:
            contacts.append(contact)

    return contacts, encoding


def file_md5(file_path: Path) -> str:
    """Calculate MD5 hash of a file."""
    return hashlib.md5(file_path.read_bytes()).hexdigest()
