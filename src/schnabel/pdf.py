"""PDF phone list generator — one A4 landscape page per letter."""

import unicodedata
from collections import defaultdict
from pathlib import Path

from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, PageBreak, Spacer,
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

from schnabel.model import Contact
from schnabel.reader import parse_vcf_file


# Page dimensions
PAGE_W, PAGE_H = landscape(A4)
MARGIN = 15 * mm


def _sort_key_letter(contact: Contact) -> str:
    """Return the uppercase first letter of the surname for grouping.

    Non-ASCII letters are normalized (e.g. Ä→A, É→E).
    Names starting with digits go to '#', other non-alpha to '?'.
    """
    surname = contact.family_name.strip()
    if not surname:
        # Fall back to given name, then FN
        surname = contact.given_name.strip() or contact.fn.strip()
    if not surname:
        return "?"

    ch = surname[0].upper()
    # Normalize accented chars: Ä→A, É→E, etc.
    decomposed = unicodedata.normalize("NFD", ch)
    base = decomposed[0] if decomposed else ch

    if base.isalpha():
        return base
    if base.isdigit():
        return "#"
    return "?"


def _sort_key_name(contact: Contact) -> tuple[str, str]:
    """Sort key: (surname_lower, firstname_lower) for inter-letter sorting by surname,
    then intra-page sorting by firstname."""
    family = contact.family_name.strip().lower()
    given = contact.given_name.strip().lower()
    return (family, given)


def _display_name(contact: Contact) -> str:
    """Format name as 'Firstname Surname'."""
    given = contact.given_name.strip()
    family = contact.family_name.strip()
    if given and family:
        return f"{given} {family}"
    return contact.fn.strip() or given or family or "(kein Name)"


MAX_VALUES = 6  # Max phone/email entries per contact row
MAX_VALUE_LEN = 60  # Truncate individual values longer than this


def _clean_values(values: list[str]) -> list[str]:
    """Filter out bogus values (e.g. base64 photo data parsed as phone numbers)."""
    clean = []
    for v in values:
        v = v.strip()
        if not v:
            continue
        # Skip values that look like base64 data (no spaces, very long, alphanumeric)
        if len(v) > 200 and " " not in v:
            continue
        if len(v) > MAX_VALUE_LEN:
            v = v[:MAX_VALUE_LEN] + "…"
        clean.append(v)
    return clean


def _join_values(values: list[str]) -> str:
    """Join multiple values with line breaks for readability in PDF cells.

    Caps at MAX_VALUES to prevent rows taller than a page.
    XML-escapes values for use inside Paragraph markup.
    """
    if not values:
        return ""
    from xml.sax.saxutils import escape
    values = _clean_values(values)
    if not values:
        return ""
    truncated = values[:MAX_VALUES]
    parts = [escape(v) for v in truncated]
    if len(values) > MAX_VALUES:
        parts.append(f"<i>(+{len(values) - MAX_VALUES} weitere)</i>")
    return "<br/>".join(parts)


def generate_pdf(input_path: Path, output_path: Path, title: str | None = None) -> int:
    """Generate a PDF phone list from a VCF file.

    Returns the number of contacts included.
    """
    contacts, _encoding = parse_vcf_file(input_path)
    if not contacts:
        return 0

    # Filter out contacts with no useful info
    contacts = [c for c in contacts if c.fn.strip() or c.has_structured_name]

    # Sort by surname, then firstname
    contacts.sort(key=_sort_key_name)

    # Group by first letter of surname
    groups: dict[str, list[Contact]] = defaultdict(list)
    for c in contacts:
        letter = _sort_key_letter(c)
        groups[letter].append(c)

    # Sort groups: A-Z first, then '#', then '?'
    def group_order(key: str) -> tuple[int, str]:
        if key.isalpha():
            return (0, key)
        if key == "#":
            return (1, key)
        return (2, key)

    sorted_letters = sorted(groups.keys(), key=group_order)

    # Within each letter group, sort by firstname
    for letter in sorted_letters:
        groups[letter].sort(key=lambda c: c.given_name.strip().lower())

    # Build PDF
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=landscape(A4),
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN,
        bottomMargin=MARGIN,
        title=title or f"Telefonliste — {input_path.stem}",
    )

    styles = getSampleStyleSheet()

    # Custom styles
    letter_style = ParagraphStyle(
        "LetterHeader",
        parent=styles["Heading1"],
        fontSize=24,
        spaceAfter=4 * mm,
        textColor=colors.HexColor("#333333"),
    )
    cell_style = ParagraphStyle(
        "Cell",
        parent=styles["Normal"],
        fontSize=8,
        leading=10,
    )
    cell_style_bold = ParagraphStyle(
        "CellBold",
        parent=cell_style,
        fontName="Helvetica-Bold",
    )
    header_style = ParagraphStyle(
        "HeaderCell",
        parent=cell_style,
        fontName="Helvetica-Bold",
        fontSize=9,
        textColor=colors.white,
    )

    # Available width for the table
    avail_width = PAGE_W - 2 * MARGIN
    # Column widths: Name 30%, Phones 40%, Email 30%
    col_widths = [avail_width * 0.28, avail_width * 0.40, avail_width * 0.32]

    elements = []
    total_contacts = 0

    for i, letter in enumerate(sorted_letters):
        group = groups[letter]
        label = {"#": "0–9", "?": "Andere"}.get(letter, letter)

        # Letter heading
        elements.append(Paragraph(label, letter_style))

        # Table header
        header_row = [
            Paragraph("Name", header_style),
            Paragraph("Telefon", header_style),
            Paragraph("E-Mail", header_style),
        ]

        rows = [header_row]
        for c in group:
            from xml.sax.saxutils import escape
            name = escape(_display_name(c))
            phones = _join_values(c.phones)
            emails = _join_values(c.emails)
            rows.append([
                Paragraph(name, cell_style_bold),
                Paragraph(phones, cell_style),
                Paragraph(emails, cell_style),
            ])

        table = Table(rows, colWidths=col_widths, repeatRows=1)
        table.setStyle(TableStyle([
            # Header row
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4a4a4a")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 9),
            # Data rows
            ("FONTSIZE", (0, 1), (-1, -1), 8),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            # Alternating row colors
            *[
                ("BACKGROUND", (0, r), (-1, r), colors.HexColor("#f0f0f0"))
                for r in range(2, len(rows), 2)
            ],
            # Grid
            ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#333333")),
            ("LINEBELOW", (0, 1), (-1, -1), 0.25, colors.HexColor("#cccccc")),
        ]))

        elements.append(table)
        total_contacts += len(group)

        # Page break after each letter (except the last)
        if i < len(sorted_letters) - 1:
            elements.append(PageBreak())

    doc.build(elements)
    return total_contacts
