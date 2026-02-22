"""TUI review interface for manual dedup decisions."""

import time

import readchar
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from schnabel.db import Database
from schnabel.merge import determine_survivor, merge_contacts, undo_merge
from schnabel.model import ContactField
from schnabel.normalize import normalize_email, normalize_phone


console = Console()

# Field type selection map for adding new fields
DEDUP_FIELD_TYPES = {
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
}


def _compare_symbol(val_a: str, val_b: str, norm_a: str = "", norm_b: str = "") -> str:
    """Return comparison symbol between two values."""
    if not val_a and not val_b:
        return " "
    if not val_a:
        return "⊆"
    if not val_b:
        return "⊇"
    if val_a == val_b:
        return "≡"
    if (norm_a and norm_b and norm_a == norm_b) or val_a.lower() == val_b.lower():
        return "≃"
    return "≠"


def _truncate(s: str, maxlen: int = 30) -> str:
    if len(s) <= maxlen:
        return s
    return s[:maxlen - 1] + "…"


def _confidence_bar(confidence: float, width: int = 25) -> Text:
    filled = int(confidence * width)
    empty = width - filled
    pct = f"{confidence * 100:.0f}%"

    if confidence >= 0.9:
        color = "green"
    elif confidence >= 0.7:
        color = "yellow"
    else:
        color = "red"

    bar = Text()
    bar.append("█" * filled, style=color)
    bar.append("░" * empty, style="dim")
    bar.append(f" {pct}", style=f"bold {color}")
    return bar


def _get_source_name(source_file: str) -> str:
    """Extract a clean source name from a file path."""
    if not source_file:
        return "(unknown)"
    from pathlib import Path
    return Path(source_file).stem


def render_pair(pair: dict, contact_a, contact_b, pair_idx: int, total: int,
                auto_merged: int):
    """Render a comparison view for a pair of contacts."""
    console.clear()

    # Header
    header = Text()
    header.append("schnabel dedup", style="bold cyan")
    header.append(f" — {total} pairs remaining ({auto_merged} auto-merged)")
    header.append("        [q]uit [?]help", style="dim")
    console.print(header)
    console.print("─" * console.width)

    # Pair info
    info = Text()
    info.append(f"  Pair {pair_idx + 1}/{total}", style="bold")
    info.append("  │  Confidence: ")
    info.append_text(_confidence_bar(pair["confidence"]))
    console.print(info)
    console.print("─" * console.width)

    # Source
    src_a = _get_source_name(contact_a.source_file)
    src_b = _get_source_name(contact_b.source_file)
    console.print(f"  Contact #{contact_a.id:<24}║   Contact #{contact_b.id}")
    console.print(f"  Source: {_truncate(src_a, 24):<24}║   Source: {src_b}")
    console.print("─" * console.width)

    # Build indexed field maps: type -> [(fields_index, value)]
    left_by_type: dict[str, list[tuple[int, str]]] = {}
    for i, f in enumerate(contact_a.fields):
        left_by_type.setdefault(f.field_type, []).append((i + 1, f.field_value))

    right_by_type: dict[str, list[tuple[int, str]]] = {}
    for i, f in enumerate(contact_b.fields):
        right_by_type.setdefault(f.field_type, []).append((i + 1, f.field_value))

    # Comparison rows: (label, left_tag, left_val, sym, right_val, right_tag)
    rows: list[tuple[str, str, str, str, str, str]] = []

    # FN (#0)
    sym = _compare_symbol(contact_a.fn, contact_b.fn)
    rows.append(("FN:", "L0", contact_a.fn or "(none)", sym,
                 contact_b.fn or "(none)", "R0"))

    def _field_rows(field_type: str, label: str, normalize_fn=None, min_rows: int = 0):
        la = left_by_type.get(field_type, [])
        ra = right_by_type.get(field_type, [])
        n = max(len(la), len(ra), min_rows)
        if n == 0:
            return
        for i in range(n):
            if i < len(la):
                li, lv = la[i]
                lt = f"L{li}"
            else:
                lt, lv = "  ", "(none)"
            if i < len(ra):
                ri, rv = ra[i]
                rt = f"R{ri}"
            else:
                rt, rv = "  ", "(none)"
            na = (normalize_fn(lv) or "") if normalize_fn and lv != "(none)" else ""
            nb = (normalize_fn(rv) or "") if normalize_fn and rv != "(none)" else ""
            s = _compare_symbol(lv, rv, na, nb)
            lbl = f"{label}:" if i == 0 else ""
            rows.append((lbl, lt, lv, s, rv, rt))

    _field_rows("email", "EMAIL", normalize_email, min_rows=1)
    _field_rows("tel", "TEL", normalize_phone, min_rows=1)

    # Photo (not editable via field numbers)
    photo_a = (f"[{contact_a.photos[0].photo_format} "
               f"{contact_a.photos[0].width}×{contact_a.photos[0].height}]"
               if contact_a.photos else "(none)")
    photo_b = (f"[{contact_b.photos[0].photo_format} "
               f"{contact_b.photos[0].width}×{contact_b.photos[0].height}]"
               if contact_b.photos else "(none)")
    sym = _compare_symbol(photo_a, photo_b)
    rows.append(("PHOTO:", "  ", photo_a, sym, photo_b, "  "))

    _field_rows("org", "ORG")
    _field_rows("adr", "ADR")
    _field_rows("url", "URL")
    _field_rows("note", "NOTE")
    _field_rows("bday", "BDAY")
    _field_rows("title", "TITLE")
    _field_rows("nickname", "NICK")
    _field_rows("role", "ROLE")

    # Render rows
    for label, lt, left, sym, right, rt in rows:
        left_str = _truncate(left, 25)
        right_str = _truncate(right, 25)
        line = Text()
        line.append(f"  {lt:<3}", style="dim cyan")
        line.append(f"{label:<10}")
        line.append(f"{left_str:<25}")
        line.append(f" {sym} ")
        line.append(f"  {right_str:<25}")
        line.append(f" {rt}", style="dim cyan")
        console.print(line)

    console.print("─" * console.width)

    # Action bar — three lines
    line1 = Text()
    line1.append(" a", style="bold green")
    line1.append("=auto-merge  ")
    line1.append("l", style="bold blue")
    line1.append("=keep left  ")
    line1.append("r", style="bold blue")
    line1.append("=keep right  ")
    line1.append("s", style="bold yellow")
    line1.append("=skip  ")
    line1.append("n", style="bold cyan")
    line1.append("=not a dup")
    console.print(line1)

    line2 = Text()
    line2.append(" 1", style="bold red")
    line2.append("=del left  ")
    line2.append("2", style="bold red")
    line2.append("=del right  ")
    line2.append("x", style="bold red")
    line2.append("=del both  ")
    line2.append("u", style="bold magenta")
    line2.append("=undo  ")
    line2.append("?", style="dim")
    line2.append("=help  ")
    line2.append("q", style="dim")
    line2.append("=quit")
    console.print(line2)

    line3 = Text()
    line3.append(" e", style="bold blue")
    line3.append("=edit  ")
    line3.append("d", style="bold red")
    line3.append("=del field  ")
    line3.append("+", style="bold green")
    line3.append("=add field")
    console.print(line3)


# -- Inline editing helpers --

def _prompt_side(contact_a, contact_b):
    """Prompt user to pick left or right side. Returns (contact, side_char) or (None, None)."""
    line = Text()
    line.append("  Seite? ")
    line.append("l", style="bold blue")
    line.append("=links  ")
    line.append("r", style="bold blue")
    line.append("=rechts: ")
    console.print(line, end="")

    key = readchar.readchar()
    console.print(key)
    if key == "l":
        return contact_a, "l"
    elif key == "r":
        return contact_b, "r"
    return None, None


def _show_fields(contact) -> list[tuple[int, str, str]]:
    """Show numbered field list for a contact.

    Returns list of (field_num, type_label, value).
    Field #0 = FN, Field #1..N = contact.fields[0..N-1].
    """
    entries = [(0, "FN", contact.fn or "(leer)")]
    for i, f in enumerate(contact.fields):
        label = FIELD_LABELS.get(f.field_type, f.field_type.upper())
        entries.append((i + 1, label, f.field_value))

    for num, label, value in entries:
        line = Text()
        line.append(f"    {num:<3}", style="bold cyan")
        line.append(f"{label + ':':<10}", style="bold")
        line.append(_truncate(value, 50))
        console.print(line)

    return entries


def _prompt_field_number(entries: list) -> int | None:
    """Prompt for a field number via readchar. Returns the number or None."""
    max_num = len(entries) - 1
    console.print(f"  Feld # (0-{max_num}): ", end="")
    key = readchar.readchar()
    console.print(key)
    try:
        num = int(key)
        if 0 <= num <= max_num:
            return num
    except ValueError:
        pass
    console.print("  [red]Ungültige Feldnummer.[/red]")
    time.sleep(0.7)
    return None


def _edit_dedup_field(db: Database, contact_a, contact_b):
    """Edit a field on one side."""
    contact, side = _prompt_side(contact_a, contact_b)
    if contact is None:
        return

    side_label = "links" if side == "l" else "rechts"
    console.print(f"  Felder ({side_label}, #{contact.id}):")
    entries = _show_fields(contact)

    num = _prompt_field_number(entries)
    if num is None:
        return

    if num == 0:
        console.print(f"  Aktuell: [bold]FN[/bold] = {contact.fn}")
        try:
            new_val = input("  Neuer Wert: ").strip()
        except (EOFError, KeyboardInterrupt):
            return
        if new_val:
            db.update_contact_name(contact.id, new_val, contact.family_name, contact.given_name)
            db.commit()
    else:
        f = contact.fields[num - 1]
        label = FIELD_LABELS.get(f.field_type, f.field_type.upper())
        console.print(f"  Aktuell: [bold]{label}[/bold] = {f.field_value}")
        try:
            new_val = input("  Neuer Wert: ").strip()
        except (EOFError, KeyboardInterrupt):
            return
        if new_val:
            db.update_contact_field(f.id, new_val)
            db.commit()


def _delete_dedup_field(db: Database, contact_a, contact_b):
    """Delete a field on one side."""
    contact, side = _prompt_side(contact_a, contact_b)
    if contact is None:
        return

    side_label = "links" if side == "l" else "rechts"
    console.print(f"  Felder ({side_label}, #{contact.id}):")
    entries = _show_fields(contact)

    num = _prompt_field_number(entries)
    if num is None:
        return

    if num == 0:
        console.print("  [red]FN kann nicht gelöscht werden.[/red]")
        time.sleep(0.7)
        return

    f = contact.fields[num - 1]
    label = FIELD_LABELS.get(f.field_type, f.field_type.upper())
    db.delete_contact_field(f.id)
    db.commit()
    console.print(f"  [red]{label} gelöscht.[/red]")
    time.sleep(0.5)


def _add_dedup_field(db: Database, contact_a, contact_b):
    """Add a new field to one side."""
    contact, side = _prompt_side(contact_a, contact_b)
    if contact is None:
        return

    console.print("\n  Typ wählen: ", end="")
    for k, v in DEDUP_FIELD_TYPES.items():
        label = FIELD_LABELS[v]
        console.print(f"[bold]{k}[/bold]={label} ", end="")
    console.print()

    key = readchar.readchar()
    if key not in DEDUP_FIELD_TYPES:
        console.print("  [red]Ungültige Auswahl.[/red]")
        time.sleep(0.7)
        return

    field_type = DEDUP_FIELD_TYPES[key]
    try:
        value = input("  Wert: ").strip()
    except (EOFError, KeyboardInterrupt):
        return
    if value:
        new_field = ContactField(field_type=field_type, field_value=value)
        db.add_contact_field(contact.id, new_field)
        db.commit()
        label = FIELD_LABELS[field_type]
        console.print(f"  [green]{label} hinzugefügt.[/green]")
        time.sleep(0.5)


# -- Main TUI loop --

def run_tui(db: Database, auto_merged: int = 0):
    """Run the interactive TUI for reviewing duplicate pairs."""
    pairs = db.get_pending_pairs()
    if not pairs:
        console.print("[green]No pairs to review. All done![/green]")
        return

    idx = 0
    last_merge_id = None
    total = len(pairs)

    while idx < len(pairs):
        pair = pairs[idx]

        # Inner loop: stay on this pair until a navigation key is pressed
        while True:
            # Reload contacts from DB each iteration (edits immediately visible)
            contact_a = db.get_contact(pair["contact_a_id"])
            contact_b = db.get_contact(pair["contact_b_id"])

            if not contact_a or not contact_b or not contact_a.is_active or not contact_b.is_active:
                idx += 1
                break

            render_pair(pair, contact_a, contact_b, idx, total, auto_merged)

            key = readchar.readchar()
            key = key.lower()

            if key == "q":
                console.print("\n[yellow]Progress saved. Resume anytime with 'schnabel dedup'.[/yellow]")
                stats = db.get_stats()
                console.print(f"\n[bold]Session complete.[/bold] Active contacts: {stats['active']}, "
                              f"Merges total: {stats['merges']}")
                return

            elif key == "a":
                survivor_id, absorbed_id = determine_survivor(
                    db, pair["contact_a_id"], pair["contact_b_id"]
                )
                last_merge_id = merge_contacts(
                    db, survivor_id, absorbed_id,
                    merge_type="manual", confidence=pair["confidence"],
                )
                db.update_pair_resolution(pair["id"], "manual_merged")
                idx += 1
                break

            elif key == "l":
                last_merge_id = merge_contacts(
                    db, pair["contact_a_id"], pair["contact_b_id"],
                    merge_type="manual", confidence=pair["confidence"],
                )
                db.update_pair_resolution(pair["id"], "manual_merged")
                idx += 1
                break

            elif key == "r":
                last_merge_id = merge_contacts(
                    db, pair["contact_b_id"], pair["contact_a_id"],
                    merge_type="manual", confidence=pair["confidence"],
                )
                db.update_pair_resolution(pair["id"], "manual_merged")
                idx += 1
                break

            elif key == "s":
                idx += 1
                break

            elif key == "n":
                db.update_pair_resolution(pair["id"], "not_dup")
                idx += 1
                break

            elif key == "1":
                db.delete_contact(pair["contact_a_id"])
                db.update_pair_resolution(pair["id"], "skipped")
                idx += 1
                break

            elif key == "2":
                db.delete_contact(pair["contact_b_id"])
                db.update_pair_resolution(pair["id"], "skipped")
                idx += 1
                break

            elif key == "x":
                db.delete_contact(pair["contact_a_id"])
                db.delete_contact(pair["contact_b_id"])
                db.update_pair_resolution(pair["id"], "skipped")
                idx += 1
                break

            elif key == "u":
                if last_merge_id:
                    if undo_merge(db, last_merge_id):
                        console.print("[magenta]Undo successful.[/magenta]")
                        last_merge_id = None
                        if idx > 0:
                            idx -= 1
                        time.sleep(0.5)
                        break
                    else:
                        console.print("[red]Nothing to undo.[/red]")
                else:
                    console.print("[red]Nothing to undo.[/red]")
                time.sleep(0.5)
                # stay in inner loop → re-render

            elif key == "e":
                _edit_dedup_field(db, contact_a, contact_b)
                # stay in inner loop → re-render with updated data

            elif key == "d":
                _delete_dedup_field(db, contact_a, contact_b)
                # stay in inner loop → re-render

            elif key == "+":
                _add_dedup_field(db, contact_a, contact_b)
                # stay in inner loop → re-render

            elif key == "?":
                console.clear()
                console.print(Panel(
                    "[bold]Keyboard shortcuts[/bold]\n\n"
                    "[green]a[/green]  auto-merge: richer contact survives, fields get combined\n"
                    "[blue]l[/blue]  keep left: left survives, right gets absorbed\n"
                    "[blue]r[/blue]  keep right: right survives, left gets absorbed\n"
                    "[yellow]s[/yellow]  skip: revisit this pair later\n"
                    "[cyan]n[/cyan]  not a dup: mark as different people, never suggest again\n"
                    "[red]1[/red]  delete left contact\n"
                    "[red]2[/red]  delete right contact\n"
                    "[red]x[/red]  delete both contacts\n"
                    "[magenta]u[/magenta]  undo last action\n"
                    "[blue]e[/blue]  edit a field on one side\n"
                    "[red]d[/red]  delete a field on one side\n"
                    "[green]+[/green]  add a field to one side\n"
                    "[dim]q[/dim]  quit (progress saved, resume anytime)\n\n"
                    "[bold]Symbols:[/bold] ≡ identical  ≃ equivalent  ⊆/⊇ subset/superset  ≠ different\n\n"
                    "Press any key to continue...",
                    title="Help",
                ))
                readchar.readchar()
                # stay in inner loop → re-render

    # Final summary
    stats = db.get_stats()
    console.print(f"\n[bold]Session complete.[/bold] Active contacts: {stats['active']}, "
                  f"Merges total: {stats['merges']}")
