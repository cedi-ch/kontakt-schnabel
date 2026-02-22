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
├── cli.py          # Click CLI: schnabel import, analyze, normalize, match, dedup, export, status, photos, undo, rawparse
├── config.py       # Global config: weights, thresholds, encoding chain, spam lists
├── db.py           # SQLite database with similarity graph schema
├── model.py        # Contact/ContactField/Photo dataclasses
├── reader.py       # vCard parser (vobject + regex fallback) with encoding fallback
├── rawparse.py     # Raw text parser: extract contacts from unstructured text (subtract-and-classify pipeline)
├── rawtui.py       # Rich + readchar TUI for reviewing raw-parsed contacts
├── classify.py     # 3-tier classification: real / stub / spam
├── normalize.py    # 4-stage normalization: prune → transform → normalize → simplify
├── match.py        # Blocking + weighted scoring with anchor rules
├── merge.py        # Merge engine with aggressiveness parameter and undo
├── tui.py          # Rich + readchar TUI for manual dedup review
└── export.py       # vCard 3.0 writer (custom, RFC 2426 compliant) + photo extraction
```

## Commands

```bash
source .venv/bin/activate
schnabel import data/input/*.vcf    # Parse and classify contacts
schnabel analyze [-v]               # Show statistics
schnabel normalize                  # Normalize emails, phones, names
schnabel match                      # Find duplicate candidate pairs
schnabel dedup [--auto-only] [-a 0.5]  # Auto-merge + TUI review
schnabel export ./output/           # Export to real/stubs/spam VCF files
schnabel photos --extract ./photos/ # Extract contact photos
schnabel status                     # Pipeline overview
schnabel undo <merge-id>            # Undo a merge
schnabel rawparse FILE [-o OUT.vcf]  # Parse contacts from raw text files
schnabel rawparse FILE --auto-accept # Skip TUI, accept all parsed contacts
schnabel rawparse --pending          # Resume: only skipped contacts
schnabel rawparse FILE --db-import   # Also import into main database
```

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
