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
from schnabel.ui_helpers import truncate as _truncate, confidence_bar as _confidence_bar, safe_readchar


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
    "categories": "CAT",
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



# _truncate and _confidence_bar imported from ui_helpers


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
    _field_rows("categories", "CAT")

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
    line1.append("=links  ")
    line1.append("r", style="bold blue")
    line1.append("=rechts  ")
    line1.append("s", style="bold yellow")
    line1.append("=skip  ")
    line1.append("n", style="bold cyan")
    line1.append("=kein Dup  ")
    line1.append("b", style="bold cyan")
    line1.append("=zurück")
    console.print(line1)

    line2 = Text()
    line2.append(" 1", style="bold red")
    line2.append("=del links  ")
    line2.append("2", style="bold red")
    line2.append("=del rechts  ")
    line2.append("x", style="bold red")
    line2.append("=del beide  ")
    line2.append("u", style="bold magenta")
    line2.append("=undo  ")
    line2.append("?", style="dim")
    line2.append("=hilfe  ")
    line2.append("q", style="dim")
    line2.append("=quit")
    console.print(line2)

    line3 = Text()
    line3.append(" e", style="bold blue")
    line3.append("=bearbeiten  ")
    line3.append("d", style="bold red")
    line3.append("=feld löschen  ")
    line3.append("+", style="bold green")
    line3.append("=feld hinzufügen")
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
    from schnabel.ui_helpers import index_to_label
    entries = [(0, "FN", contact.fn or "(leer)")]
    for i, f in enumerate(contact.fields):
        label = FIELD_LABELS.get(f.field_type, f.field_type.upper())
        entries.append((i + 1, label, f.field_value))

    for num, label, value in entries:
        key_label = index_to_label(num)
        line = Text()
        line.append(f"    {key_label:<3}", style="bold cyan")
        line.append(f"{label + ':':<10}", style="bold")
        line.append(_truncate(value, 50))
        console.print(line)

    return entries


def _prompt_field_number(entries: list) -> int | None:
    """Prompt for a field number via readchar. Supports 0-9 then a-z."""
    from schnabel.ui_helpers import index_to_label, key_to_field_index
    max_num = len(entries) - 1
    max_label = index_to_label(max_num)
    console.print(f"  Feld # (0-{max_label}): ", end="")
    key = readchar.readchar()
    console.print(key)
    num = key_to_field_index(key, max_num)
    if num is not None:
        return num
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


def _rescore_pair(db: Database, pair: dict) -> float:
    """Recalculate confidence score for a pair after field edits."""
    from schnabel.match import score_contacts
    a = db.get_contact(pair["contact_a_id"])
    b = db.get_contact(pair["contact_b_id"])
    if not a or not b:
        return pair["confidence"]
    scores = score_contacts(a, b)
    return scores["confidence"]


# -- Main TUI loop --

def run_tui(db: Database, auto_merged: int = 0):
    """Run the interactive TUI for reviewing duplicate pairs."""
    pairs = db.get_pending_pairs()
    if not pairs:
        console.print("[green]No pairs to review. All done![/green]")
        return

    idx = 0
    # History stack: (pair_index, action_type, merge_id_or_None)
    history: list[tuple[int, str, int | None]] = []
    merge_id_stack: list[int] = []
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
                console.print("\n[yellow]Progress saved. Resume with 'schnabel dedup --pending'.[/yellow]")
                stats = db.get_stats()
                console.print(f"\n[bold]Session complete.[/bold] Active contacts: {stats['active']}, "
                              f"Merges total: {stats['merges']}")
                return

            elif key == "a":
                survivor_id, absorbed_id = determine_survivor(
                    db, pair["contact_a_id"], pair["contact_b_id"]
                )
                mid = merge_contacts(
                    db, survivor_id, absorbed_id,
                    merge_type="manual", confidence=pair["confidence"],
                )
                db.update_pair_resolution(pair["id"], "manual_merged")
                history.append((idx, "merge", mid))
                merge_id_stack.append(mid)
                _inline_address_chooser(db, survivor_id)
                idx += 1
                break

            elif key == "l":
                mid = merge_contacts(
                    db, pair["contact_a_id"], pair["contact_b_id"],
                    merge_type="manual", confidence=pair["confidence"],
                )
                db.update_pair_resolution(pair["id"], "manual_merged")
                history.append((idx, "merge", mid))
                merge_id_stack.append(mid)
                _inline_address_chooser(db, pair["contact_a_id"])
                idx += 1
                break

            elif key == "r":
                mid = merge_contacts(
                    db, pair["contact_b_id"], pair["contact_a_id"],
                    merge_type="manual", confidence=pair["confidence"],
                )
                db.update_pair_resolution(pair["id"], "manual_merged")
                history.append((idx, "merge", mid))
                merge_id_stack.append(mid)
                _inline_address_chooser(db, pair["contact_b_id"])
                idx += 1
                break

            elif key == "s":
                history.append((idx, "skip", None))
                idx += 1
                break

            elif key == "n":
                db.update_pair_resolution(pair["id"], "not_dup")
                history.append((idx, "not_dup", None))
                idx += 1
                break

            elif key == "1":
                db.delete_contact(pair["contact_a_id"])
                db.update_pair_resolution(pair["id"], "skipped")
                history.append((idx, "del_left", None))
                idx += 1
                break

            elif key == "2":
                db.delete_contact(pair["contact_b_id"])
                db.update_pair_resolution(pair["id"], "skipped")
                history.append((idx, "del_right", None))
                idx += 1
                break

            elif key == "x":
                db.delete_contact(pair["contact_a_id"])
                db.delete_contact(pair["contact_b_id"])
                db.update_pair_resolution(pair["id"], "skipped")
                history.append((idx, "del_both", None))
                idx += 1
                break

            elif key == "b":
                if history:
                    prev_idx, prev_action, prev_merge_id = history.pop()
                    # Undo the previous action
                    prev_pair = pairs[prev_idx]
                    if prev_action == "merge" and prev_merge_id:
                        undo_merge(db, prev_merge_id)
                        if merge_id_stack and merge_id_stack[-1] == prev_merge_id:
                            merge_id_stack.pop()
                        db.update_pair_resolution(prev_pair["id"], "pending")
                    elif prev_action == "not_dup":
                        db.update_pair_resolution(prev_pair["id"], "pending")
                    elif prev_action == "del_left":
                        db.reactivate_contact(prev_pair["contact_a_id"])
                        db.update_pair_resolution(prev_pair["id"], "pending")
                    elif prev_action == "del_right":
                        db.reactivate_contact(prev_pair["contact_b_id"])
                        db.update_pair_resolution(prev_pair["id"], "pending")
                    elif prev_action == "del_both":
                        db.reactivate_contact(prev_pair["contact_a_id"])
                        db.reactivate_contact(prev_pair["contact_b_id"])
                        db.update_pair_resolution(prev_pair["id"], "pending")
                    elif prev_action == "skip":
                        pass  # nothing to undo for skip
                    db.commit()
                    idx = prev_idx
                else:
                    console.print("\n  [dim]Kein vorheriges Paar.[/dim]")
                    time.sleep(0.5)
                break

            elif key == "u":
                if merge_id_stack:
                    mid = merge_id_stack[-1]
                    if undo_merge(db, mid):
                        merge_id_stack.pop()
                        console.print("[magenta]Undo successful.[/magenta]")
                        # Also remove from history and go back
                        if history and history[-1][2] == mid:
                            prev_idx, _, _ = history.pop()
                            prev_pair = pairs[prev_idx]
                            db.update_pair_resolution(prev_pair["id"], "pending")
                            db.commit()
                            idx = prev_idx
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
                # Recalculate score after edit
                pair["confidence"] = _rescore_pair(db, pair)
                # stay in inner loop → re-render with updated data

            elif key == "d":
                _delete_dedup_field(db, contact_a, contact_b)
                pair["confidence"] = _rescore_pair(db, pair)
                # stay in inner loop → re-render

            elif key == "+":
                _add_dedup_field(db, contact_a, contact_b)
                pair["confidence"] = _rescore_pair(db, pair)
                # stay in inner loop → re-render

            elif key == "?":
                console.clear()
                console.print(Panel(
                    "[bold]Tastaturkürzel[/bold]\n\n"
                    "[green]a[/green]  auto-merge: reicherer Kontakt überlebt, Felder kombiniert\n"
                    "[blue]l[/blue]  links behalten: links überlebt, rechts absorbiert\n"
                    "[blue]r[/blue]  rechts behalten: rechts überlebt, links absorbiert\n"
                    "[yellow]s[/yellow]  überspringen: Paar später nochmal anschauen\n"
                    "[cyan]n[/cyan]  kein Duplikat: als verschiedene Personen markieren\n"
                    "[cyan]b[/cyan]  zurück: vorheriges Paar nochmal, Aktion rückgängig\n"
                    "[red]1[/red]  linken Kontakt löschen\n"
                    "[red]2[/red]  rechten Kontakt löschen\n"
                    "[red]x[/red]  beide Kontakte löschen\n"
                    "[magenta]u[/magenta]  undo: letzten Merge rückgängig\n"
                    "[blue]e[/blue]  Feld bearbeiten (eine Seite wählen)\n"
                    "[red]d[/red]  Feld löschen (eine Seite wählen)\n"
                    "[green]+[/green]  Feld hinzufügen (eine Seite wählen)\n"
                    "[dim]q[/dim]  beenden (Zustand gespeichert, --pending zum Fortsetzen)\n\n"
                    "[bold]Symbole:[/bold] ≡ identisch  ≃ äquivalent  ⊆/⊇ Teilmenge  ≠ verschieden\n\n"
                    "Beliebige Taste zum Fortfahren...",
                    title="Hilfe",
                ))
                readchar.readchar()
                # stay in inner loop → re-render

    # Final summary
    stats = db.get_stats()
    console.print(f"\n[bold]Session complete.[/bold] Active contacts: {stats['active']}, "
                  f"Merges total: {stats['merges']}")


# -- Address chooser --

def _render_address_list(contact_fn: str, addr_fields: list, contact_id: int,
                         progress: str = ""):
    """Render the address list for a contact."""
    from schnabel.sanitize import format_address_display

    header = f"  [bold cyan]{contact_fn}[/bold cyan] (#{contact_id})"
    if progress:
        header += f"  {progress}"
    header += f" — {len(addr_fields)} Adressen"
    console.print(header)

    for i, (_, val) in enumerate(addr_fields):
        display = format_address_display(val)
        console.print(f"  [bold green][{i + 1}][/bold green] {display}")

    keys_str = "/".join(str(i + 1) for i in range(len(addr_fields)))
    line = Text()
    line.append(f"  {keys_str}", style="bold green")
    line.append("=behalten  ")
    line.append("e", style="bold blue")
    line.append("=edit  ")
    line.append("d", style="bold red")
    line.append("=del  ")
    line.append("a", style="bold yellow")
    line.append("=alle  ")
    line.append("q", style="dim")
    line.append("=fertig")
    console.print(line)


def _address_edit_loop(db: Database, contact_id: int, progress: str = "") -> str:
    """Interactive address edit loop for a single contact.

    Returns: 'resolved' if addresses were changed, 'skip' if kept all, 'quit' to stop.
    """
    from schnabel.sanitize import (
        format_address_display, resolve_multi_addresses, _address_key,
    )

    changed = False

    while True:
        contact = db.get_contact(contact_id)
        if not contact:
            return "skip"

        addr_fields = [(f.id, f.field_value) for f in contact.fields if f.field_type == "adr"]

        # If down to 0-1 addresses, done
        if len(addr_fields) < 2:
            return "resolved" if changed else "skip"

        # If all addresses now have the same key, done
        keys = {_address_key(v) for _, v in addr_fields}
        if len(keys) < 2:
            return "resolved" if changed else "skip"

        console.print(f"\n{'─' * 50}")
        _render_address_list(contact.fn, addr_fields, contact_id, progress)

        key = readchar.readchar()

        if key == "q":
            return "quit"

        if key in ("a", "n"):
            return "resolved" if changed else "skip"

        # Keep one, archive rest
        try:
            choice = int(key) - 1
            if 0 <= choice < len(addr_fields):
                archived = resolve_multi_addresses(db, contact_id, choice)
                display = format_address_display(addr_fields[choice][1])
                console.print(f"  [green]→ {display}[/green]  ({archived} archiviert)")
                return "resolved"
        except (ValueError, IndexError):
            pass

        # Edit
        if key == "e":
            console.print("  Welche # editieren? ", end="")
            num_key = readchar.readchar()
            console.print(num_key)
            try:
                idx = int(num_key) - 1
                if 0 <= idx < len(addr_fields):
                    fid, val = addr_fields[idx]
                    display = format_address_display(val)
                    console.print(f"  Aktuell: [bold]{display}[/bold]")
                    console.print(f"  [dim]Raw: {val}[/dim]")
                    try:
                        new_val = input("  Neuer Wert: ").strip()
                    except (EOFError, KeyboardInterrupt):
                        continue
                    if new_val:
                        db.update_contact_field(fid, new_val)
                        db.commit()
                        console.print(f"  [green]Aktualisiert.[/green]")
                        changed = True
                else:
                    console.print("  [red]Ungültige Nummer.[/red]")
                    time.sleep(0.5)
            except (ValueError, IndexError):
                console.print("  [red]Ungültige Nummer.[/red]")
                time.sleep(0.5)
            continue

        # Delete
        if key == "d":
            console.print("  Welche # löschen? ", end="")
            num_key = readchar.readchar()
            console.print(num_key)
            try:
                idx = int(num_key) - 1
                if 0 <= idx < len(addr_fields):
                    fid, val = addr_fields[idx]
                    display = format_address_display(val)
                    db.delete_contact_field(fid)
                    db.commit()
                    console.print(f"  [red]{display} gelöscht.[/red]")
                    changed = True
                else:
                    console.print("  [red]Ungültige Nummer.[/red]")
                    time.sleep(0.5)
            except (ValueError, IndexError):
                console.print("  [red]Ungültige Nummer.[/red]")
                time.sleep(0.5)
            continue


def _inline_address_chooser(db: Database, contact_id: int) -> bool:
    """Prompt user to manage addresses after a merge.

    Returns True if addresses were changed, False if skipped.
    """
    from schnabel.sanitize import _address_key

    contact = db.get_contact(contact_id)
    if not contact:
        return False

    addr_fields = [(f.id, f.field_value) for f in contact.fields if f.field_type == "adr"]
    if len(addr_fields) < 2:
        return False

    keys = {_address_key(v) for _, v in addr_fields}
    if len(keys) < 2:
        return False

    result = _address_edit_loop(db, contact_id)
    return result == "resolved"


def address_chooser(db: Database, contact_ids: list[int]) -> int:
    """Batch address chooser for contacts with multiple addresses.

    Returns number of contacts where addresses were resolved.
    """
    from schnabel.sanitize import _address_key

    resolved = 0
    total = len(contact_ids)

    for i, cid in enumerate(contact_ids):
        contact = db.get_contact(cid)
        if not contact:
            continue

        addr_fields = [(f.id, f.field_value) for f in contact.fields if f.field_type == "adr"]
        if len(addr_fields) < 2:
            continue

        keys = {_address_key(v) for _, v in addr_fields}
        if len(keys) < 2:
            continue

        progress = f"[{i + 1}/{total}]"
        result = _address_edit_loop(db, cid, progress)

        if result == "resolved":
            resolved += 1
        elif result == "quit":
            break

    return resolved
