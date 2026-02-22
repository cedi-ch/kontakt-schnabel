# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project: kontakt-schnabel

A CLI tool for merging, editing, deduplicating, sanitizing, and separating vCard files.
Written in Python. Target use: cleaning up 10+ years of messy contacts from iPhones, Android,
Windows, Mac, Linux — for use on GrapheneOS with DAVx5, Thunderbird, and messengers.

## Repository

https://github.com/cedi-ch/kontakt-schnabel

## Architecture

```
src/schnabel/
├── cli.py          # Click CLI: all commands + auto-export + session logging
├── config.py       # Global config: weights, thresholds, encoding chain, make_output_dir()
├── db.py           # SQLite database with similarity graph + metadata table
├── model.py        # Contact/ContactField/Photo dataclasses
├── reader.py       # vCard parser (vobject + regex fallback) with encoding fallback
├── rawparse.py     # Raw text parser: extract contacts from unstructured text (subtract-and-classify pipeline)
├── rawtui.py       # Rich + readchar TUI for reviewing raw-parsed contacts
├── classify.py     # 3-tier classification: real / stub / spam
├── normalize.py    # 4-stage normalization: prune → transform → normalize → simplify
├── sanitize.py     # Within-contact field cleanup: phone/email/address/URL/text dedup
├── match.py        # Blocking + weighted scoring with anchor rules
├── merge.py        # Merge engine with aggressiveness parameter and undo
├── tui.py          # Rich + readchar TUI for manual dedup review (with back navigation)
├── splittui.py     # Rich + readchar TUI for splitting VCF files
└── export.py       # vCard 3.0 writer (custom, RFC 2426 compliant) + photo extraction
```

## Commands

```bash
source .venv/bin/activate
schnabel import data/input/*.vcf       # Parse and classify contacts
schnabel analyze [-v]                  # Show statistics
schnabel normalize                     # Normalize emails, phones, names
schnabel sanitize                      # Deduplicate fields within each contact
schnabel match                         # Find duplicate candidate pairs
schnabel dedup [--auto-only] [-a 0.5]  # Auto-merge + TUI review
schnabel dedup --pending               # Resume TUI with remaining pairs
schnabel export [DIR]                  # Export to real/stubs/spam VCF files (timestamped default)
schnabel photos --extract ./photos/    # Extract contact photos
schnabel status                        # Pipeline overview
schnabel stats                         # Detailed session statistics
schnabel reset [--confirm]             # Delete DB and start fresh
schnabel undo <merge-id>              # Undo a merge
schnabel rawparse FILE [-o OUT.vcf]    # Parse contacts from raw text files
schnabel rawparse FILE --auto-accept   # Skip TUI, accept all parsed contacts
schnabel rawparse --pending            # Resume: only skipped contacts
schnabel rawparse FILE --db-import     # Also import into main database
schnabel split FILE [-o DIR]           # Split VCF into named target files
schnabel split --pending               # Resume split session
```

**Global flags:** `--db PATH` (database path), `--no-export` (skip auto-export after data changes)

## Development

```bash
python -m pytest tests/ -v          # Run tests
```

## Key Design Decisions

- **similarity_pairs** table stores per-pair float confidence scores (0.0–1.0), enabling the aggressiveness parameter to control auto-merge threshold
- Aggressiveness 0.0–1.0 maps to threshold 0.95–0.50
- Anchor rules: shared email/phone → min 0.70; both → min 0.95; name-only → max 0.60
- vobject for parsing with regex fallback for malformed cards
- Custom writer for export (no vobject serialization)
- Data files in `data/` and `input/` are gitignored (personal contacts)
- Auto-export after data-changing commands (import, normalize, sanitize, dedup) — disable with `--no-export`
- Timestamped output directories: `output/YYYY-MM-DD_HHMM_command/`
- Session log at `output/schnabel.log`
- State files at stable paths: `output/.rawparse_state.json`, `output/.split_state.json`
