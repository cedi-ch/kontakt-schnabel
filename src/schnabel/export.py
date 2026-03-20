"""vCard 3.0 writer and export pipeline."""

import io
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

import phonenumbers

from schnabel.config import DEFAULT_PHONE_REGION
from schnabel.db import Database
from schnabel.model import Contact


def _fold_line(line: str, max_length: int = 75) -> str:
    """Fold a vCard line at max_length chars per RFC 2426."""
    if len(line.encode("utf-8")) <= max_length:
        return line

    result = []
    current = ""
    for char in line:
        test = current + char
        if len(test.encode("utf-8")) > max_length:
            result.append(current)
            current = " " + char  # continuation line starts with space
        else:
            current = test
    if current:
        result.append(current)
    return "\r\n".join(result)


def _format_phone(phone: str, region: str = DEFAULT_PHONE_REGION) -> str:
    """Format phone in localized format."""
    try:
        parsed = phonenumbers.parse(phone, region)
        if not phonenumbers.is_valid_number(parsed):
            return phone
        country = phonenumbers.region_code_for_number(parsed)
        if country == "CH":
            # Swiss format: 079 123 45 67
            national = phonenumbers.format_number(
                parsed, phonenumbers.PhoneNumberFormat.NATIONAL
            )
            return national
        else:
            # International format: +49 170 1234567
            return phonenumbers.format_number(
                parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL
            )
    except phonenumbers.NumberParseException:
        return phone


def _escape_vcard_value(value: str) -> str:
    """Escape special characters in vCard structured values (N, ADR).

    Escapes semicolons (component separator), commas (multi-value separator
    within components), backslashes, and newlines.
    """
    value = value.replace("\\", "\\\\")
    value = value.replace(";", "\\;")
    value = value.replace(",", "\\,")
    value = value.replace("\n", "\\n")
    return value


def _escape_n_component(value: str) -> str:
    """Escape a single N field component.

    RFC 2426 §3.1.2: N components are ;-separated. Within a component,
    commas separate multiple values (e.g. given names "Hans,Peter").
    So we escape semicolons but NOT commas.
    """
    value = value.replace("\\", "\\\\")
    value = value.replace(";", "\\;")
    value = value.replace("\n", "\\n")
    return value


def _escape_text_value(value: str) -> str:
    """Escape a free-text vCard value (FN, TITLE, NOTE, NICKNAME, ROLE, ORG).

    RFC 2426: these are single text values where semicolons and commas
    do NOT need escaping — only backslash and newline.
    """
    value = value.replace("\\", "\\\\")
    value = value.replace("\n", "\\n")
    return value


def _type_param(params: dict) -> str:
    """Build TYPE parameter string from field params."""
    if "TYPE" in params:
        types = params["TYPE"]
        if isinstance(types, list):
            return ";".join(f"TYPE={t}" for t in types)
        return f"TYPE={types}"
    return ""


import base64


def contact_to_vcard(contact: Contact) -> str:
    """Serialize a Contact to vCard 3.0 format."""
    lines = ["BEGIN:VCARD", "VERSION:3.0"]

    # FN
    fn = contact.fn or f"{contact.given_name} {contact.family_name}".strip()
    if fn:
        lines.append(f"FN:{_escape_text_value(fn)}")

    # N
    n_parts = [
        contact.family_name or "",
        contact.given_name or "",
        contact.additional_names or "",
        contact.prefix or "",
        contact.suffix or "",
    ]
    lines.append(f"N:{';'.join(_escape_n_component(p) for p in n_parts)}")

    # UID
    uid = contact.uid or str(uuid.uuid4())
    lines.append(f"UID:{uid}")

    # Fields
    for f in contact.fields:
        if f.field_value is None or not str(f.field_value).strip():
            continue
        tp = _type_param(f.field_params)
        prefix = f";{tp}" if tp else ""

        if f.field_type == "email":
            lines.append(f"EMAIL{prefix}:{f.field_value}")
        elif f.field_type == "tel":
            formatted = _format_phone(f.field_value)
            lines.append(f"TEL{prefix}:{formatted}")
        elif f.field_type == "adr":
            # Re-serialize address — stored as 7 semicolon-separated components
            # (PO Box;Extended;Street;City;Region;Code;Country) or legacy formats
            parts = f.field_value.split(";")
            if len(parts) < 5 and "," in f.field_value:
                # Legacy comma-separated format (5 parts: street,city,region,code,country)
                legacy = [p.strip() for p in f.field_value.split(",")]
                while len(legacy) < 5:
                    legacy.append("")
                # Convert to 7-component: prepend empty PO Box and Extended
                parts = ["", ""] + legacy[:5]
            while len(parts) < 7:
                parts.append("")
            escaped = [_escape_vcard_value(p) for p in parts[:7]]
            adr_val = ";".join(escaped)
            lines.append(f"ADR{prefix}:{adr_val}")
        elif f.field_type == "org":
            lines.append(f"ORG:{_escape_text_value(f.field_value)}")
        elif f.field_type == "title":
            lines.append(f"TITLE:{_escape_text_value(f.field_value)}")
        elif f.field_type == "note":
            lines.append(f"NOTE:{_escape_text_value(f.field_value)}")
        elif f.field_type == "url":
            lines.append(f"URL:{f.field_value}")
        elif f.field_type == "bday":
            lines.append(f"BDAY:{f.field_value}")
        elif f.field_type == "nickname":
            lines.append(f"NICKNAME:{_escape_text_value(f.field_value)}")
        elif f.field_type == "role":
            lines.append(f"ROLE:{_escape_text_value(f.field_value)}")
        elif f.field_type == "categories":
            pass  # handled after field loop

    # CATEGORIES — collect, case-insensitive dedup, emit single line
    cat_values = [f.field_value for f in contact.fields if f.field_type == "categories"]
    if cat_values:
        seen_cats: dict[str, str] = {}
        for cv in cat_values:
            key = cv.lower()
            if key not in seen_cats:
                seen_cats[key] = cv
        escaped_cats = [_escape_vcard_value(c) for c in seen_cats.values()]
        lines.append(f"CATEGORIES:{','.join(escaped_cats)}")

    # Photo
    for photo in contact.photos:
        fmt = photo.photo_format.upper()
        b64 = base64.b64encode(photo.photo_data).decode("ascii")
        lines.append(f"PHOTO;ENCODING=BASE64;TYPE={fmt}:{b64}")

    # REV
    rev = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines.append(f"REV:{rev}")

    lines.append("END:VCARD")

    # Fold long lines and join with CRLF
    folded = [_fold_line(line) for line in lines]
    return "\r\n".join(folded)


def export_contacts(db: Database, output_dir: Path, normalize_photos: bool = True,
                    max_lines: int = 0):
    """Export contacts to three VCF files: real, stubs, spam.

    If max_lines > 0, splits output into numbered chunks that each stay
    under the line limit (e.g. real_contacts_001.vcf, real_contacts_002.vcf).
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    categories = {
        "real": "real_contacts",
        "stub": "stubs",
        "spam": "spam",
    }

    counts = {}
    for category, basename in categories.items():
        contacts = db.get_contacts_by_category(category)
        contacts.sort(key=lambda c: (c.fn or "").lower())

        vcards = []
        for contact in contacts:
            if normalize_photos:
                _normalize_contact_photos(contact)
            vcards.append(contact_to_vcard(contact))

        if max_lines > 0 and vcards:
            _write_split(vcards, output_dir, basename, max_lines)
        else:
            content = "\r\n".join(vcards)
            if content:
                content += "\r\n"
            (output_dir / f"{basename}.vcf").write_text(content, encoding="utf-8")
        counts[category] = len(contacts)

    return counts


def export_by_category(db: Database, output_dir: Path, normalize_photos: bool = True,
                       max_lines: int = 0) -> dict[str, int]:
    """Export real contacts split by CATEGORIES into separate VCF files.

    Each category gets its own file: kontakte-{category}.vcf
    Contacts without categories go into kontakte-unsortiert.vcf
    Stubs and spam are still written to their own files (unchanged).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    counts = {}

    # Get all real contacts
    real_contacts = db.get_contacts_by_category("real")
    real_contacts.sort(key=lambda c: (c.fn or "").lower())

    # Group by category
    by_cat: dict[str, list[Contact]] = {}
    for contact in real_contacts:
        cats = contact.categories
        if not cats:
            by_cat.setdefault("unsortiert", []).append(contact)
        else:
            for cat in cats:
                by_cat.setdefault(cat, []).append(contact)

    # Write category files
    for cat_name, contacts in sorted(by_cat.items()):
        # Sanitize filename
        safe_name = re.sub(r"[^\w\s-]", "", cat_name).strip().replace(" ", "_").lower()
        if not safe_name:
            safe_name = "unsortiert"
        basename = f"kontakte-{safe_name}"

        vcards = []
        for contact in contacts:
            if normalize_photos:
                _normalize_contact_photos(contact)
            vcards.append(contact_to_vcard(contact))

        if max_lines > 0 and vcards:
            _write_split(vcards, output_dir, basename, max_lines)
        else:
            content = "\r\n".join(vcards)
            if content:
                content += "\r\n"
            (output_dir / f"{basename}.vcf").write_text(content, encoding="utf-8")
        counts[cat_name] = len(contacts)

    # Stubs and spam as usual
    for category, basename in [("stub", "stubs"), ("spam", "spam")]:
        contacts = db.get_contacts_by_category(category)
        contacts.sort(key=lambda c: (c.fn or "").lower())
        vcards = []
        for contact in contacts:
            if normalize_photos:
                _normalize_contact_photos(contact)
            vcards.append(contact_to_vcard(contact))
        if max_lines > 0 and vcards:
            _write_split(vcards, output_dir, basename, max_lines)
        else:
            content = "\r\n".join(vcards)
            if content:
                content += "\r\n"
            (output_dir / f"{basename}.vcf").write_text(content, encoding="utf-8")
        counts[f"({category})"] = len(contacts)

    return counts


def get_export_preview(db: Database) -> dict:
    """Build an export preview with counts per category.

    Returns a dict with:
      - real: total real contacts
      - real_with_photos: real contacts with photos
      - categories: dict of category_name → count
      - uncategorized: count of real contacts without categories
      - stubs: stub count
      - spam: spam count
    """
    preview = {}

    real_contacts = db.get_contacts_by_category("real")
    preview["real"] = len(real_contacts)
    preview["real_with_photos"] = sum(1 for c in real_contacts if c.photos)

    # Count by category
    cat_counts: dict[str, int] = {}
    uncategorized = 0
    for contact in real_contacts:
        cats = contact.categories
        if not cats:
            uncategorized += 1
        else:
            for cat in cats:
                cat_counts[cat] = cat_counts.get(cat, 0) + 1
    preview["categories"] = cat_counts
    preview["uncategorized"] = uncategorized

    stubs = db.get_contacts_by_category("stub")
    preview["stubs"] = len(stubs)

    spam = db.get_contacts_by_category("spam")
    preview["spam"] = len(spam)

    return preview


def _write_split(vcards: list[str], output_dir: Path, basename: str, max_lines: int):
    """Write vCards into numbered chunk files, each under max_lines."""
    chunk = []
    chunk_lines = 0
    chunk_num = 1

    for vcard in vcards:
        vcard_lines = vcard.count("\r\n") + 1
        if chunk and (chunk_lines + vcard_lines + 1) > max_lines:
            # Write current chunk
            content = "\r\n".join(chunk) + "\r\n"
            fname = f"{basename}_{chunk_num:03d}.vcf"
            (output_dir / fname).write_text(content, encoding="utf-8")
            chunk_num += 1
            chunk = []
            chunk_lines = 0

        chunk.append(vcard)
        chunk_lines += vcard_lines + 1  # +1 for separator line

    # Write final chunk
    if chunk:
        content = "\r\n".join(chunk) + "\r\n"
        if chunk_num == 1:
            # Only one chunk needed — use plain name
            fname = f"{basename}.vcf"
        else:
            fname = f"{basename}_{chunk_num:03d}.vcf"
        (output_dir / fname).write_text(content, encoding="utf-8")


def _normalize_contact_photos(contact: Contact):
    """Normalize photos in-place: EXIF rotate, resize, JPEG convert."""
    from schnabel.config import PHOTO_JPEG_QUALITY, PHOTO_MAX_SIZE

    normalized = []
    for photo in contact.photos:
        try:
            from PIL import Image, ImageOps
            img = Image.open(io.BytesIO(photo.photo_data))
            # EXIF auto-rotate
            img = ImageOps.exif_transpose(img)
            # RGBA → RGB
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            # Resize
            img.thumbnail((PHOTO_MAX_SIZE, PHOTO_MAX_SIZE), Image.LANCZOS)
            # Save as JPEG
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=PHOTO_JPEG_QUALITY)
            photo.photo_data = buf.getvalue()
            photo.photo_format = "JPEG"
            photo.width, photo.height = img.size
        except Exception:
            pass  # keep original if normalization fails
        normalized.append(photo)
    contact.photos = normalized


def extract_photos(db: Database, output_dir: Path) -> int:
    """Extract all contact photos to files. Returns count."""
    output_dir.mkdir(parents=True, exist_ok=True)
    contacts = db.get_contacts_by_category("real")
    count = 0

    for contact in contacts:
        if not contact.photos:
            continue
        # Build filename
        name = contact.fn or f"{contact.given_name}_{contact.family_name}"
        name = re.sub(r"[^\w\s-]", "", name).strip().replace(" ", "_")
        if not name:
            name = f"contact_{contact.id}"

        for i, photo in enumerate(contact.photos):
            ext = photo.photo_format.lower()
            if ext not in ("jpeg", "jpg", "png", "gif"):
                ext = "jpg"
            if ext == "jpeg":
                ext = "jpg"
            suffix = f"_{i+1}" if i > 0 else ""
            filename = f"{name}_{contact.id}{suffix}.{ext}"
            (output_dir / filename).write_bytes(photo.photo_data)
            count += 1

    return count
