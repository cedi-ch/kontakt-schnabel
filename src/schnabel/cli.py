"""Click CLI: schnabel import, analyze, normalize, match, dedup, export, status."""

from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from schnabel.config import DEFAULT_DB_PATH, DEFAULT_DATA_DIR, DEFAULT_OUTPUT_DIR

console = Console()


def get_db(db_path: Path) -> "Database":
    from schnabel.db import Database
    return Database(db_path)


@click.group()
@click.option("--db", "db_path", type=click.Path(), default=str(DEFAULT_DB_PATH),
              help="Path to SQLite database.")
@click.pass_context
def cli(ctx, db_path):
    """kontakt-schnabel: merge, deduplicate, and sanitize vCard files."""
    ctx.ensure_object(dict)
    ctx.obj["db_path"] = Path(db_path)


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
@click.pass_context
def dedup(ctx, auto_only, aggressiveness):
    """Deduplicate contacts: auto-merge then review remaining pairs."""
    from schnabel.merge import aggressiveness_to_threshold, auto_resolve

    db = get_db(ctx.obj["db_path"])
    threshold = aggressiveness_to_threshold(aggressiveness)
    console.print(f"Aggressiveness: {aggressiveness:.1f} → threshold: {threshold:.3f}")

    # Auto-resolve phase
    with console.status("[bold cyan]Auto-merging obvious duplicates...") as status:
        def progress(current, total, merged):
            status.update(
                f"[bold cyan]Auto-merging... {current}/{total} pairs, "
                f"{merged} merged"
            )

        merged = auto_resolve(db, aggressiveness=aggressiveness, progress_callback=progress)

    console.print(f"[bold green]Auto-merged {merged} pairs.[/bold green]")

    if auto_only:
        stats = db.get_stats()
        console.print(f"Remaining pending pairs: {stats['pending_pairs']}")
        _print_stats_table(stats)
        db.close()
        return

    # TUI phase
    from schnabel.tui import run_tui
    run_tui(db, auto_merged=merged)
    db.close()


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
@click.argument("output_dir", type=click.Path(), default=str(DEFAULT_OUTPUT_DIR))
@click.option("--normalize-photos/--preserve-photos", default=True,
              help="Normalize photos (resize, JPEG convert). Default: normalize.")
@click.pass_context
def export(ctx, output_dir, normalize_photos):
    """Export contacts to clean vCard 3.0 files."""
    from schnabel.export import export_contacts

    db = get_db(ctx.obj["db_path"])
    output_path = Path(output_dir)

    with console.status("[bold cyan]Exporting..."):
        counts = export_contacts(db, output_path, normalize_photos=normalize_photos)

    console.print(f"\n[bold green]Export complete → {output_path}/[/bold green]")
    for category, count in counts.items():
        console.print(f"  {category}: {count} contacts")
    db.close()


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


# ── Helpers ─────────────────────────────────────────────────────────────────

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
