"""Birthday calendar export: ICS generation with category-filtered contacts."""

import re
import time
from dataclasses import dataclass, field
from datetime import date, timedelta

import readchar
from rich.console import Console
from rich.table import Table
from rich.text import Text

from schnabel.db import Database
from schnabel.model import Contact

console = Console()

# Reuse key mapping pattern from cattui
_RESERVED_KEYS = {"n", "q"}
_CAT_KEYS = [c for c in
             [str(i) for i in range(10)] + [chr(o) for o in range(ord("a"), ord("z") + 1)]
             if c not in _RESERVED_KEYS]


def _category_key(idx: int) -> str:
    if idx < len(_CAT_KEYS):
        return _CAT_KEYS[idx]
    return "?"


def _key_to_index(key: str, max_idx: int) -> int | None:
    if key in _RESERVED_KEYS:
        return None
    try:
        idx = _CAT_KEYS.index(key)
    except ValueError:
        return None
    if 0 <= idx <= max_idx:
        return idx
    return None


# -- BDAY parsing --

def parse_bday(bday_str: str) -> tuple[int | None, int, int] | None:
    """Parse a BDAY value into (year_or_None, month, day). Returns None if unparseable."""
    bday_str = bday_str.strip()
    # --MM-DD (no year)
    m = re.match(r"^--(\d{2})-(\d{2})$", bday_str)
    if m:
        return (None, int(m.group(1)), int(m.group(2)))
    # YYYY-MM-DD (possibly with trailing time)
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", bday_str)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    # YYYYMMDD compact
    m = re.match(r"^(\d{4})(\d{2})(\d{2})$", bday_str)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None


def calculate_age(birth_year: int, event_year: int) -> int:
    return event_year - birth_year


# -- Reminder config --

@dataclass
class ReminderConfig:
    triggers: list[str] = field(default_factory=lambda: ["PT6H"])
    alarm_time: str = "06:00"


def run_reminder_config_tui() -> ReminderConfig:
    """Prompt for reminder settings."""
    config = ReminderConfig()

    console.print("\n[bold cyan]Erinnerungen konfigurieren[/bold cyan]\n")
    console.print(f"  Aktuell: [bold]Am Geburtstag, 06:00[/bold]\n")
    console.print("  [green]Enter[/green]=Standard übernehmen  "
                  "[green]c[/green]=anpassen  "
                  "[dim]q[/dim]=keine Erinnerung")

    key = readchar.readchar()

    if key == "q":
        config.triggers = []
        console.print("  [dim]Keine Erinnerungen.[/dim]")
        return config

    if key in ("\r", "\n", " "):
        console.print("  [green]Standard: Am Geburtstag, 06:00[/green]")
        return config

    if key == "c":
        config.triggers = []
        while True:
            console.print("\n  Erinnerung hinzufügen:")
            console.print("  [green]1[/green]=Tage vorher  "
                          "[green]2[/green]=Stunden vorher  "
                          "[green]Enter[/green]=fertig")

            k = readchar.readchar()

            if k in ("\r", "\n", " "):
                break

            if k == "1":
                console.print("  Anzahl Tage: ", end="")
                try:
                    days = input().strip()
                    d = int(days)
                    if d > 0:
                        config.triggers.append(f"-P{d}D")
                        console.print(f"  [green]✓ {d} Tag(e) vorher[/green]")
                except (ValueError, EOFError, KeyboardInterrupt):
                    console.print("  [red]Ungültig.[/red]")

            elif k == "2":
                console.print("  Anzahl Stunden: ", end="")
                try:
                    hours = input().strip()
                    h = int(hours)
                    if h > 0:
                        config.triggers.append(f"-PT{h}H")
                        console.print(f"  [green]✓ {h} Stunde(n) vorher[/green]")
                except (ValueError, EOFError, KeyboardInterrupt):
                    console.print("  [red]Ungültig.[/red]")

        console.print("  Uhrzeit der Erinnerung (HH:MM): ", end="")
        try:
            t = input().strip()
            if re.match(r"^\d{2}:\d{2}$", t):
                config.alarm_time = t
        except (EOFError, KeyboardInterrupt):
            pass

        if not config.triggers:
            config.triggers = ["PT6H"]
            console.print("  [dim]Keine Eingabe — Standard: Am Geburtstag, 06:00[/dim]")

        console.print(f"  [green]Erinnerungen: {', '.join(config.triggers)} um {config.alarm_time}[/green]")

    return config


# -- Category selection TUI --

def run_category_selection_tui(db: Database) -> tuple[set[str], bool] | None:
    """Interactive category selection for birthday export.

    Returns (selected_categories, include_all) or None if aborted.
    include_all=True means all contacts including uncategorized.
    """
    all_categories = db.get_all_category_values()
    breakdown = db.get_category_breakdown()

    # Count contacts without categories that have BDAY
    all_contacts = db.get_contacts_by_category("real")
    uncategorized_with_bday = sum(
        1 for c in all_contacts
        if c.bdays and not c.categories
    )
    total_with_bday = sum(1 for c in all_contacts if c.bdays)

    selected: set[str] = set()
    select_all = False

    while True:
        console.clear()

        header = Text()
        header.append("schnabel birthdays", style="bold cyan")
        header.append(" — Kategorien auswählen")
        console.print(header)
        console.print("─" * console.width)

        # Select all option
        if select_all:
            console.print(f"  [bold green][*] ✓ ALLE ({total_with_bday} mit Geburtstag)[/bold green]")
        else:
            console.print(f"  [dim][*]   ALLE ({total_with_bday} mit Geburtstag)[/dim]")

        console.print()

        # Category list
        for i, cat in enumerate(all_categories):
            key = _category_key(i)
            count = breakdown.get(cat, 0)
            if select_all or cat in selected:
                console.print(f"  [bold green][{key}] ✓ {cat} ({count})[/bold green]")
            else:
                console.print(f"  [dim][{key}]   {cat} ({count})[/dim]")

        # Uncategorized
        if uncategorized_with_bday > 0:
            if select_all:
                console.print(f"\n  [bold green]  + {uncategorized_with_bday} ohne Kategorie[/bold green]")
            else:
                console.print(f"\n  [dim]  + {uncategorized_with_bday} ohne Kategorie (nur mit *)[/dim]")

        console.print("\n" + "─" * console.width)
        line = Text()
        line.append("  *", style="bold green")
        line.append("=alle  ")
        max_key = _category_key(len(all_categories) - 1) if all_categories else "?"
        line.append(f"0-{max_key}", style="bold green")
        line.append("=toggle  ")
        line.append("Enter", style="bold cyan")
        line.append("=weiter  ")
        line.append("q", style="dim")
        line.append("=abbrechen")
        console.print(line)

        key = readchar.readchar()

        if key == "q":
            return None

        if key in ("\r", "\n", " "):
            if not select_all and not selected:
                console.print("  [red]Keine Kategorie ausgewählt.[/red]")
                time.sleep(0.5)
                continue
            return (selected, select_all)

        if key == "*":
            select_all = not select_all
            if select_all:
                selected.clear()
            continue

        cat_idx = _key_to_index(key, len(all_categories) - 1)
        if cat_idx is not None and cat_idx < len(all_categories):
            cat = all_categories[cat_idx]
            select_all = False
            if cat in selected:
                selected.discard(cat)
            else:
                selected.add(cat)


# -- Contact filtering --

def get_birthday_contacts(db: Database, selected_categories: set[str],
                          include_all: bool = False) -> list[tuple[Contact, int | None, int, int]]:
    """Get real contacts with BDAY, filtered by categories.

    Returns list of (contact, year_or_None, month, day).
    """
    contacts = db.get_contacts_by_category("real")
    result = []

    for c in contacts:
        if not c.bdays:
            continue

        parsed = parse_bday(c.bdays[0])
        if not parsed:
            continue

        if include_all:
            result.append((c, *parsed))
            continue

        contact_cats = set(c.categories)
        if contact_cats & selected_categories:
            result.append((c, *parsed))

    result.sort(key=lambda x: (x[2], x[3], x[0].fn))  # sort by month, day, name
    return result


def get_missing_birthday_contacts(db: Database, selected_categories: set[str],
                                  include_all: bool = False) -> list[Contact]:
    """Get real contacts WITHOUT BDAY, filtered by categories."""
    contacts = db.get_contacts_by_category("real")
    result = []

    for c in contacts:
        if c.bdays:
            continue

        if include_all:
            result.append(c)
            continue

        contact_cats = set(c.categories)
        if contact_cats & selected_categories:
            result.append(c)

    result.sort(key=lambda x: x.fn)
    return result


# -- Preview --

def print_birthday_preview(entries: list[tuple[Contact, int | None, int, int]]):
    """Print a Rich table preview of contacts to export."""
    current_year = date.today().year
    table = Table(title="[bold cyan]Geburtstage[/bold cyan]", border_style="cyan")
    table.add_column("Name", style="bold")
    table.add_column("Datum", justify="center")
    table.add_column("Alter", justify="right")
    table.add_column("Kategorien", style="dim")

    for contact, year, month, day in entries:
        date_str = f"{day:02d}.{month:02d}"
        if year:
            date_str += f".{year}"
            age = str(calculate_age(year, current_year))
        else:
            age = "-"
        cats = ", ".join(contact.categories) if contact.categories else "-"
        table.add_row(contact.fn, date_str, age, cats)

    table.add_row("[bold]TOTAL[/bold]", f"[bold]{len(entries)}[/bold]", "", "")
    console.print(table)


def print_missing_preview(contacts: list[Contact]):
    """Print contacts without birthdays."""
    table = Table(title="[bold yellow]Kontakte ohne Geburtstag[/bold yellow]",
                  border_style="yellow")
    table.add_column("Name", style="bold")
    table.add_column("Email", style="dim")
    table.add_column("Kategorien", style="dim")

    for c in contacts:
        email = c.emails[0] if c.emails else "-"
        cats = ", ".join(c.categories) if c.categories else "-"
        table.add_row(c.fn, email, cats)

    table.add_row("[bold]TOTAL[/bold]", f"[bold]{len(contacts)}[/bold]", "")
    console.print(table)


# -- ICS generation --

def _fold_line(line: str) -> str:
    """Fold a content line per RFC 5545 (max 75 octets)."""
    encoded = line.encode("utf-8")
    if len(encoded) <= 75:
        return line
    parts = []
    while len(encoded) > 75:
        # Find a safe split point (don't break multi-byte chars)
        cut = 75 if not parts else 74  # subsequent lines have leading space
        while cut > 0 and (encoded[cut] & 0xC0) == 0x80:
            cut -= 1
        parts.append(encoded[:cut].decode("utf-8"))
        encoded = encoded[cut:]
    if encoded:
        parts.append(encoded.decode("utf-8"))
    return "\r\n ".join(parts)


def _escape_ics(text: str) -> str:
    """Escape text for ICS: backslash, semicolons, commas, newlines."""
    text = text.replace("\\", "\\\\")
    text = text.replace(";", "\\;")
    text = text.replace(",", "\\,")
    text = text.replace("\n", "\\n")
    return text


def generate_ics(entries: list[tuple[Contact, int | None, int, int]],
                 years: int = 100,
                 reminders: ReminderConfig | None = None) -> str:
    """Generate a VCALENDAR string with birthday events."""
    current_year = date.today().year
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//kontakt-schnabel//Birthdays//DE",
        "X-WR-CALNAME:Geburtstage",
    ]

    for contact, birth_year, month, day in entries:
        for event_year in range(current_year, current_year + years):
            # Skip invalid dates (Feb 29 in non-leap years)
            try:
                event_date = date(event_year, month, day)
            except ValueError:
                continue

            next_day = event_date + timedelta(days=1)

            # Summary with age
            fn = contact.fn or "(kein Name)"
            if birth_year:
                age = calculate_age(birth_year, event_year)
                summary = f"🎂 {fn} ({age})"
                description = f"{fn} wird {age}"
            else:
                summary = f"🎂 {fn}"
                description = f"Geburtstag {fn}"

            uid = f"schnabel-bday-{contact.id}-{event_year}@kontakt-schnabel"

            lines.append("BEGIN:VEVENT")
            lines.append(f"UID:{uid}")
            lines.append(f"DTSTART;VALUE=DATE:{event_date.strftime('%Y%m%d')}")
            lines.append(f"DTEND;VALUE=DATE:{next_day.strftime('%Y%m%d')}")
            lines.append(_fold_line(f"SUMMARY:{_escape_ics(summary)}"))
            lines.append(_fold_line(f"DESCRIPTION:{_escape_ics(description)}"))
            lines.append("TRANSP:TRANSPARENT")

            if contact.categories:
                cats = ",".join(_escape_ics(c) for c in contact.categories)
                lines.append(_fold_line(f"CATEGORIES:{cats}"))

            # Reminders
            if reminders and reminders.triggers:
                for trigger in reminders.triggers:
                    lines.append("BEGIN:VALARM")
                    lines.append(f"TRIGGER:{trigger}")
                    lines.append("ACTION:DISPLAY")
                    lines.append(_fold_line(f"DESCRIPTION:{_escape_ics(summary)}"))
                    lines.append("END:VALARM")

            lines.append("END:VEVENT")

    lines.append("END:VCALENDAR")

    return "\r\n".join(lines) + "\r\n"
