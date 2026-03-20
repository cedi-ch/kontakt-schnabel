# CLAUDE.md

## Project: kontakt-schnabel v1.0.0

vCard CLI tool for merging, editing, deduplicating, sanitizing, and separating contact files.
Python, CLI + Rich TUI. Target: GrapheneOS + DAVx5 + Thunderbird + Infomaniak/mailbox.org CardDAV.

**Repository:** https://github.com/cedi-ch/kontakt-schnabel

## Status: v1.0.0 — Spec-Driven Rebuild Complete

All 5 phases of the spec-driven rebuild are done. 12 bugs fixed, 224 tests passing, RFC 2426 compliant export with UIDs.

## Phase Documents

All planning and audit documents live in `docs/`:

| File | Content |
|------|---------|
| `docs/architecture-legacy.md` | Original architecture reference (pre-restart) |
| `docs/01-audit.md` | Full codebase audit: 38 bugs, git history analysis, test coverage |
| `docs/02-spec.md` | Spec-driven development — requirements + test definitions |
| `docs/03-gap-analysis.md` | Current capabilities vs spec |
| `docs/04-plan.md` | Implementation plan (5 steps) |
| `docs/05-execution.md` | Execution log |

## Development

```bash
source .venv/bin/activate
python -m pytest tests/ -v
```

## Key Rules

- Data files in `data/` and `input/` are gitignored (personal contacts)
- Output in `output/` is gitignored
- Never trust vobject output without post-parse validation
- All vCard export MUST pass roundtrip test (export -> re-import -> compare)
- RFC 2426 compliance is non-negotiable for the export writer
- Phone comparison must use E.164 normalization, not string matching
- UIDs must be preserved on roundtrip; generated as UUID v4 when missing
- Merge undo must remove fields that were added to the survivor
