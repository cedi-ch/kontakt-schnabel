"""vCard 3.0 writer and export pipeline."""

import io
import re
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
    """Escape special characters in vCard values."""
    value = value.replace("\\", "\\\\")
    value = value.replace(";", "\\;")
    value = value.replace(",", "\\,")
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
        lines.append(f"FN:{_escape_vcard_value(fn)}")

    # N
    n_parts = [
        contact.family_name or "",
        contact.given_name or "",
        contact.additional_names or "",
        contact.prefix or "",
        contact.suffix or "",
    ]
    lines.append(f"N:{';'.join(n_parts)}")

    # Fields
    for f in contact.fields:
        tp = _type_param(f.field_params)
        prefix = f";{tp}" if tp else ""

        if f.field_type == "email":
            lines.append(f"EMAIL{prefix}:{f.field_value}")
        elif f.field_type == "tel":
            formatted = _format_phone(f.field_value)
            lines.append(f"TEL{prefix}:{formatted}")
        elif f.field_type == "adr":
            # Re-serialize address
            parts = [p.strip() for p in f.field_value.split(",")]
            # ADR: PO;Ext;Street;City;Region;Code;Country
            while len(parts) < 5:
                parts.append("")
            adr_val = f";;{parts[0]};{parts[1]};{parts[2]};{parts[3]};{parts[4]}"
            lines.append(f"ADR{prefix}:{adr_val}")
        elif f.field_type == "org":
            lines.append(f"ORG:{_escape_vcard_value(f.field_value)}")
        elif f.field_type == "title":
            lines.append(f"TITLE:{_escape_vcard_value(f.field_value)}")
        elif f.field_type == "note":
            lines.append(f"NOTE:{_escape_vcard_value(f.field_value)}")
        elif f.field_type == "url":
            lines.append(f"URL:{f.field_value}")
        elif f.field_type == "bday":
            lines.append(f"BDAY:{f.field_value}")
        elif f.field_type == "nickname":
            lines.append(f"NICKNAME:{_escape_vcard_value(f.field_value)}")
        elif f.field_type == "role":
            lines.append(f"ROLE:{_escape_vcard_value(f.field_value)}")

    # Photo
    for photo in contact.photos:
        fmt = photo.photo_format.upper()
        b64 = base64.b64encode(photo.photo_data).decode("ascii")
        lines.append(f"PHOTO;ENCODING=b;TYPE={fmt}:{b64}")

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
            filename = f"{name}{suffix}.{ext}"
            (output_dir / filename).write_bytes(photo.photo_data)
            count += 1

    return count
