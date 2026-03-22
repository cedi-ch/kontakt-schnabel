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


def _print_help_all(ctx):
    """Print expanded help for every command, then exit."""
    import sys

    group = ctx.command
    console.print("[bold cyan]kontakt-schnabel[/bold cyan] — comprehensive command reference\n")

    # Global options from the group itself
    console.print("[bold]Global options:[/bold]")
    for param in group.params:
        if isinstance(param, click.Option):
            opts = ", ".join(param.opts)
            console.print(f"  [green]{opts}[/green]  {param.help or ''}")
    console.print()

    # Pipeline order header
    console.print("[bold]Pipeline order:[/bold]")
    console.print("  import → analyze → normalize → sanitize → match → dedup → categorize → export\n")

    # Iterate all subcommands
    commands = group.list_commands(ctx)
    for cmd_name in commands:
        cmd = group.get_command(ctx, cmd_name)
        if cmd is None:
            continue

        # Command name + short description (first line of docstring)
        help_text = cmd.get_short_help_str(limit=300)
        console.print(f"[bold cyan]schnabel {cmd_name}[/bold cyan]  —  {help_text}")

        # Full docstring (if longer than short help)
        if cmd.help:
            full = cmd.help.strip()
            lines = full.split("\n")
            if len(lines) > 1:
                for line in lines[1:]:
                    stripped = line.strip()
                    if stripped:
                        console.print(f"  [dim]{stripped}[/dim]")

        # Options and arguments
        for param in cmd.params:
            if isinstance(param, click.Argument):
                name = param.human_readable_name
                req = "" if param.required else " (optional)"
                console.print(f"  [green]{name}[/green]{req}")
            elif isinstance(param, click.Option):
                opts = ", ".join(param.opts)
                default = ""
                show_default = (param.default is not None
                                and param.default is not False
                                and param.default != ""
                                and "Sentinel" not in str(type(param.default)))
                if show_default:
                    default = f" [dim](default: {param.default})[/dim]"
                console.print(f"  [green]{opts}[/green]  {param.help or ''}{default}")

        console.print()

    sys.exit(0)


@click.group()
@click.option("--db", "db_path", type=click.Path(), default=str(DEFAULT_DB_PATH),
              help="Path to SQLite database.")
@click.option("--no-export", is_flag=True, help="Skip automatic VCF export after data changes.")
@click.option("--help-all", "help_all", is_flag=True, is_eager=True, expose_value=False,
              callback=lambda ctx, param, value: _print_help_all(ctx) if value else None,
              help="Show expanded help for all commands.")
@click.pass_context
def cli(ctx, db_path, no_export):
    """kontakt-schnabel: merge, deduplicate, and sanitize vCard files.

    \b
    Pipeline order:
      1. import      Import VCF files into database
      2. analyze     Review what you have (optional)
      3. normalize   Normalize emails, phones, names for matching
      4. sanitize    Deduplicate fields within each contact
      5. match       Find duplicate candidate pairs
      6. dedup       Auto-merge + interactive review
      7. categorize  Assign categories (optional)
      8. export      Export clean vCard 3.0 files
    """
    ctx.ensure_object(dict)
    ctx.obj["db_path"] = Path(db_path)
    ctx.obj["no_export"] = no_export


# ── Import ──────────────────────────────────────────────────────────────────

@cli.command("import")
@click.argument("files", nargs=-1, type=click.Path(exists=True))
@click.option("--dir", "input_dir", type=click.Path(exists=True),
              help="Import all .vcf files from a directory.")
@click.option("--list", "list_sources", is_flag=True,
              help="List all previously imported source files.")
@click.pass_context
def import_cmd(ctx, files, input_dir, list_sources):
    """Import vCard files into the database."""
    db = get_db(ctx.obj["db_path"])

    if list_sources:
        sources = db.get_import_sources()
        if not sources:
            console.print("[dim]No files imported yet.[/dim]")
            db.close()
            return
        table = Table(title="[bold cyan]Imported Sources[/bold cyan]",
                      border_style="cyan")
        table.add_column("#", style="dim", justify="right")
        table.add_column("File", style="bold")
        table.add_column("Contacts", justify="right")
        table.add_column("Encoding", style="dim")
        table.add_column("Imported at", style="dim")
        total = 0
        for s in sources:
            name = Path(s["file_path"]).name
            count = s["contact_count"] or 0
            total += count
            table.add_row(
                str(s["id"]), name, str(count),
                s["encoding_used"], s["imported_at"],
            )
        table.add_row("", "[bold]TOTAL[/bold]", f"[bold]{total}[/bold]", "", "")
        console.print(table)
        db.close()
        return

    from schnabel.classify import classify_contact
    from schnabel.reader import file_md5, parse_vcf_file

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
    imported_names = []

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
            imported_names.append(fp.name)
            console.print(
                f"  [green]✓[/green] {fp.name}: {len(contacts)} contacts "
                f"(enc: {encoding})"
            )

    stats = db.get_stats()
    skipped = len(file_paths) - total_files
    _print_module_report("Import", [
        ("Files parsed", str(total_files)),
        ("Skipped", str(skipped), "dim"),
        ("Contacts imported", str(total_contacts), "bold"),
        ("─ Real", str(stats["real"]), "green"),
        ("─ Stubs", str(stats["stub"]), "yellow"),
        ("─ Spam", str(stats["spam"]), "red"),
    ])

    db.log_pipeline_run("import", {
        "files": total_files, "skipped": skipped,
        "contacts": total_contacts,
        "real": stats["real"], "stubs": stats["stub"], "spam": stats["spam"],
        "filenames": imported_names,
    })
    db.close()

    _log_session_event("import", f"{total_contacts} contacts from {total_files} files")
    _auto_export(ctx, "import")


# ── Analyze ─────────────────────────────────────────────────────────────────

@cli.command()
@click.option("-v", "--verbose", is_flag=True, help="Show detailed breakdown.")
@click.pass_context
def analyze(ctx, verbose):
    """Show contact content analysis: field coverage for Real, Stubs, Spam."""
    db = get_db(ctx.obj["db_path"])
    stats = db.get_stats()

    console.print(f"\n[bold cyan]Analyze[/bold cyan]  "
                  f"[green]{stats['real']} real[/green]  "
                  f"[yellow]{stats['stub']} stubs[/yellow]  "
                  f"[red]{stats['spam']} spam[/red]\n")

    _print_analyze(db)

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

    _print_module_report("Normalize", [
        ("Contacts normalized", str(stats['active']), "green"),
    ])

    db.log_pipeline_run("normalize", {"contacts": stats['active']})
    db.close()

    _log_session_event("normalize", f"{stats['active']} contacts normalized")
    _auto_export(ctx, "normalize")


# ── Sanitize ───────────────────────────────────────────────────────────────

@cli.command()
@click.option("--addresses", is_flag=True,
              help="Also resolve contacts with multiple addresses interactively.")
@click.option("--addresses-only", is_flag=True,
              help="Only resolve multi-address contacts (skip full sanitize).")
@click.pass_context
def sanitize(ctx, addresses, addresses_only):
    """Sanitize contacts: deduplicate phones, emails, addresses within each contact."""
    db = get_db(ctx.obj["db_path"])
    stats = db.get_stats()
    if stats["active"] == 0:
        console.print("[red]No contacts in database. Run 'schnabel import' first.[/red]")
        db.close()
        return

    if addresses_only:
        from schnabel.sanitize import find_contacts_with_multi_addresses
        from schnabel.tui import address_chooser
        multi_addr_ids = find_contacts_with_multi_addresses(db)
        if multi_addr_ids:
            console.print(f"[bold yellow]{len(multi_addr_ids)} Kontakte mit mehreren Adressen.[/bold yellow]")
            resolved_addrs = address_chooser(db, multi_addr_ids)
            if resolved_addrs:
                console.print(f"[green]{resolved_addrs} Kontakte aufgeräumt.[/green]")
        else:
            console.print("[green]Keine Kontakte mit mehreren Adressen.[/green]")
        db.close()
        _auto_export(ctx, "sanitize")
        return

    from schnabel.sanitize import sanitize_contacts

    with console.status("[bold cyan]Sanitizing contacts...") as status:
        def progress(current, total):
            status.update(f"[bold cyan]Sanitizing... {current}/{total}")

        report = sanitize_contacts(db, progress_callback=progress)

    # Handle ambiguous BDAYs with mini-TUI
    resolved_bdays = 0
    if report.ambiguous_bdays:
        import readchar
        from rich.text import Text

        console.print(f"\n[bold yellow]{len(report.ambiguous_bdays)} mehrdeutige "
                      f"Geburtstage gefunden:[/bold yellow]\n")

        for amb in report.ambiguous_bdays:
            line = Text()
            line.append(f"  {amb.contact_fn}", style="bold")
            line.append(f"  (Roh: {amb.raw_value})", style="dim")
            console.print(line)

            opt = Text()
            opt.append("  [1] ", style="bold green")
            opt.append(amb.label_a)
            opt.append("  [2] ", style="bold blue")
            opt.append(amb.label_b)
            opt.append("  [s] ", style="bold yellow")
            opt.append("überspringen")
            console.print(opt)

            console.print("  Wahl: ", end="")
            key = readchar.readchar()
            console.print(key)

            if key == "1":
                db.update_contact_field(amb.field_id, amb.option_a)
                report.reformatted["bday"] += 1
                resolved_bdays += 1
                console.print(f"  [green]→ {amb.option_a}[/green]")
            elif key == "2":
                db.update_contact_field(amb.field_id, amb.option_b)
                report.reformatted["bday"] += 1
                resolved_bdays += 1
                console.print(f"  [green]→ {amb.option_b}[/green]")
            else:
                console.print("  [dim]übersprungen[/dim]")
            console.print()

        if resolved_bdays:
            db.commit()

    # Show report as Rich table
    from rich.table import Table
    label_map = {
        "empty": "Empty", "tel": "Phone", "email": "Email",
        "adr": "Address", "url": "URL", "text": "Names",
        "bday": "Birthday", "type": "Type",
    }
    table = Table(title="[bold cyan]Sanitize[/bold cyan]", show_header=True, border_style="cyan")
    table.add_column("Type", style="bold")
    table.add_column("Removed", justify="right", style="red")
    table.add_column("Reformatted", justify="right", style="yellow")

    for key in ("empty", "tel", "email", "adr", "url", "text", "bday"):
        removed = report.removed.get(key, 0)
        reformatted = report.reformatted.get(key, 0)
        if removed > 0 or reformatted > 0:
            table.add_row(label_map.get(key, key.upper()), str(removed), str(reformatted))

    if report.total_removed > 0 or report.total_reformatted > 0:
        table.add_row("TOTAL", str(report.total_removed), str(report.total_reformatted),
                       style="bold")
        console.print(table)
    else:
        console.print("[green]Alle Kontakte bereits sauber — keine Änderungen.[/green]")

    if addresses:
        from schnabel.sanitize import find_contacts_with_multi_addresses
        from schnabel.tui import address_chooser
        multi_addr_ids = find_contacts_with_multi_addresses(db)
        if multi_addr_ids:
            console.print(f"\n[bold yellow]{len(multi_addr_ids)} Kontakte mit mehreren Adressen.[/bold yellow]")
            resolved_addrs = address_chooser(db, multi_addr_ids)
            if resolved_addrs:
                console.print(f"[green]{resolved_addrs} Kontakte aufgeräumt.[/green]")
        else:
            console.print("[green]Keine Kontakte mit mehreren Adressen.[/green]")

    db.log_pipeline_run("sanitize", {
        "removed_total": report.total_removed,
        "reformatted_total": report.total_reformatted,
    })
    db.close()

    summary = f"{report.total_removed} removed, {report.total_reformatted} reformatted"
    if resolved_bdays:
        summary += f", {resolved_bdays} ambiguous BDAYs resolved"
    _log_session_event("sanitize", summary)
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

    pairs = db.get_pending_pairs()
    high = sum(1 for p in pairs if p["confidence"] >= 0.90)
    med = sum(1 for p in pairs if 0.70 <= p["confidence"] < 0.90)
    low = sum(1 for p in pairs if p["confidence"] < 0.70)

    _print_module_report("Match", [
        ("Candidate pairs", str(stored), "bold"),
        ("─ High (≥90%)", str(high), "green"),
        ("─ Medium (70–90%)", str(med), "yellow"),
        ("─ Low (<70%)", str(low), "red"),
    ])

    db.log_pipeline_run("match", {
        "total": stored, "high": high, "medium": med, "low": low,
    })
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

        # Check for multi-address contacts after auto-merge
        from schnabel.sanitize import find_contacts_with_multi_addresses
        from schnabel.tui import address_chooser
        multi_addr_ids = find_contacts_with_multi_addresses(db)
        if multi_addr_ids:
            console.print(f"\n[bold yellow]{len(multi_addr_ids)} Kontakte mit mehreren Adressen.[/bold yellow]")
            resolved = address_chooser(db, multi_addr_ids)
            if resolved:
                console.print(f"[green]{resolved} Kontakte aufgeräumt.[/green]")

    if auto_only:
        stats = db.get_stats()
        pending_count = stats['pending_pairs']
        _print_module_report("Dedup", [
            ("Pairs processed", str(merged + pending_count), "bold"),
            ("Merged", str(merged), "green"),
            ("Pending", str(pending_count), "yellow"),
            ("Active contacts", str(stats['active'])),
        ])
        db.log_pipeline_run("dedup", {
            "processed": merged + pending_count, "merged": merged,
            "skipped": 0, "pending": pending_count,
            "before": stats['total'], "after": stats['active'],
        })
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

    stats = db.get_stats()
    db.log_pipeline_run("dedup", {
        "processed": merged + stats.get('merges', 0), "merged": stats.get('merges', 0),
        "skipped": 0, "pending": stats['pending_pairs'],
        "before": stats['total'], "after": stats['active'],
    })
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
@click.option("--by-category", is_flag=True,
              help="Split real contacts by CATEGORIES into separate files.")
@click.option("--no-preview", is_flag=True,
              help="Skip export preview and export immediately.")
@click.pass_context
def export(ctx, output_dir, normalize_photos, max_lines, by_category, no_preview):
    """Export contacts to clean vCard 3.0 files.

    OUTPUT_DIR is optional; if omitted, a timestamped directory is created.
    """
    from schnabel.export import export_by_category, export_contacts, get_export_preview

    db = get_db(ctx.obj["db_path"])

    # Preview
    if not no_preview:
        preview = get_export_preview(db)
        console.print("\n[bold cyan]Export-Vorschau:[/bold cyan]")
        console.print(f"  Echte Kontakte:     {preview['real']}"
                      f" (davon {preview['real_with_photos']} mit Foto)")
        if preview["categories"]:
            for cat_name, count in sorted(preview["categories"].items()):
                console.print(f"  - Kategorie {cat_name}: {count}")
        if preview["uncategorized"] > 0:
            console.print(f"  - Ohne Kategorie: {preview['uncategorized']}")
        console.print(f"  Stubs:              {preview['stubs']}")
        console.print(f"  Spam:               {preview['spam']}")

        if by_category:
            console.print(f"\n  [dim]Modus: nach Kategorien aufgeteilt[/dim]")
        console.print()

    output_path = Path(output_dir) if output_dir else make_output_dir("export")

    with console.status("[bold cyan]Exporting..."):
        if by_category:
            counts = export_by_category(db, output_path, normalize_photos=normalize_photos,
                                        max_lines=max_lines)
        else:
            counts = export_contacts(db, output_path, normalize_photos=normalize_photos,
                                     max_lines=max_lines)

    rows = []
    for category, count in counts.items():
        style = {"real": "green", "stub": "yellow", "spam": "red"}.get(category, "dim")
        rows.append((category.capitalize(), str(count), style))
    rows.append(("Path", str(output_path), "dim"))

    if max_lines > 0:
        from glob import glob
        files = sorted(glob(str(output_path / "*.vcf")))
        rows.append(("Files", str(len(files))))

    _print_module_report("Export", rows)

    db.log_pipeline_run("export", {
        "files": dict(counts), "path": str(output_path),
    })
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
    """Show session overview: pipeline runs + content analysis."""
    db = get_db(ctx.obj["db_path"])
    stats = db.get_stats()

    # Session section
    session_start = db.get_metadata("session_start")
    session_rows = [
        ("Started", session_start or "(no reset)", "dim"),
        ("Total imported", str(stats["total"])),
        ("Active contacts", str(stats["active"]), "bold"),
    ]
    _print_module_report("Session", session_rows)

    # Pipeline Runs section
    runs = db.get_pipeline_runs()
    if runs:
        run_rows = []
        run_labels = {
            "import": lambda d: f"{d.get('contacts', '?')} contacts from {d.get('files', '?')} files",
            "normalize": lambda d: f"{d.get('contacts', '?')} contacts",
            "sanitize": lambda d: f"{d.get('removed_total', 0)} removed, {d.get('reformatted_total', 0)} reformatted",
            "match": lambda d: f"{d.get('total', '?')} pairs ({d.get('high', 0)}h/{d.get('medium', 0)}m/{d.get('low', 0)}l)",
            "dedup": lambda d: f"{d.get('merged', 0)} merged, {d.get('pending', 0)} pending",
            "categorize": lambda d: f"{d.get('assigned', 0)} assigned",
            "export": lambda d: f"{sum(d.get('files', {}).values()) if isinstance(d.get('files'), dict) else '?'} contacts",
        }
        for cmd, data in runs.items():
            label_fn = run_labels.get(cmd)
            summary = label_fn(data) if label_fn else str(data)
            ts = data.get("timestamp", "")
            run_rows.append((cmd, f"{summary}  [dim]{ts}[/dim]"))
        _print_module_report("Pipeline Runs", run_rows)
    else:
        console.print("\n[dim]No pipeline runs recorded yet.[/dim]")

    # Import sources
    sources = db.get_import_sources()
    if sources:
        src_table = Table(title="[bold cyan]Import Sources[/bold cyan]",
                          border_style="cyan", show_header=True)
        src_table.add_column("File", style="bold")
        src_table.add_column("Contacts", justify="right")
        src_table.add_column("Enc", style="dim")
        src_table.add_column("Imported", style="dim")
        total_imported = 0
        for s in sources:
            name = Path(s["file_path"]).name
            count = s["contact_count"] or 0
            total_imported += count
            src_table.add_row(name, str(count), s["encoding_used"], s["imported_at"])
        src_table.add_row("[bold]TOTAL[/bold]", f"[bold]{total_imported}[/bold]", "", "")
        console.print(src_table)

    # Full analyze output
    console.print()
    _print_analyze(db)

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
def reset(ctx, confirm):
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

    if not confirm:
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


# ── Reclassify ─────────────────────────────────────────────────────────────

@cli.command()
@click.option("--dry-run", is_flag=True, help="Show what would change without applying.")
@click.pass_context
def reclassify(ctx, dry_run):
    """Reclassify all active contacts with current rules.

    Use after classification rule changes to update existing contacts.
    Real requires phone, photo, or address. Name-only/email-only → stub.
    """
    from schnabel.classify import classify_contact

    db = get_db(ctx.obj["db_path"])
    contacts = db.get_all_active_contacts()

    transitions: dict[str, int] = {}
    changes: list[tuple[int, str, str]] = []

    for c in contacts:
        new_cat = classify_contact(c)
        if new_cat != c.category:
            key = f"{c.category} → {new_cat}"
            transitions[key] = transitions.get(key, 0) + 1
            changes.append((c.id, c.category, new_cat))

    if not changes:
        console.print("[green]Keine Änderungen — alle Kontakte korrekt klassifiziert.[/green]")
        db.close()
        return

    rows = [(k, str(v)) for k, v in sorted(transitions.items())]
    _print_module_report("Reclassify" + (" (dry run)" if dry_run else ""), rows)

    if dry_run:
        console.print(f"\n[dim]{len(changes)} Kontakte würden geändert.[/dim]")
        db.close()
        return

    for cid, _old, new_cat in changes:
        db.update_contact_category(cid, new_cat)
    db.commit()

    console.print(f"\n[bold green]{len(changes)} Kontakte reklassifiziert.[/bold green]")
    db.close()

    _log_session_event("reclassify", f"{len(changes)} contacts reclassified")
    _auto_export(ctx, "reclassify")


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
@click.pass_context
def split(ctx, input_file, output_dir, no_rest, pending):
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


# ── Compare ───────────────────────────────────────────────────────────

@cli.command()
@click.argument("file_a", type=click.Path(exists=True))
@click.argument("file_b", type=click.Path(exists=True))
@click.option("--min-confidence", type=float, default=0.40,
              help="Minimum confidence for matching (default 0.40).")
def compare(file_a, file_b, min_confidence):
    """Compare two VCF files side-by-side and show differences.

    Matches contacts across FILE_A and FILE_B, then launches a read-only TUI
    showing only the differences — with color-highlighted field diffs.
    """
    from schnabel.compare import find_cross_file_pairs, run_compare_tui
    from schnabel.reader import parse_vcf_file

    path_a = Path(file_a)
    path_b = Path(file_b)

    with console.status("[bold cyan]Parsing..."):
        contacts_a, enc_a = parse_vcf_file(path_a)
        contacts_b, enc_b = parse_vcf_file(path_b)

    console.print(f"  {path_a.name}: {len(contacts_a)} Kontakte (enc: {enc_a})")
    console.print(f"  {path_b.name}: {len(contacts_b)} Kontakte (enc: {enc_b})")

    if not contacts_a and not contacts_b:
        console.print("[yellow]Beide Dateien leer.[/yellow]")
        return

    with console.status("[bold cyan]Matching..."):
        result = find_cross_file_pairs(contacts_a, contacts_b,
                                       min_confidence=min_confidence)

    n_identical = len(result["matched_identical"])
    n_different = len(result["matched_different"])
    n_only_a = len(result["only_a"])
    n_only_b = len(result["only_b"])
    n_matched = n_identical + n_different
    n_total_diff = n_different + n_only_a + n_only_b

    console.print(f"\n[bold]Ergebnis:[/bold]")
    console.print(f"  Gematcht: {n_matched} ({n_identical} identisch, {n_different} verschieden)")
    if n_only_a:
        console.print(f"  Nur in {path_a.name}: {n_only_a}")
    if n_only_b:
        console.print(f"  Nur in {path_b.name}: {n_only_b}")

    if n_total_diff == 0:
        console.print("\n[bold green]Dateien identisch.[/bold green]")
        return

    console.print(f"\n[bold cyan]{n_total_diff} Unterschiede — TUI starten...[/bold cyan]")
    name_a = path_a.stem
    name_b = path_b.stem
    run_compare_tui(result["matched_different"], result["only_a"], result["only_b"],
                    name_a, name_b)

    _log_session_event("compare",
                       f"{path_a.name} vs {path_b.name}: "
                       f"{n_matched} matched ({n_identical} identical), "
                       f"{n_only_a} only-A, {n_only_b} only-B")


# ── PDF ────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("input_file", type=click.Path(exists=True))
@click.option("-o", "--output", "output_file", type=click.Path(), default=None,
              help="Output PDF path (default: same name as input, .pdf extension).")
@click.option("--title", default=None, help="Custom title for the PDF document.")
def pdf(input_file, output_file, title):
    """Generate a PDF phone list from a VCF file.

    One A4 landscape page per letter, sorted alphabetically by surname,
    then by firstname within each letter. Columns: Name, Phone, Email.
    """
    from schnabel.pdf import generate_pdf

    input_path = Path(input_file)

    if output_file:
        output_path = Path(output_file)
    else:
        output_path = input_path.with_suffix(".pdf")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with console.status("[bold cyan]Generating PDF..."):
        count = generate_pdf(input_path, output_path, title=title)

    if count == 0:
        console.print("[red]Keine Kontakte gefunden — kein PDF erstellt.[/red]")
        return

    console.print(f"[bold green]{count} Kontakte → {output_path}[/bold green]")
    _log_session_event("pdf", f"{count} contacts → {output_path}")


# ── Categorize ────────────────────────────────────────────────────────

@cli.command()
@click.option("--uncategorized", is_flag=True,
              help="Only show contacts without any categories.")
@click.option("--purge", type=str, default=None,
              help="Delete a specific category from ALL contacts (bulk cleanup).")
@click.pass_context
def categorize(ctx, uncategorized, purge):
    """Interactively assign categories to contacts."""
    db = get_db(ctx.obj["db_path"])

    if purge:
        _purge_category(db, purge)
        db.close()
        _log_session_event("categorize", f"purged category '{purge}'")
        _auto_export(ctx, "categorize")
        return

    from schnabel.cattui import run_categorize_tui

    stats = db.get_stats()
    if stats["real"] == 0:
        console.print("[red]Keine 'real'-Kontakte. Zuerst 'schnabel import' ausführen.[/red]")
        db.close()
        return

    run_categorize_tui(db, uncategorized_only=uncategorized)

    # Log pipeline run with category breakdown
    breakdown = db.get_category_breakdown()
    stats = db.get_stats()
    db.log_pipeline_run("categorize", {
        "reviewed": stats["real"],
        "assigned": stats["contacts_with_categories"],
        "categories": breakdown,
    })
    db.close()

    _log_session_event("categorize", "category assignment session")
    _auto_export(ctx, "categorize")


# ── Split-Export ──────────────────────────────────────────────────────

@cli.command("split-export")
@click.option("-o", "--output-dir", type=click.Path(), default=None,
              help="Output directory (default: timestamped directory).")
@click.option("--no-rest", is_flag=True,
              help="Don't write rest.vcf for unassigned contacts.")
@click.pass_context
def split_export(ctx, output_dir, no_rest):
    """Export contacts from a saved split session (no TUI).

    Reads the split state saved by 'schnabel split' and writes VCF files.
    The state file is preserved so you can re-export with different options.
    """
    from schnabel.splittui import load_split_state, write_split_files

    state_path = DEFAULT_OUTPUT_DIR / ".split_state.json"
    state = load_split_state(state_path)

    if not state:
        console.print("[red]Kein gespeicherter Split-Zustand gefunden.[/red]")
        console.print("[dim]Zuerst 'schnabel split' ausführen.[/dim]")
        return

    contacts = state["contacts"]
    targets = state["targets"]
    assignments = state["assignments"]
    deleted = state["deleted"]

    # Summary
    pending_count = sum(
        1 for i in range(len(contacts))
        if i not in assignments and i not in deleted
    )
    console.print(f"\n[bold cyan]Split-Export[/bold cyan]")
    console.print(f"  Kontakte total: {len(contacts)}")
    console.print(f"  Zugewiesen: {len(assignments)}")
    console.print(f"  Gelöscht: {len(deleted)}")
    if pending_count:
        console.print(f"  [yellow]Offen (nicht zugewiesen): {pending_count}[/yellow]")
    console.print()

    for i, t in enumerate(targets):
        count = sum(1 for v in assignments.values() if v == i)
        if count:
            console.print(f"  [{t.key}] {t.name}: {count} Kontakte")

    output_path = Path(output_dir) if output_dir else make_output_dir("split-export")

    written = write_split_files(contacts, targets, assignments, output_path,
                                write_rest=not no_rest, deleted=deleted)

    if deleted:
        console.print(f"\n[red]{len(deleted)} Kontakte gelöscht.[/red]")
    console.print(f"\n[bold green]Aufgeteilt:[/bold green]")
    for filename, count in written.items():
        console.print(f"  {filename}: {count} Kontakte")

    if not written:
        console.print("  [dim](keine Kontakte zugewiesen)[/dim]")

    if written:
        console.print(f"\n[bold green]Dateien geschrieben → {output_path}/[/bold green]")

    total = sum(written.values())
    _log_session_event("split-export", f"{total} contacts → {output_path}")


# ── Helpers ─────────────────────────────────────────────────────────────────

def _purge_category(db, category_value: str):
    """Delete a specific category from all contacts, with confirmation."""
    # Check if this category actually exists
    all_cats = db.get_all_category_values()
    # Case-insensitive match
    match = None
    for cat in all_cats:
        if cat == category_value:
            match = cat
            break
        if cat.lower() == category_value.lower():
            match = cat
            break

    if not match:
        console.print(f"[red]Kategorie '{category_value}' nicht gefunden.[/red]")
        if all_cats:
            console.print(f"\n[dim]Vorhandene Kategorien:[/dim]")
            for cat in all_cats:
                console.print(f"  {cat}")
        return

    # Count affected contacts
    breakdown = db.get_category_breakdown()
    count = breakdown.get(match, 0)
    # Also count stubs/spam that might have it
    row = db.conn.execute(
        """SELECT COUNT(DISTINCT cf.contact_id) as n FROM contact_fields cf
           JOIN contacts c ON cf.contact_id = c.id
           WHERE cf.field_type = 'categories' AND cf.field_value = ? AND c.is_active = 1""",
        (match,),
    ).fetchone()
    total_affected = row["n"]

    console.print(f"\n[bold yellow]Kategorie löschen:[/bold yellow] {match}")
    console.print(f"  Betroffen: [bold]{total_affected}[/bold] Kontakte")

    try:
        answer = input("\nFortfahren? (ja/nein): ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        console.print("\n[yellow]Abgebrochen.[/yellow]")
        return

    if answer not in ("ja", "j", "yes", "y"):
        console.print("[yellow]Abgebrochen.[/yellow]")
        return

    deleted = db.delete_category_from_all(match)
    console.print(f"[bold green]'{match}' entfernt aus {deleted} Kontakten.[/bold green]")


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


def _print_module_report(title: str, rows: list, border_style: str = "cyan"):
    """Print a Rich Table for a module report.

    rows: list of (label, value) or (label, value, style) tuples.
    """
    table = Table(title=f"[bold {border_style}]{title}[/bold {border_style}]",
                  show_header=False, border_style=border_style)
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")

    for row in rows:
        if len(row) == 3:
            table.add_row(str(row[0]), str(row[1]), style=row[2])
        else:
            table.add_row(str(row[0]), str(row[1]))

    console.print(table)


def _print_analyze(db):
    """Print the analyze output: Real/Stubs/Spam content tables."""
    stats = db.get_stats()

    # Real Contacts
    real_table = Table(title="[bold green]Real Contacts[/bold green]",
                       show_header=False, border_style="green")
    real_table.add_column("Metric L", style="bold")
    real_table.add_column("Value L", justify="right")
    real_table.add_column("Metric R", style="bold")
    real_table.add_column("Value R", justify="right")

    real_table.add_row(
        "With phone", str(stats.get("real_with_tel", 0)),
        "Unique emails", str(stats["unique_emails"]),
    )
    real_table.add_row(
        "With email", str(stats.get("real_with_email", 0)),
        "Unique phones", str(stats["unique_phones"]),
    )
    real_table.add_row(
        "With photo", str(stats.get("real_with_photo", 0)),
        "With org", str(stats.get("with_org", 0)),
    )
    real_table.add_row(
        "With birthday", str(stats.get("real_with_bday", 0)),
        "With note", str(stats.get("with_note", 0)),
    )
    real_table.add_row(
        "With address", str(stats.get("real_with_adr", 0)),
        "With URL", str(stats.get("with_url", 0)),
    )
    real_table.add_row(
        "With categories", str(stats.get("contacts_with_categories", 0)),
        "", "",
    )

    console.print(real_table)

    # Top 5 categories
    breakdown = db.get_category_breakdown()
    if breakdown:
        top5 = list(breakdown.items())[:5]
        parts = [f"{name} ({count})" for name, count in top5]
        console.print(f"  [dim]Top categories: {', '.join(parts)}[/dim]")

    # Stubs
    stub_rows = [
        ("With email", str(stats.get("stub_with_email", 0))),
        ("With phone", str(stats.get("stub_with_tel", 0))),
    ]
    _print_module_report("Stubs", stub_rows, border_style="yellow")

    # Spam
    spam_rows = [
        ("With email", str(stats.get("spam_with_email", 0))),
    ]
    _print_module_report("Spam", spam_rows, border_style="red")


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

    if stats.get("unique_categories", 0) > 0:
        table.add_row("Categories", str(stats["unique_categories"]))
        table.add_row("With categories", str(stats["contacts_with_categories"]))

    console.print(table)
