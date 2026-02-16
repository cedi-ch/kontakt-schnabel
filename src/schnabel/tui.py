"""TUI review interface for manual dedup decisions."""

import sys

import readchar
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from schnabel.db import Database
from schnabel.merge import determine_survivor, merge_contacts, undo_merge
from schnabel.normalize import normalize_email, normalize_phone


console = Console()


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

    # Comparison table
    rows = []

    # FN
    sym = _compare_symbol(contact_a.fn, contact_b.fn)
    rows.append(("FN:", contact_a.fn or "(none)", sym, contact_b.fn or "(none)"))

    # Emails
    emails_a = contact_a.emails
    emails_b = contact_b.emails
    max_emails = max(len(emails_a), len(emails_b), 1)
    for i in range(max_emails):
        ea = emails_a[i] if i < len(emails_a) else "(none)"
        eb = emails_b[i] if i < len(emails_b) else "(none)"
        na = normalize_email(ea) if ea != "(none)" else ""
        nb = normalize_email(eb) if eb != "(none)" else ""
        sym = _compare_symbol(ea, eb, na, nb)
        label = "EMAIL:" if i == 0 else ""
        rows.append((label, ea, sym, eb))

    # Phones
    phones_a = contact_a.phones
    phones_b = contact_b.phones
    max_phones = max(len(phones_a), len(phones_b), 1)
    for i in range(max_phones):
        pa = phones_a[i] if i < len(phones_a) else "(none)"
        pb = phones_b[i] if i < len(phones_b) else "(none)"
        na = normalize_phone(pa) or "" if pa != "(none)" else ""
        nb = normalize_phone(pb) or "" if pb != "(none)" else ""
        sym = _compare_symbol(pa, pb, na, nb)
        label = "TEL:" if i == 0 else ""
        rows.append((label, pa, sym, pb))

    # Photo
    photo_a = f"[{contact_a.photos[0].photo_format} {contact_a.photos[0].width}×{contact_a.photos[0].height}]" if contact_a.photos else "(none)"
    photo_b = f"[{contact_b.photos[0].photo_format} {contact_b.photos[0].width}×{contact_b.photos[0].height}]" if contact_b.photos else "(none)"
    sym = _compare_symbol(photo_a, photo_b)
    rows.append(("PHOTO:", photo_a, sym, photo_b))

    # ORG
    orgs_a = contact_a.orgs
    orgs_b = contact_b.orgs
    if orgs_a or orgs_b:
        oa = orgs_a[0] if orgs_a else "(none)"
        ob = orgs_b[0] if orgs_b else "(none)"
        sym = _compare_symbol(oa, ob)
        rows.append(("ORG:", _truncate(oa), sym, _truncate(ob)))

    # Addresses
    addrs_a = contact_a.addresses
    addrs_b = contact_b.addresses
    if addrs_a or addrs_b:
        aa = addrs_a[0] if addrs_a else "(none)"
        ab = addrs_b[0] if addrs_b else "(none)"
        sym = _compare_symbol(aa, ab)
        rows.append(("ADR:", _truncate(aa), sym, _truncate(ab)))

    # Render rows
    for label, left, sym, right in rows:
        left_str = _truncate(left, 28)
        line = f"  {label:<8}{left_str:<28} {sym}   {right}"
        console.print(line)

    console.print("─" * console.width)

    # Action bar
    actions = Text()
    actions.append(" [a]", style="bold green")
    actions.append("uto-pick ")
    actions.append("[m]", style="bold blue")
    actions.append("erge L→R ")
    actions.append("[M]", style="bold blue")
    actions.append("erge R→L ")
    actions.append("[s]", style="bold yellow")
    actions.append("kip ")
    actions.append("[n]", style="bold red")
    actions.append("ot-dup ")
    actions.append("[d]", style="bold red")
    actions.append("el-L ")
    actions.append("[D]", style="bold red")
    actions.append("el-R ")
    actions.append("[u]", style="bold magenta")
    actions.append("ndo")
    console.print(actions)


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

        contact_a = db.get_contact(pair["contact_a_id"])
        contact_b = db.get_contact(pair["contact_b_id"])

        if not contact_a or not contact_b or not contact_a.is_active or not contact_b.is_active:
            idx += 1
            continue

        render_pair(pair, contact_a, contact_b, idx, total, auto_merged)

        key = readchar.readchar()

        if key == "q":
            console.print("\n[yellow]Progress saved. Resume anytime with 'schnabel dedup'.[/yellow]")
            break

        elif key == "a":
            # Auto-pick: merge lesser into richer
            survivor_id, absorbed_id = determine_survivor(
                db, pair["contact_a_id"], pair["contact_b_id"]
            )
            last_merge_id = merge_contacts(
                db, survivor_id, absorbed_id,
                merge_type="manual", confidence=pair["confidence"],
            )
            db.update_pair_resolution(pair["id"], "manual_merged")
            idx += 1

        elif key == "m":
            # Merge left into right (right survives)
            last_merge_id = merge_contacts(
                db, pair["contact_b_id"], pair["contact_a_id"],
                merge_type="manual", confidence=pair["confidence"],
            )
            db.update_pair_resolution(pair["id"], "manual_merged")
            idx += 1

        elif key == "M":
            # Merge right into left (left survives)
            last_merge_id = merge_contacts(
                db, pair["contact_a_id"], pair["contact_b_id"],
                merge_type="manual", confidence=pair["confidence"],
            )
            db.update_pair_resolution(pair["id"], "manual_merged")
            idx += 1

        elif key == "s":
            # Skip
            idx += 1

        elif key == "n":
            # Not a duplicate
            db.update_pair_resolution(pair["id"], "not_dup")
            idx += 1

        elif key == "d":
            # Delete left
            db.delete_contact(pair["contact_a_id"])
            db.update_pair_resolution(pair["id"], "skipped")
            idx += 1

        elif key == "D":
            # Delete right
            db.delete_contact(pair["contact_b_id"])
            db.update_pair_resolution(pair["id"], "skipped")
            idx += 1

        elif key == "u":
            # Undo last action
            if last_merge_id:
                if undo_merge(db, last_merge_id):
                    console.print("[magenta]Undo successful.[/magenta]")
                    last_merge_id = None
                    if idx > 0:
                        idx -= 1
                else:
                    console.print("[red]Nothing to undo.[/red]")
            else:
                console.print("[red]Nothing to undo.[/red]")
            import time
            time.sleep(0.5)

        elif key == "?":
            console.clear()
            console.print(Panel(
                "[bold]Keyboard shortcuts[/bold]\n\n"
                "[green]a[/green]  Auto-pick: merge lesser into richer\n"
                "[blue]m[/blue]  Merge left → right (right survives)\n"
                "[blue]M[/blue]  Merge right → left (left survives)\n"
                "[yellow]s[/yellow]  Skip (revisit later)\n"
                "[red]n[/red]  Not a duplicate (never suggest again)\n"
                "[red]d[/red]  Delete left contact\n"
                "[red]D[/red]  Delete right contact\n"
                "[magenta]u[/magenta]  Undo last action\n"
                "[dim]q[/dim]  Quit (progress saved)\n\n"
                "[bold]Symbols:[/bold] ≡ identical  ≃ equivalent  ⊆/⊇ subset/superset  ≠ different\n\n"
                "Press any key to continue...",
                title="Help",
            ))
            readchar.readchar()

    # Final summary
    stats = db.get_stats()
    console.print(f"\n[bold]Session complete.[/bold] Active contacts: {stats['active']}, "
                  f"Merges total: {stats['merges']}")
