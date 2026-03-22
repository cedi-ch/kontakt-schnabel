"""TUI for searching and editing individual contacts."""

import time

import readchar
from rich.console import Console
from rich.table import Table
from rich.text import Text

from schnabel.db import Database
from schnabel.model import ContactField
from schnabel.ui_helpers import index_to_label, key_to_field_index

console = Console()

FIELD_TYPES = {
    "1": "email",
    "2": "tel",
    "3": "adr",
    "4": "org",
    "5": "url",
    "6": "note",
    "7": "bday",
    "8": "nickname",
    "9": "role",
    "0": "title",
}

FIELD_LABELS = {
    "email": "EMAIL", "tel": "TEL", "adr": "ADR", "org": "ORG",
    "url": "URL", "note": "NOTE", "bday": "BDAY", "nickname": "NICK",
    "role": "ROLE", "title": "TITLE", "categories": "CAT",
}


def _render_contact_detail(contact, db):
    """Render full contact detail view."""
    console.clear()

    header = Text()
    header.append("schnabel search", style="bold cyan")
    header.append(f"  —  #{contact.id}")
    console.print(header)
    console.print("─" * console.width)

    # Build numbered field list
    entries = []

    # FN as field 0
    entries.append((0, "FN", contact.fn or "(leer)", None))

    # N components
    name_parts = []
    if contact.family_name:
        name_parts.append(f"Nachname: {contact.family_name}")
    if contact.given_name:
        name_parts.append(f"Vorname: {contact.given_name}")
    if name_parts:
        entries.append((None, "N", ", ".join(name_parts), None))

    # UID
    if contact.uid:
        entries.append((None, "UID", contact.uid, None))

    # Category
    entries.append((None, "KAT", contact.category, None))

    # All fields
    field_idx = 1
    for f in contact.fields:
        label = FIELD_LABELS.get(f.field_type, f.field_type.upper())
        val = f.field_value.replace("\r\n", " ").replace("\n", " ")
        entries.append((field_idx, label, val, f))
        field_idx += 1

    # Photos
    for p in contact.photos:
        entries.append((None, "PHOTO", f"[{p.photo_format} {p.width}×{p.height}]", None))

    # Render
    for entry in entries:
        idx, label, value, _field = entry
        line = Text()
        if idx is not None:
            key_label = index_to_label(idx)
            line.append(f"  {key_label:<3}", style="bold cyan")
        else:
            line.append("     ")
        line.append(f"{label + ':':<10}", style="bold")
        line.append(value)
        console.print(line)

    console.print("─" * console.width)

    # Action bar
    max_idx = field_idx - 1
    max_label = index_to_label(max_idx) if max_idx >= 0 else "0"
    line1 = Text()
    line1.append("  e", style="bold blue")
    line1.append(f"+0-{max_label}=editieren  ")
    line1.append("d", style="bold red")
    line1.append(f"+0-{max_label}=löschen  ")
    line1.append("+", style="bold green")
    line1.append("=feld hinzufügen  ")
    line1.append("/", style="bold cyan")
    line1.append("=neue Suche  ")
    line1.append("q", style="dim")
    line1.append("=zurück")
    console.print(line1)

    return entries, field_idx


def _edit_field(db, contact, entries, field_idx):
    """Edit a field by index."""
    console.print("  Welches Feld? ", end="")
    key = readchar.readchar()
    console.print(key)

    num = key_to_field_index(key, field_idx - 1)
    if num is None:
        console.print("  [red]Ungültige Nummer.[/red]")
        time.sleep(0.5)
        return

    # Find the entry
    target = None
    for entry in entries:
        if entry[0] == num:
            target = entry
            break

    if target is None:
        console.print("  [red]Feld nicht editierbar.[/red]")
        time.sleep(0.5)
        return

    idx, label, value, field_obj = target

    if idx == 0:
        # Edit FN
        console.print(f"  Aktuell: [bold]FN[/bold] = {contact.fn}")
        try:
            new_val = input("  Neuer Wert: ").strip()
        except (EOFError, KeyboardInterrupt):
            return
        if new_val:
            db.update_contact_name(contact.id, new_val, contact.family_name, contact.given_name)
            db.commit()
            console.print(f"  [green]FN → {new_val}[/green]")
            time.sleep(0.3)
    elif field_obj is not None:
        console.print(f"  Aktuell: [bold]{label}[/bold] = {value}")
        try:
            new_val = input("  Neuer Wert: ").strip()
        except (EOFError, KeyboardInterrupt):
            return
        if new_val:
            db.update_contact_field(field_obj.id, new_val)
            db.commit()
            console.print(f"  [green]{label} aktualisiert.[/green]")
            time.sleep(0.3)


def _delete_field(db, contact, entries, field_idx):
    """Delete a field by index."""
    console.print("  Welches Feld löschen? ", end="")
    key = readchar.readchar()
    console.print(key)

    num = key_to_field_index(key, field_idx - 1)
    if num is None:
        console.print("  [red]Ungültige Nummer.[/red]")
        time.sleep(0.5)
        return

    if num == 0:
        console.print("  [red]FN kann nicht gelöscht werden.[/red]")
        time.sleep(0.5)
        return

    target = None
    for entry in entries:
        if entry[0] == num:
            target = entry
            break

    if target is None or target[3] is None:
        console.print("  [red]Feld nicht löschbar.[/red]")
        time.sleep(0.5)
        return

    _, label, value, field_obj = target
    db.delete_contact_field(field_obj.id)
    db.commit()
    console.print(f"  [red]{label} gelöscht.[/red]")
    time.sleep(0.3)


def _add_field(db, contact):
    """Add a new field to the contact."""
    console.print("\n  Typ wählen: ", end="")
    for k, v in FIELD_TYPES.items():
        label = FIELD_LABELS.get(v, v.upper())
        console.print(f"[bold]{k}[/bold]={label} ", end="")
    console.print()

    key = readchar.readchar()
    if key not in FIELD_TYPES:
        console.print("  [red]Ungültige Auswahl.[/red]")
        time.sleep(0.5)
        return

    field_type = FIELD_TYPES[key]
    try:
        value = input("  Wert: ").strip()
    except (EOFError, KeyboardInterrupt):
        return
    if value:
        new_field = ContactField(field_type=field_type, field_value=value)
        db.add_contact_field(contact.id, new_field)
        db.commit()
        label = FIELD_LABELS.get(field_type, field_type.upper())
        console.print(f"  [green]{label} hinzugefügt.[/green]")
        time.sleep(0.3)


def _show_search_results(results):
    """Show search results as a numbered list."""
    table = Table(title="[bold cyan]Suchergebnisse[/bold cyan]",
                  border_style="cyan", show_header=True)
    table.add_column("#", style="bold cyan", justify="right")
    table.add_column("Name", style="bold")
    table.add_column("Email", style="dim")
    table.add_column("Tel", style="dim")
    table.add_column("Kat", style="dim")

    for i, c in enumerate(results):
        key = index_to_label(i)
        email = c.emails[0] if c.emails else ""
        phone = c.phones[0] if c.phones else ""
        table.add_row(key, c.fn, email, phone, c.category)

    console.print(table)
    console.print(f"  [dim]{len(results)} Treffer[/dim]")


def run_search_tui(db: Database):
    """Run the interactive search/edit TUI."""
    while True:
        console.print("\n[bold cyan]Suche:[/bold cyan] ", end="")
        try:
            query = input().strip()
        except (EOFError, KeyboardInterrupt):
            return

        if not query:
            return

        results = db.search_contacts(query)

        if not results:
            console.print("[yellow]Keine Treffer.[/yellow]")
            continue

        if len(results) == 1:
            # Jump directly to detail view
            selected = results[0]
        else:
            _show_search_results(results)
            console.print("  Kontakt auswählen (0-z), /=neue Suche, q=quit: ", end="")
            key = readchar.readchar()
            console.print(key)

            if key == "q":
                return
            if key == "/":
                continue

            idx = key_to_field_index(key, len(results) - 1)
            if idx is None:
                console.print("  [red]Ungültige Auswahl.[/red]")
                time.sleep(0.5)
                continue
            selected = results[idx]

        # Detail view loop
        while True:
            contact = db.get_contact(selected.id)
            if not contact:
                console.print("[red]Kontakt nicht gefunden.[/red]")
                break

            entries, field_count = _render_contact_detail(contact, db)

            key = readchar.readchar()

            if key == "q" or key == "/":
                break

            elif key == "e":
                _edit_field(db, contact, entries, field_count)

            elif key == "d":
                _delete_field(db, contact, entries, field_count)

            elif key == "+":
                _add_field(db, contact)

            # Any other key: re-render
