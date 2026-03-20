"""TUI for splitting a VCF file into multiple named target files."""

import base64
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import readchar
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from schnabel.export import contact_to_vcard
from schnabel.model import Contact, ContactField, Photo

console = Console()

# Field type selection map (same as rawtui)
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
    "categories": "CAT",
}


def _truncate(s: str, maxlen: int = 40) -> str:
    from schnabel.ui_helpers import truncate
    return truncate(s, maxlen)


@dataclass
class SplitTarget:
    """A named target file for split output."""
    name: str
    key: str  # "1", "2", etc.


def _sanitize_filename(name: str) -> str:
    """Sanitize a filename: replace special chars with underscore."""
    name = re.sub(r"[^\w\s\-]", "_", name)
    name = re.sub(r"\s+", "_", name)
    return name.strip("_") or "output"


def _serialize_contact(contact: Contact) -> dict:
    """Serialize a Contact to a JSON-safe dict (including photo data as base64)."""
    return {
        "fn": contact.fn,
        "family_name": contact.family_name,
        "given_name": contact.given_name,
        "additional_names": contact.additional_names,
        "prefix": contact.prefix,
        "suffix": contact.suffix,
        "uid": contact.uid,
        "fields": [
            {
                "field_type": f.field_type,
                "field_value": f.field_value,
                "field_params": f.field_params,
            }
            for f in contact.fields
        ],
        "photos": [
            {
                "photo_data_b64": base64.b64encode(p.photo_data).decode("ascii") if p.photo_data else "",
                "photo_format": p.photo_format,
                "byte_hash": p.byte_hash,
                "width": p.width,
                "height": p.height,
            }
            for p in contact.photos
        ],
    }


def _deserialize_contact(data: dict) -> Contact:
    """Reconstruct a Contact from serialized dict (including photo data)."""
    # Support both old format (photos_meta without data) and new format (photos with data)
    photos = []
    if "photos" in data:
        for p in data["photos"]:
            photo_data = base64.b64decode(p["photo_data_b64"]) if p.get("photo_data_b64") else b""
            photos.append(Photo(
                photo_data=photo_data,
                photo_format=p["photo_format"],
                byte_hash=p.get("byte_hash", ""),
                width=p["width"],
                height=p["height"],
            ))
    elif "photos_meta" in data:
        # Legacy format — photos without data
        for p in data["photos_meta"]:
            photos.append(Photo(
                photo_data=b"",
                photo_format=p["photo_format"],
                width=p["width"],
                height=p["height"],
            ))

    contact = Contact(
        fn=data["fn"],
        family_name=data["family_name"],
        given_name=data["given_name"],
        additional_names=data.get("additional_names", ""),
        prefix=data.get("prefix", ""),
        suffix=data.get("suffix", ""),
        uid=data.get("uid", ""),
        fields=[
            ContactField(
                field_type=f["field_type"],
                field_value=f["field_value"],
                field_params=f.get("field_params", {}),
            )
            for f in data["fields"]
        ],
        photos=photos,
    )
    return contact


def save_split_state(contacts: list[Contact], targets: list[SplitTarget],
                     assignments: dict[int, int], deleted: set[int],
                     input_file: str, state_path: Path):
    """Save split session state to a JSON file."""
    data = {
        "input_file": input_file,
        "targets": [{"name": t.name, "key": t.key} for t in targets],
        "assignments": {str(k): v for k, v in assignments.items()},
        "deleted": sorted(deleted),
        "contacts": [_serialize_contact(c) for c in contacts],
    }
    state_path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write: write to temp file then rename to prevent corruption
    tmp_path = state_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.rename(state_path)


def load_split_state(state_path: Path) -> dict | None:
    """Load saved split state. Returns dict with contacts/targets/assignments/deleted or None."""
    if not state_path.exists():
        return None
    data = json.loads(state_path.read_text(encoding="utf-8"))
    contacts = [_deserialize_contact(c) for c in data["contacts"]]
    targets = [SplitTarget(name=t["name"], key=t["key"]) for t in data["targets"]]
    assignments = {int(k): v for k, v in data["assignments"].items()}
    deleted = set(data["deleted"])
    return {
        "input_file": data["input_file"],
        "contacts": contacts,
        "targets": targets,
        "assignments": assignments,
        "deleted": deleted,
    }


def _start_dialog() -> list[SplitTarget] | None:
    """Interactive start dialog to choose number and names of target files.

    Returns list of SplitTarget or None if cancelled.
    """
    console.print("\n[bold cyan]schnabel split[/bold cyan] \u2014 VCF-Datei aufteilen\n")

    while True:
        try:
            raw = input("Anzahl Zieldateien (1\u20135, q=abbrechen): ").strip()
        except (EOFError, KeyboardInterrupt):
            return None
        if raw.lower() == "q":
            return None
        try:
            count = int(raw)
            if 1 <= count <= 5:
                break
        except ValueError:
            pass
        console.print("[red]Bitte eine Zahl von 1 bis 5 eingeben.[/red]")

    targets = []
    for i in range(count):
        while True:
            try:
                name = input(f"  Name f\u00fcr Datei [{i + 1}]: ").strip()
            except (EOFError, KeyboardInterrupt):
                return None
            if name:
                break
            console.print("  [red]Name darf nicht leer sein.[/red]")
        targets.append(SplitTarget(name=name, key=str(i + 1)))

    console.print()
    for t in targets:
        safe = _sanitize_filename(t.name)
        console.print(f"  [bold]{t.key}[/bold] \u2192 {safe}.vcf")
    console.print()

    return targets


def _render_contact(contact: Contact, idx: int, total: int,
                    assigned: int, skipped: int,
                    targets: list[SplitTarget],
                    assignments: dict[int, int]):
    """Render the split review screen for a single contact."""
    console.clear()

    # Header
    header = Text()
    header.append("schnabel split", style="bold cyan")
    header.append(f" \u2014 {total} Kontakte")
    header.append("                      ", style="dim")
    header.append("[q]uit [?]hilfe", style="dim")
    console.print(header)
    console.print("\u2500" * console.width)

    # Progress
    info = Text()
    info.append(f"  Kontakt {idx + 1}/{total}", style="bold")
    info.append(f"  ({assigned} zugewiesen, {skipped} \u00fcbersprungen)")
    console.print(info)
    console.print("\u2500" * console.width)

    # Two-column layout
    # Left: contact fields, Right: target files with counts
    left_lines = []
    right_lines = []

    # -- Left column: contact details --
    left_lines.append(("FN:", contact.fn or "(kein Name)"))

    for f in contact.fields:
        label = FIELD_LABELS.get(f.field_type, f.field_type.upper())
        left_lines.append((label + ":", f.field_value))

    # Photo info
    if contact.photos:
        p = contact.photos[0]
        left_lines.append(("PHOTO:", f"[{p.photo_format} {p.width}\u00d7{p.height}]"))

    # -- Right column: target files --
    right_lines.append("Zieldateien:")
    right_lines.append("")
    for i, t in enumerate(targets):
        count = sum(1 for v in assignments.values() if v == i)
        right_lines.append(f"[{t.key}] {_sanitize_filename(t.name)}.vcf" + f"  ({count})")

    # Render columns
    col_width = max(console.width // 2 - 2, 30)
    max_rows = max(len(left_lines), len(right_lines))

    for row in range(max_rows):
        line = Text()

        # Left
        if row < len(left_lines):
            label, value = left_lines[row]
            left_str = f"  {label:<10}{_truncate(value, col_width - 12)}"
        else:
            left_str = ""
        line.append(f"{left_str:<{col_width}}")

        line.append("\u2502 ", style="dim")

        # Right
        if row < len(right_lines):
            rtext = right_lines[row]
            if row == 0:
                line.append(rtext, style="bold")
            else:
                line.append(rtext)

        console.print(line)

    console.print("\u2500" * console.width)

    # Action bar
    keys_str = f"1-{len(targets)}" if len(targets) > 1 else "1"
    line1 = Text()
    line1.append(f" {keys_str}", style="bold green")
    line1.append("=zuweisen  ")
    line1.append("s", style="bold yellow")
    line1.append("=\u00fcberspringen  ")
    line1.append("b", style="bold cyan")
    line1.append("=zur\u00fcck  ")
    line1.append("u", style="bold magenta")
    line1.append("=r\u00fcckg\u00e4ngig")
    console.print(line1)

    line2 = Text()
    line2.append(" e", style="bold blue")
    line2.append("=bearbeiten  ")
    line2.append("d", style="bold red")
    line2.append("=feld l\u00f6schen  ")
    line2.append("+", style="bold green")
    line2.append("=feld hinzuf\u00fcgen  ")
    line2.append("x", style="bold red")
    line2.append("=kontakt l\u00f6schen")
    console.print(line2)

    line3 = Text()
    line3.append(" ?", style="dim")
    line3.append("=hilfe  ")
    line3.append("q", style="dim")
    line3.append("=quit")
    console.print(line3)


# -- Inline editing helpers (in-memory, no DB) --

def _show_fields(contact: Contact) -> list[tuple[int, str, str]]:
    """Show numbered field list. #0=FN, #1..N=fields."""
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
    """Prompt for field number via readchar. Returns number or None."""
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
    console.print("  [red]Ung\u00fcltige Feldnummer.[/red]")
    time.sleep(0.7)
    return None


def _edit_field(contact: Contact):
    """Edit a field value in-memory."""
    console.print("\n  Felder:")
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
            contact.fn = new_val
    else:
        f = contact.fields[num - 1]
        label = FIELD_LABELS.get(f.field_type, f.field_type.upper())
        console.print(f"  Aktuell: [bold]{label}[/bold] = {f.field_value}")
        try:
            new_val = input("  Neuer Wert: ").strip()
        except (EOFError, KeyboardInterrupt):
            return
        if new_val:
            f.field_value = new_val


def _delete_field(contact: Contact):
    """Delete a field in-memory."""
    console.print("\n  Felder:")
    entries = _show_fields(contact)

    num = _prompt_field_number(entries)
    if num is None:
        return

    if num == 0:
        console.print("  [red]FN kann nicht gel\u00f6scht werden.[/red]")
        time.sleep(0.7)
        return

    removed = contact.fields.pop(num - 1)
    label = FIELD_LABELS.get(removed.field_type, removed.field_type.upper())
    console.print(f"  [red]{label} gel\u00f6scht.[/red]")
    time.sleep(0.5)


def _add_field(contact: Contact):
    """Add a new field in-memory."""
    console.print("\n  Typ w\u00e4hlen: ", end="")
    for k, v in FIELD_TYPES.items():
        label = FIELD_LABELS[v]
        console.print(f"[bold]{k}[/bold]={label} ", end="")
    console.print()

    key = readchar.readchar()
    if key not in FIELD_TYPES:
        console.print("  [red]Ung\u00fcltige Auswahl.[/red]")
        time.sleep(0.7)
        return

    field_type = FIELD_TYPES[key]
    try:
        value = input("  Wert: ").strip()
    except (EOFError, KeyboardInterrupt):
        return
    if value:
        contact.fields.append(ContactField(field_type=field_type, field_value=value))
        label = FIELD_LABELS[field_type]
        console.print(f"  [green]{label} hinzugef\u00fcgt.[/green]")
        time.sleep(0.5)


def _show_help(num_targets: int):
    """Show help screen."""
    console.clear()
    keys_str = f"1-{num_targets}" if num_targets > 1 else "1"
    console.print(Panel(
        "[bold]Tastaturk\u00fcrzel[/bold]\n\n"
        f"[green]{keys_str}[/green]  Kontakt einer Zieldatei zuweisen, weiter\n"
        "[yellow]s[/yellow]  \u00fcberspringen: sp\u00e4ter nochmal anschauen\n"
        "[cyan]b[/cyan]  zur\u00fcck: vorherigen Kontakt nochmal anzeigen\n"
        "[magenta]u[/magenta]  r\u00fcckg\u00e4ngig: letzte Aktion zur\u00fccknehmen\n"
        "[blue]e[/blue]  bearbeiten: Feldwert \u00e4ndern (bleibt beim Kontakt)\n"
        "[red]d[/red]  feld l\u00f6schen: ein Feld entfernen (bleibt beim Kontakt)\n"
        "[green]+[/green]  feld hinzuf\u00fcgen: neues Feld eingeben (bleibt beim Kontakt)\n"
        "[red]x[/red]  kontakt l\u00f6schen: ganzen Kontakt entfernen, weiter\n"
        "[dim]?[/dim]  diese Hilfe\n"
        "[dim]q[/dim]  beenden (Zustand wird gespeichert, --pending zum Fortsetzen)\n\n"
        "Beliebige Taste zum Fortfahren...",
        title="Hilfe",
    ))
    readchar.readchar()


# -- File output --

def write_split_files(contacts: list[Contact], targets: list[SplitTarget],
                      assignments: dict[int, int], output_dir: Path,
                      write_rest: bool = True,
                      deleted: set[int] | None = None) -> dict[str, int]:
    """Write assigned contacts into target VCF files.

    Returns dict of filename -> count.
    """
    deleted = deleted or set()
    output_dir.mkdir(parents=True, exist_ok=True)
    written = {}

    for target_idx, target in enumerate(targets):
        target_contacts = [
            contacts[ci] for ci, ti in sorted(assignments.items()) if ti == target_idx
        ]
        if not target_contacts:
            continue

        # Sort by FN
        target_contacts.sort(key=lambda c: (c.fn or "").lower())

        vcards = [contact_to_vcard(c) for c in target_contacts]
        content = "\r\n".join(vcards)
        if content:
            content += "\r\n"

        filename = f"{_sanitize_filename(target.name)}.vcf"
        (output_dir / filename).write_text(content, encoding="utf-8")
        written[filename] = len(target_contacts)

    # Rest file for unassigned (and not deleted) contacts
    if write_rest:
        assigned_indices = set(assignments.keys())
        rest_contacts = [
            c for i, c in enumerate(contacts)
            if i not in assigned_indices and i not in deleted
        ]
        if rest_contacts:
            rest_contacts.sort(key=lambda c: (c.fn or "").lower())
            vcards = [contact_to_vcard(c) for c in rest_contacts]
            content = "\r\n".join(vcards)
            if content:
                content += "\r\n"
            (output_dir / "rest.vcf").write_text(content, encoding="utf-8")
            written["rest.vcf"] = len(rest_contacts)

    return written


# -- Main TUI loop --

@dataclass
class SplitResult:
    """Result of run_split_tui: either pending (quit early) or finished."""
    contacts: list[Contact]
    targets: list[SplitTarget]
    assignments: dict[int, int]
    deleted: set[int]
    pending: bool  # True if user quit with unprocessed contacts


def run_split_tui(contacts: list[Contact], targets: list[SplitTarget],
                  output_dir: Path, write_rest: bool = True,
                  initial_assignments: dict[int, int] | None = None,
                  initial_deleted: set[int] | None = None) -> SplitResult:
    """Run the interactive split TUI.

    Returns SplitResult with state and pending flag.
    """
    if not contacts:
        console.print("[yellow]Keine Kontakte zum Aufteilen.[/yellow]")
        return SplitResult(contacts, targets, {}, set(), pending=False)

    # assignments: contact_index -> target_index
    assignments: dict[int, int] = dict(initial_assignments) if initial_assignments else {}
    # deleted contact indices
    deleted: set[int] = set(initial_deleted) if initial_deleted else set()
    # history for undo: list of (action, contact_index, extra)
    # action: "assign" (extra=target_idx), "skip" (extra=None), "delete" (extra=None)
    history: list[tuple[str, int, int | None]] = []

    # Find first unprocessed contact
    idx = 0
    for i in range(len(contacts)):
        if i not in assignments and i not in deleted:
            idx = i
            break

    key = None
    while idx < len(contacts):
        # Skip deleted contacts
        if idx in deleted:
            idx += 1
            continue

        contact = contacts[idx]

        # Inner loop: stay on this contact until navigation
        while True:
            visited_not_assigned = sum(
                1 for act, _, _ in history if act == "skip"
            )

            _render_contact(contact, idx, len(contacts),
                            len(assignments), visited_not_assigned,
                            targets, assignments)

            key = readchar.readchar()

            if key == "q":
                break

            elif key in [str(i + 1) for i in range(len(targets))]:
                target_idx = int(key) - 1
                assignments[idx] = target_idx
                history.append(("assign", idx, target_idx))
                idx += 1
                break

            elif key == "s":
                # Remove any previous assignment for this contact
                assignments.pop(idx, None)
                history.append(("skip", idx, None))
                idx += 1
                break

            elif key == "x":
                assignments.pop(idx, None)
                deleted.add(idx)
                history.append(("delete", idx, None))
                console.print(f"\n  [red]Kontakt gel\u00f6scht.[/red]")
                time.sleep(0.5)
                idx += 1
                break

            elif key == "b":
                if history:
                    act, prev_ci, prev_extra = history.pop()
                    if act == "assign":
                        assignments.pop(prev_ci, None)
                    elif act == "delete":
                        deleted.discard(prev_ci)
                    idx = prev_ci
                else:
                    console.print("\n  [dim]Kein vorheriger Kontakt.[/dim]")
                    time.sleep(0.5)
                break

            elif key == "u":
                if history:
                    act, prev_ci, prev_extra = history.pop()
                    if act == "assign":
                        assignments.pop(prev_ci, None)
                    elif act == "delete":
                        deleted.discard(prev_ci)
                    idx = prev_ci
                    break
                else:
                    console.print("\n  [dim]Nichts zum R\u00fcckg\u00e4ngigmachen.[/dim]")
                    time.sleep(0.5)
                # stay in inner loop

            elif key == "e":
                _edit_field(contact)
                # stay in inner loop -> re-render

            elif key == "d":
                _delete_field(contact)

            elif key == "+":
                _add_field(contact)

            elif key == "?":
                _show_help(len(targets))

        if key == "q":
            break

    # Check if there are unprocessed contacts
    has_pending = any(
        i not in assignments and i not in deleted
        for i in range(len(contacts))
    )

    return SplitResult(
        contacts=contacts,
        targets=targets,
        assignments=assignments,
        deleted=deleted,
        pending=has_pending,
    )
