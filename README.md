# kontakt-schnabel

A CLI tool for merging, editing, deduplicating, sanitizing, and separating vCard files.

Built for cleaning up 10+ years of messy contacts from iPhones, Android, Windows, Mac, and Linux — for use with GrapheneOS, DAVx5, Thunderbird, and messengers.

## Features

- **Import** vCard files with multi-encoding fallback (UTF-8, ISO-8859-1, CP1252, UTF-16)
- **Classify** contacts into real, stub (Thunderbird Gesammelte Adressen), and spam
- **Normalize** emails, phones (E.164), and names for accurate matching
- **Sanitize** within-contact duplicates: phones, emails, addresses, URLs, text fields
- **Match** duplicates via blocking + weighted scoring with anchor rules
- **Deduplicate** with configurable aggressiveness (auto-merge + interactive TUI)
- **Split** VCF files into multiple named target files
- **Parse** contacts from unstructured text files (rawparse)
- **Export** clean vCard 3.0 files with photo normalization
- **Session management** with timestamped output directories and logging

## Installation

```bash
git clone https://github.com/cedi-ch/kontakt-schnabel.git
cd kontakt-schnabel
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Quick Start

```bash
# 1. Import vCard files
schnabel import data/input/*.vcf

# 2. Analyze what you have
schnabel analyze -v

# 3. Normalize for matching
schnabel normalize

# 4. Sanitize within-contact duplicates
schnabel sanitize

# 5. Find duplicate candidates
schnabel match

# 6. Deduplicate (auto + interactive)
schnabel dedup -a 0.5

# 7. Export clean files
schnabel export
```

## Pipeline

```
VCF files ──► import ──► classify ──► normalize ──► sanitize
                                                       │
              export ◄── dedup ◄── match ◄─────────────┘
                │
                ▼
         output/YYYY-MM-DD_HHMM_export/
           ├── real_contacts.vcf
           ├── stubs.vcf
           └── spam.vcf
```

## Command Reference

| Command | Description | Key Flags |
|---------|-------------|-----------|
| `schnabel import [FILES]` | Import vCard files into database | `--dir DIR` |
| `schnabel analyze` | Show contact statistics | `-v` verbose |
| `schnabel normalize` | Normalize emails, phones, names | |
| `schnabel sanitize` | Deduplicate fields within contacts | |
| `schnabel match` | Find duplicate candidate pairs | `--min-confidence 0.10` |
| `schnabel dedup` | Auto-merge + interactive TUI review | `-a 0.5`, `--auto-only`, `--pending` |
| `schnabel export [DIR]` | Export to clean vCard 3.0 files | `--max-lines N`, `--preserve-photos` |
| `schnabel rawparse FILE` | Parse contacts from text files | `-o FILE`, `--db-import`, `--auto-accept`, `--pending` |
| `schnabel split FILE` | Split VCF into named targets | `-o DIR`, `--no-rest`, `--pending` |
| `schnabel status` | Pipeline overview | |
| `schnabel stats` | Detailed session statistics | |
| `schnabel reset` | Delete database and start fresh | `--confirm` |
| `schnabel photos` | Manage contact photos | `--extract DIR` |
| `schnabel undo MERGE_ID` | Undo a specific merge | |

**Global flags:** `--db PATH` (database path), `--no-export` (skip auto-export)

## Aggressiveness

The `--aggressiveness` / `-a` parameter controls how eagerly contacts are auto-merged:

| Aggressiveness | Threshold | Behavior |
|:-:|:-:|---|
| 0.0 | 95% | Only near-exact matches |
| 0.5 (default) | 72.5% | Balanced |
| 1.0 | 50% | Aggressive fuzzy matching |

## Interactive TUI

### Dedup TUI (schnabel dedup)

| Key | Action |
|-----|--------|
| `a` | Auto-merge (richer contact survives) |
| `l` / `r` | Keep left / right |
| `s` | Skip (revisit later) |
| `n` | Not a duplicate |
| `b` | Back (undo previous, return to it) |
| `1` / `2` / `x` | Delete left / right / both |
| `u` | Undo last merge |
| `e` / `d` / `+` | Edit / delete / add field |
| `?` | Help |
| `q` | Quit (progress saved) |

### Rawparse TUI (schnabel rawparse)

| Key | Action |
|-----|--------|
| `a` | Accept contact |
| `r` | Reject contact |
| `x` | Delete contact (removed from state) |
| `s` | Skip |
| `b` | Back |
| `e` / `d` / `+` | Edit / delete / add field |
| `w` | Change field type |
| `?` | Help |
| `q` | Quit |

### Split TUI (schnabel split)

| Key | Action |
|-----|--------|
| `1-N` | Assign to target file |
| `s` | Skip |
| `b` | Back |
| `u` | Undo last action |
| `x` | Delete contact |
| `e` / `d` / `+` | Edit / delete / add field |
| `?` | Help |
| `q` | Quit (state saved) |

## Architecture

```
src/schnabel/
├── cli.py          # Click CLI with all commands
├── config.py       # Global config, make_output_dir()
├── db.py           # SQLite database with similarity graph + metadata
├── model.py        # Contact/ContactField/Photo dataclasses
├── reader.py       # vCard parser (vobject + regex fallback)
├── rawparse.py     # Raw text parser (subtract-and-classify pipeline)
├── rawtui.py       # TUI for reviewing raw-parsed contacts
├── classify.py     # 3-tier classification: real / stub / spam
├── normalize.py    # 4-stage normalization pipeline
├── sanitize.py     # Within-contact field cleanup and dedup
├── match.py        # Blocking + weighted scoring with anchor rules
├── merge.py        # Merge engine with aggressiveness and undo
├── tui.py          # TUI for manual dedup review
├── splittui.py     # TUI for splitting VCF files
└── export.py       # vCard 3.0 writer + photo extraction
```

## Development

```bash
python -m pytest tests/ -v
```
