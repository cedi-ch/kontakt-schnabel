"""TUI review interface for raw-parsed contacts."""

import sys

import readchar
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from schnabel.rawparse import ParsedContact, ParsedField

console = Console()

# Field type display names and selection map
FIELD_TYPES = {
    "1": "fn",
    "2": "email",
    "3": "tel",
    "4": "adr",
    "5": "org",
    "6": "url",
    "7": "note",
    "8": "bday",
    "9": "nickname",
    "0": "role",
}

FIELD_LABELS = {
    "fn": "FN",
    "email": "EMAIL",
    "tel": "TEL",
    "adr": "ADR",
    "org": "ORG",
    "url": "URL",
    "note": "NOTE",
    "bday": "BDAY",
    "nickname": "NICKNAME",
    "role": "ROLE",
    "title": "TITLE",
    "unknown": "???",
}


def _confidence_stars(confidence: str) -> Text:
    """Render confidence as colored stars."""
    if confidence == "high":
        t = Text()
        t.append("\u2605\u2605\u2605", style="green")
        return t
    elif confidence == "medium":
        t = Text()
        t.append("\u2605\u2605", style="yellow")
        t.append("\u2606", style="dim")
        return t
    else:
        t = Text()
        t.append("\u2605", style="red")
        t.append("\u2606\u2606", style="dim")
        return t


def _truncate(s: str, maxlen: int = 50) -> str:
    if len(s) <= maxlen:
        return s
    return s[: maxlen - 1] + "\u2026"


def _render_contact(contact: ParsedContact, idx: int, total: int,
                    accepted: int, rejected: int):
    """Render the review screen for a single parsed contact."""
    console.clear()

    # Header
    header = Text()
    header.append("schnabel rawparse", style="bold cyan")
    header.append(f" \u2014 {total} Kontakte geparst")
    header.append(f"  ({accepted} akzeptiert, {rejected} verworfen)", style="dim")
    header.append("   [q]uit [?]hilfe", style="dim")
    console.print(header)
    console.print("\u2500" * console.width)

    # Contact info
    status_text = {
        "pending": "ausstehend",
        "accepted": "akzeptiert",
        "rejected": "verworfen",
    }.get(contact.status, contact.status)

    info = Text()
    info.append(f"  Kontakt {idx + 1}/{total}", style="bold")
    info.append(f" ({status_text})")
    console.print(info)

    # Raw text (truncated)
    raw_display = contact.raw_text.replace("\n", " \u00b7 ")
    console.print(f"  Roh: \"{_truncate(raw_display, console.width - 12)}\"", style="dim")
    console.print("\u2500" * console.width)

    # Fields
    for i, f in enumerate(contact.fields):
        label = FIELD_LABELS.get(f.field_type, f.field_type.upper())
        stars = _confidence_stars(f.confidence)

        line = Text()
        line.append(f"  {i + 1:<3}", style="bold cyan")
        line.append(f"{label + ':':<8}", style="bold")
        line.append(f"{_truncate(f.value, console.width - 25):<{console.width - 25}}")
        line.append_text(stars)
        console.print(line)

    if not contact.fields:
        console.print("  (keine Felder erkannt)", style="dim")

    console.print("\u2500" * console.width)

    # Action bar
    line1 = Text()
    line1.append(" a", style="bold green")
    line1.append("=akzeptieren  ")
    line1.append("r", style="bold red")
    line1.append("=verwerfen  ")
    line1.append("s", style="bold yellow")
    line1.append("=\u00fcberspringen  ")
    line1.append("b", style="bold cyan")
    line1.append("=zur\u00fcck")
    console.print(line1)

    line2 = Text()
    line2.append(" e", style="bold blue")
    line2.append("=bearbeiten  ")
    line2.append("d", style="bold red")
    line2.append("=feld l\u00f6schen  ")
    line2.append("w", style="bold magenta")
    line2.append("=typ wechseln  ")
    line2.append("+", style="bold green")
    line2.append("=feld hinzuf\u00fcgen")
    console.print(line2)

    line3 = Text()
    line3.append(" ?", style="dim")
    line3.append("=hilfe  ")
    line3.append("q", style="dim")
    line3.append("=quit")
    console.print(line3)


def _prompt_field_number(contact: ParsedContact, prompt_text: str = "Feld #: ") -> int | None:
    """Prompt user for a field number. Returns 0-based index or None."""
    console.print(f"\n  {prompt_text}", end="")
    key = readchar.readchar()
    console.print(key)
    try:
        num = int(key)
        if 1 <= num <= len(contact.fields):
            return num - 1
    except ValueError:
        pass
    console.print("  [red]Ung\u00fcltige Feldnummer.[/red]")
    _pause()
    return None


def _pause():
    """Wait for any keypress."""
    import time
    time.sleep(0.7)


def _edit_field(contact: ParsedContact):
    """Edit a field value inline."""
    idx = _prompt_field_number(contact)
    if idx is None:
        return

    f = contact.fields[idx]
    label = FIELD_LABELS.get(f.field_type, f.field_type)
    console.print(f"  Aktuell: [bold]{label}[/bold] = {f.value}")
    try:
        new_val = input("  Neuer Wert: ").strip()
    except (EOFError, KeyboardInterrupt):
        return
    if new_val:
        f.value = new_val
        f.confidence = "high"  # user-edited = high confidence


def _delete_field(contact: ParsedContact):
    """Delete a field by number."""
    idx = _prompt_field_number(contact)
    if idx is None:
        return
    removed = contact.fields.pop(idx)
    label = FIELD_LABELS.get(removed.field_type, removed.field_type)
    console.print(f"  [red]Feld {label} gel\u00f6scht.[/red]")
    _pause()


def _change_type(contact: ParsedContact):
    """Change the type of a field."""
    idx = _prompt_field_number(contact)
    if idx is None:
        return

    console.print("  Typ w\u00e4hlen: ", end="")
    for k, v in FIELD_TYPES.items():
        label = FIELD_LABELS[v]
        console.print(f"[bold]{k}[/bold]={label} ", end="")
    console.print()

    key = readchar.readchar()
    if key in FIELD_TYPES:
        contact.fields[idx].field_type = FIELD_TYPES[key]
        label = FIELD_LABELS[FIELD_TYPES[key]]
        console.print(f"  Typ ge\u00e4ndert zu [bold]{label}[/bold].")
        _pause()
    else:
        console.print("  [red]Ung\u00fcltige Auswahl.[/red]")
        _pause()


def _add_field(contact: ParsedContact):
    """Add a new field."""
    console.print("\n  Typ w\u00e4hlen: ", end="")
    for k, v in FIELD_TYPES.items():
        label = FIELD_LABELS[v]
        console.print(f"[bold]{k}[/bold]={label} ", end="")
    console.print()

    key = readchar.readchar()
    if key not in FIELD_TYPES:
        console.print("  [red]Ung\u00fcltige Auswahl.[/red]")
        _pause()
        return

    field_type = FIELD_TYPES[key]
    try:
        value = input("  Wert: ").strip()
    except (EOFError, KeyboardInterrupt):
        return
    if value:
        contact.fields.append(ParsedField(
            field_type=field_type,
            value=value,
            confidence="high",
            original_fragment="(manuell)",
        ))


def _show_help():
    """Show help screen."""
    console.clear()
    console.print(Panel(
        "[bold]Tastaturk\u00fcrzel[/bold]\n\n"
        "[green]a[/green]  akzeptieren: Kontakt \u00fcbernehmen, weiter\n"
        "[red]r[/red]  verwerfen: Kontakt l\u00f6schen, weiter\n"
        "[yellow]s[/yellow]  \u00fcberspringen: sp\u00e4ter nochmal anschauen\n"
        "[cyan]b[/cyan]  zur\u00fcck: vorherigen Kontakt nochmal anzeigen\n"
        "[blue]e[/blue]  bearbeiten: Feldwert \u00e4ndern (bleibt beim Kontakt)\n"
        "[red]d[/red]  feld l\u00f6schen: ein Feld entfernen (bleibt beim Kontakt)\n"
        "[magenta]w[/magenta]  typ wechseln: Feldtyp \u00e4ndern (bleibt beim Kontakt)\n"
        "[green]+[/green]  feld hinzuf\u00fcgen: neues Feld eingeben (bleibt beim Kontakt)\n"
        "[dim]?[/dim]  diese Hilfe\n"
        "[dim]q[/dim]  beenden (akzeptierte Kontakte werden gespeichert)\n\n"
        "[bold]Konfidenz:[/bold] \u2605\u2605\u2605 hoch  \u2605\u2605\u2606 mittel  \u2605\u2606\u2606 tief\n\n"
        "Beliebige Taste zum Fortfahren...",
        title="Hilfe",
    ))
    readchar.readchar()


def run_raw_tui(contacts: list[ParsedContact]) -> list[ParsedContact]:
    """Run the interactive TUI for reviewing raw-parsed contacts.

    Returns the list with updated status fields (accepted/rejected/pending).
    """
    if not contacts:
        console.print("[yellow]Keine Kontakte zum Pr\u00fcfen.[/yellow]")
        return contacts

    quit_requested = False
    # History of visited contact indices (for back navigation)
    history: list[int] = []

    while not quit_requested:
        # Collect pending contacts for this pass
        pending_ids = [i for i, c in enumerate(contacts) if c.status == "pending"]
        if not pending_ids:
            break

        pos = 0  # position within pending_ids
        history.clear()

        while pos < len(pending_ids) and not quit_requested:
            idx = pending_ids[pos]
            contact = contacts[idx]
            if contact.status != "pending":
                pos += 1
                continue

            # Inner loop: stay on this contact until a/r/s/b/q
            while True:
                accepted = sum(1 for c in contacts if c.status == "accepted")
                rejected = sum(1 for c in contacts if c.status == "rejected")
                _render_contact(contact, idx, len(contacts), accepted, rejected)

                key = readchar.readchar()

                if key == "q":
                    quit_requested = True
                    break

                elif key == "a":
                    contact.status = "accepted"
                    history.append(pos)
                    pos += 1
                    break

                elif key == "r":
                    contact.status = "rejected"
                    history.append(pos)
                    pos += 1
                    break

                elif key == "s":
                    history.append(pos)
                    pos += 1
                    break

                elif key == "b":
                    if history:
                        prev_pos = history.pop()
                        # Undo the status change so it's pending again
                        prev_contact = contacts[pending_ids[prev_pos]]
                        prev_contact.status = "pending"
                        pos = prev_pos
                    else:
                        console.print("\n  [dim]Kein vorheriger Kontakt.[/dim]")
                        import time
                        time.sleep(0.5)
                    break

                elif key == "e":
                    _edit_field(contact)
                    # stays in inner loop → re-renders

                elif key == "d":
                    _delete_field(contact)

                elif key == "w":
                    _change_type(contact)

                elif key == "+":
                    _add_field(contact)

                elif key == "?":
                    _show_help()

        # End of pass — check if we should loop
        if not quit_requested:
            pending = sum(1 for c in contacts if c.status == "pending")
            if pending == 0:
                break
            console.print(
                f"\n[yellow]Durchlauf fertig. {pending} \u00fcbersprungen "
                f"\u2014 starte n\u00e4chste Runde...[/yellow]"
            )
            import time
            time.sleep(1)

    # Final summary
    accepted = sum(1 for c in contacts if c.status == "accepted")
    rejected = sum(1 for c in contacts if c.status == "rejected")
    pending = sum(1 for c in contacts if c.status == "pending")
    if pending > 0:
        console.print(
            f"\n[yellow]Beendet. {accepted} akzeptiert, "
            f"{rejected} verworfen, {pending} offen.[/yellow]"
        )
    else:
        console.print(
            f"\n[bold green]Alle Kontakte gepr\u00fcft! "
            f"{accepted} akzeptiert, {rejected} verworfen.[/bold green]"
        )

    return contacts
