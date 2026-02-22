"""Click CLI: schnabel import, analyze, normalize, match, dedup, export, status."""

from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from schnabel.config import DEFAULT_DB_PATH, DEFAULT_DATA_DIR, DEFAULT_OUTPUT_DIR, make_output_dir

console = Console()


def get_db(db_path: Path) -> "Database":
    from schnabel.db import Database
    return Database(db_path)


@click.group()
@click.option("--db", "db_path", type=click.Path(), default=str(DEFAULT_DB_PATH),
              help="Path to SQLite database.")
@click.option("--no-export", is_flag=True, help="Skip automatic VCF export after data changes.")
@click.pass_context
def cli(ctx, db_path, no_export):
    """kontakt-schnabel: merge, deduplicate, and sanitize vCard files."""
    ctx.ensure_object(dict)
    ctx.obj["db_path"] = Path(db_path)
    ctx.obj["no_export"] = no_export


# ── Import ──────────────────────────────────────────────────────────────────

@cli.command("import")
@click.argument("files", nargs=-1, type=click.Path(exists=True))
@click.option("--dir", "input_dir", type=click.Path(exists=True),
              help="Import all .vcf files from a directory.")
@click.pass_context
def import_cmd(ctx, files, input_dir):
    """Import vCard files into the database."""
    from schnabel.classify import classify_contact
    from schnabel.reader import file_md5, parse_vcf_file

    db = get_db(ctx.obj["db_path"])

    file_paths = [Path(f) for f in files]
    if input_dir:
        file_paths.extend(sorted(Path(input_dir).glob("*.vcf")))

    if not file_paths:
        file_paths = sorted(DEFAULT_DATA_DIR.glob("*.vcf"))

    if not file_paths:
        console.print("[red]No VCF files found.[/red]")
        return

    already_imported = db.get_imported_hashes()
    total_contacts = 0
    total_files = 0

    with console.status("[bold cyan]Importing...") as status:
        for fp in file_paths:
            md5 = file_md5(fp)
            if md5 in already_imported:
                console.print(f"  [dim]Skip (already imported): {fp.name}[/dim]")
                continue

            status.update(f"[bold cyan]Parsing {fp.name}...")
            contacts, encoding = parse_vcf_file(fp)
            if not contacts:
                console.print(f"  [yellow]Empty or unparseable: {fp.name}[/yellow]")
                continue

            import_id = db.add_import_source(str(fp), md5, encoding)

            for contact in contacts:
                contact.source_import_id = import_id
                contact.category = classify_contact(contact)
                db.insert_contact(contact)

            db.update_import_count(import_id, len(contacts))
            db.commit()

            total_contacts += len(contacts)
            total_files += 1
            console.print(
                f"  [green]✓[/green] {fp.name}: {len(contacts)} contacts "
                f"(enc: {encoding})"
            )

    console.print(f"\n[bold green]Imported {total_contacts} contacts "
                  f"from {total_files} files.[/bold green]")

    # Show classification summary
    stats = db.get_stats()
    _print_stats_table(stats)
    db.close()

    _log_session_event("import", f"{total_contacts} contacts from {total_files} files")
    _auto_export(ctx, "import")


# ── Analyze ─────────────────────────────────────────────────────────────────

@cli.command()
@click.option("-v", "--verbose", is_flag=True, help="Show detailed breakdown.")
@click.pass_context
def analyze(ctx, verbose):
    """Show contact statistics and analysis."""
    db = get_db(ctx.obj["db_path"])
    stats = db.get_stats()
    _print_stats_table(stats)

    if verbose:
        console.print("\n[bold]Category breakdown:[/bold]")
        for cat in ("real", "stub", "spam", "unknown"):
            contacts = db.get_contacts_by_category(cat)
            if contacts:
                console.print(f"\n  [bold]{cat.upper()}[/bold] ({len(contacts)}):")
                for c in contacts[:10]:
                    emails = ", ".join(c.emails[:2]) or "(no email)"
                    phones = ", ".join(c.phones[:2]) or "(no phone)"
                    console.print(f"    {c.fn or '(no name)':<30} {emails:<35} {phones}")
                if len(contacts) > 10:
                    console.print(f"    ... and {len(contacts) - 10} more")

    db.close()


# ── Normalize ───────────────────────────────────────────────────────────────

@cli.command()
@click.pass_context
def normalize(ctx):
    """Normalize contacts (emails, phones, names) for matching."""
    from schnabel.normalize import normalize_contacts

    db = get_db(ctx.obj["db_path"])
    stats = db.get_stats()
    if stats["active"] == 0:
        console.print("[red]No contacts in database. Run 'schnabel import' first.[/red]")
        db.close()
        return

    with console.status("[bold cyan]Normalizing contacts...") as status:
        def progress(current, total):
            status.update(f"[bold cyan]Normalizing... {current}/{total}")

        normalize_contacts(db, progress_callback=progress)

    console.print(f"[bold green]Normalized {stats['active']} contacts.[/bold green]")
    db.close()

    _log_session_event("normalize", f"{stats['active']} contacts normalized")
    _auto_export(ctx, "normalize")


# ── Sanitize ───────────────────────────────────────────────────────────────

@cli.command()
@click.pass_context
def sanitize(ctx):
    """Sanitize contacts: deduplicate phones, emails, addresses within each contact."""
    from schnabel.sanitize import sanitize_contacts

    db = get_db(ctx.obj["db_path"])
    stats = db.get_stats()
    if stats["active"] == 0:
        console.print("[red]No contacts in database. Run 'schnabel import' first.[/red]")
        db.close()
        return

    with console.status("[bold cyan]Sanitizing contacts...") as status:
        def progress(current, total):
            status.update(f"[bold cyan]Sanitizing... {current}/{total}")

        report = sanitize_contacts(db, progress_callback=progress)

    # Show report as Rich table
    from rich.table import Table
    table = Table(title="Sanitize Report", show_header=True, border_style="cyan")
    table.add_column("Type", style="bold")
    table.add_column("Removed", justify="right", style="red")
    table.add_column("Reformatted", justify="right", style="yellow")

    for key in ("empty", "tel", "email", "adr", "url", "text"):
        removed = report.removed.get(key, 0)
        reformatted = report.reformatted.get(key, 0)
        if removed > 0 or reformatted > 0:
            table.add_row(key.upper(), str(removed), str(reformatted))

    if report.total_removed > 0 or report.total_reformatted > 0:
        table.add_row("TOTAL", str(report.total_removed), str(report.total_reformatted),
                       style="bold")
        console.print(table)
    else:
        console.print("[green]Alle Kontakte bereits sauber — keine Änderungen.[/green]")

    db.close()

    _log_session_event("sanitize",
                       f"{report.total_removed} removed, {report.total_reformatted} reformatted")
    _auto_export(ctx, "sanitize")


# ── Match ───────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--min-confidence", type=float, default=0.10,
              help="Minimum confidence to store a pair.")
@click.pass_context
def match(ctx, min_confidence):
    """Find duplicate candidate pairs and score them."""
    from schnabel.match import run_matching

    db = get_db(ctx.obj["db_path"])
    stats = db.get_stats()
    if stats["active"] == 0:
        console.print("[red]No contacts. Run 'schnabel import' first.[/red]")
        db.close()
        return

    with console.status("[bold cyan]Finding duplicate candidates...") as status:
        def progress(current, total, stored):
            status.update(
                f"[bold cyan]Scoring pairs... {current}/{total} "
                f"({stored} above threshold)"
            )

        stored = run_matching(db, min_confidence=min_confidence, progress_callback=progress)

    console.print(f"[bold green]Found {stored} candidate pairs.[/bold green]")

    # Show distribution
    pairs = db.get_pending_pairs()
    if pairs:
        high = sum(1 for p in pairs if p["confidence"] >= 0.90)
        med = sum(1 for p in pairs if 0.70 <= p["confidence"] < 0.90)
        low = sum(1 for p in pairs if p["confidence"] < 0.70)
        console.print(f"  [green]High (≥90%): {high}[/green]  "
                      f"[yellow]Medium (70–90%): {med}[/yellow]  "
                      f"[red]Low (<70%): {low}[/red]")

    db.close()


# ── Dedup ───────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--auto-only", is_flag=True, help="Only auto-merge, no TUI.")
@click.option("--aggressiveness", "-a", type=float, default=0.5,
              help="Aggressiveness 0.0–1.0 (default 0.5).")
@click.option("--pending", is_flag=True,
              help="Resume: skip auto-merge, go directly to TUI with remaining pairs.")
@click.pass_context
def dedup(ctx, auto_only, aggressiveness, pending):
    """Deduplicate contacts: auto-merge then review remaining pairs.

    Use --pending to resume where you left off (skips auto-merge phase).
    """
    from schnabel.merge import aggressiveness_to_threshold, auto_resolve

    db = get_db(ctx.obj["db_path"])
    merged = 0

    if not pending:
        threshold = aggressiveness_to_threshold(aggressiveness)
        console.print(f"Aggressiveness: {aggressiveness:.1f} → threshold: {threshold:.3f}")

        # Auto-resolve phase
        with console.status("[bold cyan]Auto-merging obvious duplicates...") as status:
            def progress(current, total, merged_count):
                status.update(
                    f"[bold cyan]Auto-merging... {current}/{total} pairs, "
                    f"{merged_count} merged"
                )

            merged = auto_resolve(db, aggressiveness=aggressiveness, progress_callback=progress)

        console.print(f"[bold green]Auto-merged {merged} pairs.[/bold green]")

    if auto_only:
        stats = db.get_stats()
        console.print(f"Remaining pending pairs: {stats['pending_pairs']}")
        _print_stats_table(stats)
        db.close()
        _log_session_event("dedup", f"{merged} auto-merged (--auto-only)")
        _auto_export(ctx, "dedup")
        return

    if pending:
        pairs = db.get_pending_pairs()
        console.print(f"[bold cyan]Fortsetzen:[/bold cyan] {len(pairs)} offene Paare")

    # TUI phase
    from schnabel.tui import run_tui
    run_tui(db, auto_merged=merged)
    db.close()

    _log_session_event("dedup", f"{merged} auto-merged + TUI session")
    _auto_export(ctx, "dedup")


# ── Undo ────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("merge_id", type=int)
@click.pass_context
def undo(ctx, merge_id):
    """Undo a specific merge by its ID."""
    from schnabel.merge import undo_merge

    db = get_db(ctx.obj["db_path"])
    if undo_merge(db, merge_id):
        console.print(f"[green]Merge #{merge_id} undone.[/green]")
    else:
        console.print(f"[red]Merge #{merge_id} not found.[/red]")
    db.close()


# ── Export ──────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("output_dir", type=click.Path(), required=False, default=None)
@click.option("--normalize-photos/--preserve-photos", default=True,
              help="Normalize photos (resize, JPEG convert). Default: normalize.")
@click.option("--max-lines", type=int, default=0,
              help="Split output files at this line limit (e.g. 10000 for Infomaniak).")
@click.pass_context
def export(ctx, output_dir, normalize_photos, max_lines):
    """Export contacts to clean vCard 3.0 files.

    OUTPUT_DIR is optional; if omitted, a timestamped directory is created.
    """
    from schnabel.export import export_contacts

    db = get_db(ctx.obj["db_path"])
    output_path = Path(output_dir) if output_dir else make_output_dir("export")

    with console.status("[bold cyan]Exporting..."):
        counts = export_contacts(db, output_path, normalize_photos=normalize_photos,
                                 max_lines=max_lines)

    console.print(f"\n[bold green]Export complete → {output_path}/[/bold green]")
    for category, count in counts.items():
        console.print(f"  {category}: {count} contacts")
    if max_lines > 0:
        from glob import glob
        files = sorted(glob(str(output_path / "*.vcf")))
        console.print(f"\n  Split into {len(files)} files (max {max_lines} lines each)")
    db.close()
    _log_session_event("export", f"{sum(counts.values())} contacts → {output_path}")


# ── Photos ──────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--extract", "extract_dir", type=click.Path(),
              help="Extract photos to directory.")
@click.pass_context
def photos(ctx, extract_dir):
    """Manage contact photos."""
    from schnabel.export import extract_photos

    db = get_db(ctx.obj["db_path"])

    if extract_dir:
        count = extract_photos(db, Path(extract_dir))
        console.print(f"[green]Extracted {count} photos → {extract_dir}/[/green]")
    else:
        stats = db.get_stats()
        console.print(f"Contacts with photos: {stats['with_photos']}")

    db.close()


# ── Status ──────────────────────────────────────────────────────────────────

@cli.command()
@click.pass_context
def status(ctx):
    """Show overall pipeline status."""
    db = get_db(ctx.obj["db_path"])
    stats = db.get_stats()

    console.print("\n[bold cyan]kontakt-schnabel status[/bold cyan]\n")
    _print_stats_table(stats)

    # Pipeline progress
    console.print("\n[bold]Pipeline:[/bold]")
    steps = [
        ("Import", stats["total"] > 0),
        ("Classify", stats["real"] + stats["stub"] + stats["spam"] > 0),
        ("Normalize", True),  # can't easily check
        ("Sanitize", True),  # can't easily check
        ("Match", stats["pending_pairs"] > 0 or stats["merges"] > 0),
        ("Dedup", stats["merges"] > 0),
        ("Export", False),  # can't easily check
    ]
    for name, done in steps:
        icon = "[green]✓[/green]" if done else "[dim]○[/dim]"
        console.print(f"  {icon} {name}")

    # Recent merges
    merges = db.get_recent_merges(5)
    if merges:
        console.print("\n[bold]Recent merges:[/bold]")
        for m in merges:
            survivor = db.get_contact(m["survivor_id"])
            absorbed = db.get_contact(m["absorbed_id"])
            s_name = survivor.fn if survivor else f"#{m['survivor_id']}"
            a_name = absorbed.fn if absorbed else f"#{m['absorbed_id']}"
            console.print(
                f"  #{m['id']}: {a_name} → {s_name} "
                f"({m['merge_type']}, {m['confidence']:.0%})"
            )

    db.close()


# ── Stats ──────────────────────────────────────────────────────────────────

@cli.command()
@click.pass_context
def stats(ctx):
    """Show detailed session statistics."""
    db = get_db(ctx.obj["db_path"])

    # Session start
    session_start = db.get_metadata("session_start")
    if session_start:
        console.print(f"\n[bold cyan]Session gestartet:[/bold cyan] {session_start}")
    else:
        console.print("\n[bold cyan]Session:[/bold cyan] (kein Reset durchgeführt)")

    # Contact stats
    contact_stats = db.get_stats()
    _print_stats_table(contact_stats)

    # Session activity
    session = db.get_session_stats()
    console.print("\n[bold]Session-Aktivität:[/bold]")
    console.print(f"  Import-Quellen: {session['imports']}")
    console.print(f"  Auto-Merges: {session['auto_merges']}")
    console.print(f"  Manuelle Merges: {session['manual_merges']}")
    console.print(f"  Gelöschte Kontakte: {session['deleted']}")
    console.print(f"  Offene Paare: {session['pending_pairs']}")

    # Log file info
    log_path = DEFAULT_OUTPUT_DIR / "schnabel.log"
    if log_path.exists():
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        console.print(f"\n[bold]Log:[/bold] {log_path} ({len(lines)} Einträge)")
        for line in lines[-5:]:
            console.print(f"  [dim]{line}[/dim]")
    else:
        console.print(f"\n[bold]Log:[/bold] (noch keine Einträge)")

    db.close()


# ── Reset ──────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--confirm", is_flag=True, help="Skip confirmation prompt.")
@click.pass_context
def reset(ctx):
    """Reset the database: delete all data and start fresh."""
    db_path = ctx.obj["db_path"]

    if not db_path.exists():
        console.print("[yellow]Keine Datenbank gefunden — nichts zu löschen.[/yellow]")
        return

    # Show current stats
    db = get_db(db_path)
    st = db.get_stats()
    console.print(f"\n[bold red]Achtung: Datenbank wird gelöscht![/bold red]")
    console.print(f"  Kontakte: {st['active']} aktiv, {st['total']} total")
    console.print(f"  Merges: {st['merges']}")
    console.print(f"  Paare: {st['pending_pairs']}")
    db.close()

    if not ctx.params.get("confirm"):
        try:
            answer = input("\nFortfahren? (ja/nein): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[yellow]Abgebrochen.[/yellow]")
            return
        if answer not in ("ja", "j", "yes", "y"):
            console.print("[yellow]Abgebrochen.[/yellow]")
            return

    # Delete and recreate
    db_path.unlink()
    db = get_db(db_path)
    from datetime import datetime
    db.set_metadata("session_start", datetime.now().strftime("%Y-%m-%d %H:%M"))
    db.close()

    console.print("[bold green]Datenbank zurückgesetzt. Neue Session gestartet.[/bold green]")
    _log_session_event("reset", "database reset")


# ── Rawparse ───────────────────────────────────────────────────────────────

@cli.command()
@click.argument("input_file", type=click.Path(exists=True), required=False)
@click.option("-o", "--output", "output_file", type=click.Path(), default=None,
              help="Output VCF file path (default: timestamped directory).")
@click.option("--db-import", "db_import", is_flag=True,
              help="Also import accepted contacts into the main database.")
@click.option("--auto-accept", is_flag=True,
              help="Skip TUI, auto-accept contacts where all fields have high confidence.")
@click.option("--pending", is_flag=True,
              help="Resume: only show previously skipped contacts.")
@click.pass_context
def rawparse(ctx, input_file, output_file, db_import, auto_accept, pending):
    """Parse contacts from unstructured text files.

    Run with INPUT_FILE to parse a new file. Run with --pending to resume
    review of previously skipped contacts.
    """
    from schnabel.export import contact_to_vcard
    from schnabel.rawparse import load_state, parse_raw_file, parsed_to_contact, save_state

    state_path = DEFAULT_OUTPUT_DIR / ".rawparse_state.json"

    if pending:
        # Resume from saved state
        contacts = load_state(state_path)
        if not contacts:
            console.print("[yellow]Kein gespeicherter Zustand gefunden.[/yellow]")
            return
        pending_count = sum(1 for c in contacts if c.status == "pending")
        if pending_count == 0:
            console.print("[green]Keine offenen Kontakte — alles erledigt.[/green]")
            state_path.unlink(missing_ok=True)
            return
        accepted_count = sum(1 for c in contacts if c.status == "accepted")
        console.print(
            f"[bold cyan]Gespeicherter Zustand geladen:[/bold cyan] "
            f"{len(contacts)} Kontakte ({pending_count} offen, "
            f"{accepted_count} bereits akzeptiert)"
        )
    else:
        # Parse new file
        if not input_file:
            console.print("[red]INPUT_FILE nötig (oder --pending zum Fortsetzen).[/red]")
            return

        contacts = parse_raw_file(input_file)

        if not contacts:
            console.print("[red]Keine Kontakte im Text gefunden.[/red]")
            return

        # Summary
        high_conf = sum(
            1 for c in contacts
            if all(f.confidence == "high" for f in c.fields)
        )
        needs_review = len(contacts) - high_conf
        console.print(
            f"[bold green]{len(contacts)} Kontakte geparst[/bold green] "
            f"({high_conf} hohe Konfidenz, {needs_review} zum Prüfen)"
        )

    if auto_accept:
        # Auto-accept contacts with fields, reject empty ones
        for c in contacts:
            if c.status != "pending":
                continue
            if c.fields and all(f.confidence == "high" for f in c.fields):
                c.status = "accepted"
            elif not c.fields:
                c.status = "rejected"
            else:
                c.status = "accepted"
        accepted = sum(1 for c in contacts if c.status == "accepted")
        low_conf = sum(
            1 for c in contacts
            if c.status == "accepted" and not all(f.confidence == "high" for f in c.fields)
        )
        console.print(
            f"[green]Auto-akzeptiert: {accepted}[/green]"
            + (f"  [yellow](davon {low_conf} mit tiefer Konfidenz)[/yellow]" if low_conf else "")
        )
    else:
        from schnabel.rawtui import run_raw_tui
        contacts = run_raw_tui(contacts)

    # Save state (so pending contacts survive quit)
    pending_count = sum(1 for c in contacts if c.status == "pending")
    if pending_count > 0:
        save_state(contacts, state_path)
        console.print(
            f"[yellow]{pending_count} Kontakte offen — "
            f"mit 'schnabel rawparse --pending' fortsetzen.[/yellow]"
        )
    else:
        state_path.unlink(missing_ok=True)

    # Export accepted contacts
    accepted_contacts = [c for c in contacts if c.status == "accepted"]
    if not accepted_contacts:
        console.print("[yellow]Keine Kontakte akzeptiert — nichts zu exportieren.[/yellow]")
        return

    if output_file:
        output_path = Path(output_file)
    else:
        output_path = make_output_dir("rawparse") / "raw_parsed.vcf"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    vcards = []
    for parsed in accepted_contacts:
        contact = parsed_to_contact(parsed)
        vcards.append(contact_to_vcard(contact))

    content = "\r\n".join(vcards)
    if content:
        content += "\r\n"
    output_path.write_text(content, encoding="utf-8")
    console.print(
        f"[bold green]{len(accepted_contacts)} Kontakte exportiert "
        f"→ {output_path}[/bold green]"
    )
    _log_session_event("rawparse", f"{len(accepted_contacts)} contacts exported → {output_path}")

    # Optional DB import
    if db_import:
        from schnabel.classify import classify_contact

        source = input_file or "rawparse-pending"
        db = get_db(ctx.obj["db_path"])
        import_id = db.add_import_source(source, "rawparse", "utf-8")

        for parsed in accepted_contacts:
            contact = parsed_to_contact(parsed)
            contact.source_file = source
            contact.source_import_id = import_id
            contact.category = classify_contact(contact)
            db.insert_contact(contact)

        db.update_import_count(import_id, len(accepted_contacts))
        db.commit()
        console.print(
            f"[green]{len(accepted_contacts)} Kontakte in Datenbank importiert.[/green]"
        )
        db.close()
        _auto_export(ctx, "rawparse")


# ── Split ──────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("input_file", type=click.Path(exists=True), required=False)
@click.option("-o", "--output-dir", type=click.Path(), default=None,
              help="Output directory for split files (default: timestamped directory).")
@click.option("--no-rest", is_flag=True,
              help="Don't write rest.vcf for unassigned contacts.")
@click.option("--pending", is_flag=True,
              help="Resume: continue from saved split state.")
def split(input_file, output_dir, no_rest, pending):
    """Interactively split a VCF file into multiple named target files."""
    from schnabel.splittui import (
        _start_dialog, load_split_state, run_split_tui,
        save_split_state, write_split_files,
    )

    output_path = Path(output_dir) if output_dir else make_output_dir("split")
    state_path = DEFAULT_OUTPUT_DIR / ".split_state.json"

    if pending:
        # Resume from saved state
        state = load_split_state(state_path)
        if not state:
            console.print("[yellow]Kein gespeicherter Zustand gefunden.[/yellow]")
            return

        contacts = state["contacts"]
        targets = state["targets"]
        initial_assignments = state["assignments"]
        initial_deleted = state["deleted"]

        pending_count = sum(
            1 for i in range(len(contacts))
            if i not in initial_assignments and i not in initial_deleted
        )
        console.print(
            f"[bold cyan]Gespeicherter Zustand geladen:[/bold cyan] "
            f"{len(contacts)} Kontakte ({pending_count} offen, "
            f"{len(initial_assignments)} zugewiesen, "
            f"{len(initial_deleted)} gelöscht)"
        )

        result = run_split_tui(contacts, targets, output_path,
                               write_rest=not no_rest,
                               initial_assignments=initial_assignments,
                               initial_deleted=initial_deleted)
    else:
        # Normal flow: parse VCF + start dialog
        if not input_file:
            console.print("[red]INPUT_FILE nötig (oder --pending zum Fortsetzen).[/red]")
            return

        from schnabel.reader import parse_vcf_file

        input_path = Path(input_file)
        contacts, encoding = parse_vcf_file(input_path)

        if not contacts:
            console.print(f"[red]Keine Kontakte in {input_path.name} gefunden.[/red]")
            return

        console.print(
            f"[bold green]{len(contacts)} Kontakte geladen[/bold green] "
            f"aus {input_path.name} (enc: {encoding})"
        )

        targets = _start_dialog()
        if not targets:
            console.print("[yellow]Abgebrochen.[/yellow]")
            return

        result = run_split_tui(contacts, targets, output_path,
                               write_rest=not no_rest)
        input_file = str(input_path)

    # Handle result
    if result.pending:
        save_split_state(result.contacts, result.targets,
                         result.assignments, result.deleted,
                         input_file or "", state_path)
        pending_count = sum(
            1 for i in range(len(result.contacts))
            if i not in result.assignments and i not in result.deleted
        )
        console.print(
            f"\n[yellow]{pending_count} Kontakte offen, "
            f"{len(result.assignments)} zugewiesen — "
            f"mit 'schnabel split --pending' fortsetzen.[/yellow]"
        )
    else:
        # All done — write files and clean up state
        written = write_split_files(result.contacts, result.targets,
                                    result.assignments, output_path,
                                    write_rest=not no_rest,
                                    deleted=result.deleted)
        state_path.unlink(missing_ok=True)

        if result.deleted:
            console.print(f"[red]{len(result.deleted)} Kontakte gelöscht.[/red]")
        console.print(f"\n[bold green]Aufgeteilt:[/bold green]")
        for filename, count in written.items():
            console.print(f"  {filename}: {count} Kontakte")

        if not written:
            console.print("  [dim](keine Kontakte zugewiesen)[/dim]")

        if written:
            console.print(f"\n[bold green]Dateien geschrieben → {output_path}/[/bold green]")


# ── Helpers ─────────────────────────────────────────────────────────────────

def _auto_export(ctx, command_name: str):
    """Auto-export contacts to a timestamped directory after a data-changing command."""
    if ctx.obj.get("no_export"):
        return
    from schnabel.export import export_contacts

    db = get_db(ctx.obj["db_path"])
    stats = db.get_stats()
    if stats["active"] == 0:
        db.close()
        return

    output_dir = make_output_dir(command_name)
    with console.status("[bold cyan]Auto-exporting..."):
        counts = export_contacts(db, output_dir)
    total = sum(counts.values())
    console.print(f"[dim]Auto-export: {total} contacts → {output_dir}/[/dim]")
    db.close()
    _log_session_event(command_name, f"auto-export {total} contacts → {output_dir.name}")


def _log_session_event(command: str, summary: str):
    """Append a log entry to output/schnabel.log."""
    from datetime import datetime
    log_path = DEFAULT_OUTPUT_DIR / "schnabel.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"{timestamp} {command}: {summary}\n"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(entry)


def _print_stats_table(stats: dict):
    table = Table(title="Contact Statistics", show_header=False, border_style="cyan")
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")

    table.add_row("Total imported", str(stats["total"]))
    table.add_row("Active", str(stats["active"]))
    table.add_row("─ Real", str(stats["real"]), style="green")
    table.add_row("─ Stubs", str(stats["stub"]), style="yellow")
    table.add_row("─ Spam", str(stats["spam"]), style="red")
    table.add_row("─ Unknown", str(stats["unknown"]), style="dim")
    table.add_row("With photos", str(stats["with_photos"]))
    table.add_row("Unique emails", str(stats["unique_emails"]))
    table.add_row("Unique phones", str(stats["unique_phones"]))
    table.add_row("Import sources", str(stats["import_sources"]))
    table.add_row("Pending pairs", str(stats["pending_pairs"]))
    table.add_row("Merges done", str(stats["merges"]))

    console.print(table)
