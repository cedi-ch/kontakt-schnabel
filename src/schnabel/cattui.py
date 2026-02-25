"""TUI for interactive category assignment to contacts."""

import time

import readchar
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from schnabel.db import Database
from schnabel.model import ContactField

console = Console()


def _category_key(idx: int) -> str:
    """Map index 0-35 to key character: 0-9 then a-z."""
    if idx < 10:
        return str(idx)
    if idx < 36:
        return chr(ord("a") + idx - 10)
    return "?"


def _key_to_index(key: str, max_idx: int) -> int | None:
    """Map key character to category index. Returns None if invalid."""
    if key.isdigit():
        idx = int(key)
    elif key.isalpha() and key.islower():
        idx = ord(key) - ord("a") + 10
    else:
        return None
    if 0 <= idx <= max_idx:
        return idx
    return None


def _truncate(s: str, maxlen: int = 40) -> str:
    if len(s) <= maxlen:
        return s
    return s[: maxlen - 1] + "\u2026"


def _render_contact(contact, idx: int, total: int, changed: int,
                    all_categories: list[str], active_cats: set[str]):
    """Render the categorize TUI screen."""
    console.clear()

    # Header
    header = Text()
    header.append("schnabel categorize", style="bold cyan")
    header.append(f" \u2014 {total} Kontakte")
    header.append("              ", style="dim")
    header.append("[q]uit [?]hilfe", style="dim")
    console.print(header)
    console.print("\u2500" * console.width)

    # Progress
    info = Text()
    info.append(f"  Kontakt {idx + 1}/{total}", style="bold")
    info.append(f"  ({changed} ge\u00e4ndert)")
    console.print(info)
    console.print("\u2500" * console.width)

    # Two-column layout
    left_lines: list[tuple[str, str]] = []
    right_lines: list[str] = []

    # Left: contact details
    left_lines.append(("FN:", contact.fn or "(kein Name)"))

    field_labels = {
        "email": "EMAIL", "tel": "TEL", "adr": "ADR", "org": "ORG",
        "url": "URL", "note": "NOTE", "bday": "BDAY", "title": "TITLE",
        "nickname": "NICK", "role": "ROLE",
    }

    for f in contact.fields:
        if f.field_type == "categories":
            continue  # shown on the right
        label = field_labels.get(f.field_type, f.field_type.upper())
        left_lines.append((label + ":", f.field_value))

    if contact.photos:
        p = contact.photos[0]
        left_lines.append(("PHOTO:", f"[{p.photo_format} {p.width}\u00d7{p.height}]"))

    # Right: category checklist
    right_lines.append("Kategorien:")
    right_lines.append("")
    for i, cat in enumerate(all_categories):
        key = _category_key(i)
        checked = "\u2611" if cat in active_cats else "\u2610"
        right_lines.append(f"[{key}] {checked} {cat}")

    # Render columns
    col_width = max(console.width // 2 - 2, 30)
    max_rows = max(len(left_lines), len(right_lines))

    for row in range(max_rows):
        line = Text()

        if row < len(left_lines):
            label, value = left_lines[row]
            left_str = f"  {label:<10}{_truncate(value, col_width - 12)}"
        else:
            left_str = ""
        line.append(f"{left_str:<{col_width}}")

        line.append("\u2502 ", style="dim")

        if row < len(right_lines):
            rtext = right_lines[row]
            if row == 0:
                line.append(rtext, style="bold")
            else:
                line.append(rtext)

        console.print(line)

    console.print("\u2500" * console.width)

    # Action bar
    max_key = _category_key(len(all_categories) - 1) if all_categories else "?"
    keys_range = f"0-{max_key}" if all_categories else "(keine)"

    line1 = Text()
    line1.append(f" {keys_range}", style="bold green")
    line1.append("=toggle  ")
    line1.append("+", style="bold green")
    line1.append("=neue Kategorie  ")
    line1.append("n", style="bold cyan")
    line1.append("/SPACE=weiter  ")
    line1.append("b", style="bold cyan")
    line1.append("=zur\u00fcck  ")
    line1.append("q", style="dim")
    line1.append("=quit")
    console.print(line1)


def _save_categories(db: Database, cid: int, contact, desired_set: set[str]):
    """Sync category fields for a contact to match desired_set."""
    current_cats = {f.field_value for f in contact.fields if f.field_type == "categories"}
    if current_cats == desired_set:
        return False  # no change

    # Remove all existing category fields
    for f in contact.fields:
        if f.field_type == "categories" and f.id is not None:
            db.delete_contact_field(f.id)

    # Add desired categories
    for cat in sorted(desired_set):
        db.add_contact_field(cid, ContactField(field_type="categories", field_value=cat))

    db.commit()
    return True


def _show_help():
    """Show help screen."""
    console.clear()
    console.print(Panel(
        "[bold]Tastaturk\u00fcrzel[/bold]\n\n"
        "[green]0-9, a-z[/green]  Kategorie ein/ausschalten (toggle)\n"
        "[green]+[/green]  neue Kategorie hinzuf\u00fcgen\n"
        "[cyan]n / SPACE[/cyan]  weiter zum n\u00e4chsten Kontakt (speichert)\n"
        "[cyan]b[/cyan]  zur\u00fcck zum vorherigen Kontakt (speichert)\n"
        "[dim]q[/dim]  beenden (speichert aktuellen Kontakt)\n\n"
        "\u00c4nderungen werden beim Navigieren (n/b/q) gespeichert.\n\n"
        "Beliebige Taste zum Fortfahren...",
        title="Hilfe",
    ))
    readchar.readchar()


def run_categorize_tui(db: Database, uncategorized_only: bool = False):
    """Run the interactive category assignment TUI."""
    contact_ids = db.get_categorized_contact_ids(uncategorized_only=uncategorized_only)
    if not contact_ids:
        if uncategorized_only:
            console.print("[green]Alle Kontakte haben bereits Kategorien.[/green]")
        else:
            console.print("[yellow]Keine 'real'-Kontakte in der Datenbank.[/yellow]")
        return

    # Load all known categories
    all_categories = db.get_all_category_values()

    total = len(contact_ids)
    idx = 0
    changed_count = 0

    while 0 <= idx < total:
        cid = contact_ids[idx]
        contact = db.get_contact(cid)
        if not contact:
            idx += 1
            continue

        # Current categories for this contact
        active_cats = {f.field_value for f in contact.fields if f.field_type == "categories"}

        # Inner loop: stay on this contact until navigation
        while True:
            _render_contact(contact, idx, total, changed_count,
                            all_categories, active_cats)

            key = readchar.readchar()

            if key == "q":
                # Save and quit
                if _save_categories(db, cid, contact, active_cats):
                    changed_count += 1
                console.print(f"\n[bold green]{changed_count} Kontakte ge\u00e4ndert.[/bold green]")
                return

            elif key in ("n", " ", "\r"):
                # Save and advance
                if _save_categories(db, cid, contact, active_cats):
                    changed_count += 1
                idx += 1
                break

            elif key == "b":
                # Save and go back
                if _save_categories(db, cid, contact, active_cats):
                    changed_count += 1
                if idx > 0:
                    idx -= 1
                else:
                    console.print("\n  [dim]Erster Kontakt.[/dim]")
                    time.sleep(0.5)
                break

            elif key == "+":
                # Add new category
                console.print("\n  Neue Kategorie: ", end="")
                try:
                    new_cat = input().strip()
                except (EOFError, KeyboardInterrupt):
                    continue
                if new_cat:
                    # Case-insensitive check for existing
                    existing = {c.lower(): c for c in all_categories}
                    if new_cat.lower() in existing:
                        # Use existing casing
                        actual = existing[new_cat.lower()]
                        active_cats.add(actual)
                        console.print(f"  [yellow]Existiert bereits: {actual}[/yellow]")
                    else:
                        all_categories.append(new_cat)
                        all_categories.sort(key=str.lower)
                        active_cats.add(new_cat)
                        console.print(f"  [green]{new_cat} hinzugef\u00fcgt.[/green]")
                    time.sleep(0.5)
                # stay in inner loop -> re-render

            elif key == "?":
                _show_help()
                # stay in inner loop -> re-render

            else:
                # Try as category toggle
                cat_idx = _key_to_index(key, len(all_categories) - 1)
                if cat_idx is not None and cat_idx < len(all_categories):
                    cat = all_categories[cat_idx]
                    if cat in active_cats:
                        active_cats.discard(cat)
                    else:
                        active_cats.add(cat)
                # stay in inner loop -> re-render

    # Done — all contacts reviewed
    console.clear()
    console.print(f"\n[bold green]Alle {total} Kontakte durchgesehen. "
                  f"{changed_count} ge\u00e4ndert.[/bold green]")
