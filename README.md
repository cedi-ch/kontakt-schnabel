# kontakt-schnabel

CLI-Tool zum Zusammenführen, Bereinigen, Deduplizieren und Aufteilen von vCard-Kontaktdateien.

Gebaut für das Aufräumen von 10+ Jahren Kontakt-Chaos aus iPhones, Android, Windows, Mac und Linux — für den Einsatz mit GrapheneOS, DAVx5, Thunderbird und Infomaniak/mailbox.org CardDAV.

## Was es löst

Wer Kontakte aus verschiedenen Quellen zusammenführt, kennt das Problem: Duplikate, kaputte Encodings, fehlende Felder, Foto-Daten die in Telefonnummern landen. kontakt-schnabel nimmt einen Stapel VCF-Dateien, parst sie robust (auch kaputte), dedupliziert sie intelligent, und exportiert saubere RFC-2426-konforme vCard-3.0-Dateien mit UIDs — bereit für CardDAV-Import.

## Features

- **Import** mit Multi-Encoding-Fallback (UTF-8, ISO-8859-1, CP1252, UTF-16) und gebrochenem Quoted-Printable
- **Klassifizierung** in echte Kontakte, Stubs (Thunderbird Gesammelte Adressen) und Spam
- **Normalisierung** von Emails, Telefonnummern (E.164) und Namen für präzises Matching
- **Sanitization** — Deduplizierung innerhalb eines Kontakts: Telefone, Emails, Adressen, URLs
- **Schweizer Telefon-TYPE-Erkennung** — 079 wird automatisch TYPE=CELL, 044 wird TYPE=HOME
- **Schweizer Formatierung** — CH-Nummern als `079 123 45 67`, ausländische als `+49 170 1234567`
- **Duplicate Detection** via Blocking + gewichtetes Scoring mit Anchor-Rules
- **Deduplizierung** mit konfigurierbarer Aggressivität (Auto-Merge + interaktives TUI)
- **Merge mit vollem Undo** — jeder Merge kann rückgängig gemacht werden, inklusive Feld-Entfernung
- **CATEGORIES-Support** — Import, Export, interaktive Zuweisung, Export nach Kategorien
- **UID-Support** — bestehende UIDs beibehalten, fehlende als UUID v4 generieren
- **Split** — VCF-Dateien interaktiv in benannte Zieldateien aufteilen
- **Rawparse** — Kontakte aus unstrukturierten Textdateien extrahieren
- **PDF-Export** — Telefonliste als A4-Landscape-PDF
- **Export-Vorschau** — Zusammenfassung vor dem Export mit Kategorie-Breakdown
- **Export nach Kategorien** — separate VCF-Datei pro Kategorie (`--by-category`)
- **Pipeline-Backup** — SQLite-Backup vor kritischen Schritten
- **Session-Management** mit Zeitstempel-Verzeichnissen und Logging

## Installation

```bash
git clone https://github.com/cedi-ch/kontakt-schnabel.git
cd kontakt-schnabel
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

Erfordert Python 3.12+.

## Schnellstart

```bash
# 1. VCF-Dateien importieren
schnabel import data/input/*.vcf

# 2. Überblick verschaffen
schnabel analyze -v

# 3. Normalisieren für Matching
schnabel normalize

# 4. Innerhalb der Kontakte aufräumen
schnabel sanitize

# 5. Duplikate finden
schnabel match

# 6. Deduplizieren (Auto + interaktiv)
schnabel dedup -a 0.5

# 7. Kategorien zuweisen
schnabel categorize

# 8. Saubere Dateien exportieren
schnabel export
schnabel export --by-category
```

## Pipeline

```
VCF-Dateien ──> import ──> classify ──> normalize ──> sanitize
                                                         |
              export <── dedup <── match <────────────────┘
                |
                v
         output/YYYY-MM-DD_HHMM_export/
           ├── real_contacts.vcf       (oder nach Kategorie aufgeteilt)
           ├── stubs.vcf
           └── spam.vcf
```

## Befehlsreferenz

| Befehl | Beschreibung | Wichtige Flags |
|--------|-------------|----------------|
| `schnabel import [FILES]` | VCF-Dateien in Datenbank importieren | `--dir DIR` |
| `schnabel analyze` | Kontakt-Statistiken anzeigen | `-v` detailliert |
| `schnabel normalize` | Emails, Telefone, Namen normalisieren | |
| `schnabel sanitize` | Feld-Duplikate innerhalb Kontakten bereinigen | |
| `schnabel match` | Duplikat-Kandidaten finden und scoren | `--min-confidence 0.10` |
| `schnabel dedup` | Auto-Merge + interaktives TUI-Review | `-a 0.5`, `--auto-only`, `--pending` |
| `schnabel export [DIR]` | Saubere vCard-3.0-Dateien exportieren | `--by-category`, `--max-lines N`, `--no-preview` |
| `schnabel categorize` | Kategorien interaktiv zuweisen | `--uncategorized` |
| `schnabel rawparse FILE` | Kontakte aus Textdateien extrahieren | `-o FILE`, `--db-import`, `--auto-accept` |
| `schnabel split FILE` | VCF in benannte Zieldateien aufteilen | `-o DIR`, `--no-rest`, `--pending` |
| `schnabel compare A B` | Zwei VCF-Dateien Side-by-Side vergleichen | `--min-confidence 0.40` |
| `schnabel pdf FILE` | PDF-Telefonliste generieren | `-o FILE`, `--title TEXT` |
| `schnabel status` | Pipeline-Übersicht | |
| `schnabel stats` | Detaillierte Session-Statistiken | |
| `schnabel undo MERGE_ID` | Einen bestimmten Merge rückgängig machen | |
| `schnabel reset` | Datenbank löschen und neu starten | `--confirm` |
| `schnabel photos` | Kontaktfotos verwalten | `--extract DIR` |

**Globale Flags:** `--db PATH` (Datenbank-Pfad), `--no-export` (Auto-Export überspringen)

## Aggressivität

Der `--aggressiveness` / `-a` Parameter steuert, wie aggressiv Auto-Merges durchgeführt werden:

| Aggressivität | Schwellenwert | Verhalten |
|:---:|:---:|---|
| 0.0 | 95% | Nur fast-exakte Matches |
| 0.5 (Standard) | 72.5% | Ausgewogen |
| 1.0 | 50% | Aggressives Fuzzy-Matching |

## Interaktive TUIs

### Dedup TUI (`schnabel dedup`)

| Taste | Aktion |
|-------|--------|
| `a` | Auto-Merge (reicherer Kontakt überlebt) |
| `l` / `r` | Links / rechts behalten |
| `s` | Überspringen (später nochmal) |
| `n` | Kein Duplikat |
| `b` | Zurück (vorherige Aktion rückgängig) |
| `1` / `2` / `x` | Links / rechts / beide löschen |
| `u` | Letzten Merge rückgängig |
| `e` / `d` / `+` | Feld bearbeiten / löschen / hinzufügen |
| `?` | Hilfe |
| `q` | Beenden (Fortschritt gespeichert) |

### Rawparse TUI (`schnabel rawparse`)

| Taste | Aktion |
|-------|--------|
| `a` | Kontakt akzeptieren |
| `r` | Kontakt verwerfen |
| `x` | Kontakt löschen |
| `s` / `b` | Überspringen / Zurück |
| `e` / `d` / `+` / `w` | Bearbeiten / Löschen / Hinzufügen / Typ wechseln |

### Split TUI (`schnabel split`)

| Taste | Aktion |
|-------|--------|
| `1-N` | Zieldatei zuweisen |
| `s` / `b` / `u` | Überspringen / Zurück / Rückgängig |
| `x` | Kontakt löschen |
| `e` / `d` / `+` | Bearbeiten / Löschen / Hinzufügen |

## Architektur

```
src/schnabel/
├── cli.py          Click CLI mit allen Befehlen
├── config.py       Globale Konfiguration
├── db.py           SQLite mit Similarity-Graph, CASCADE DELETE, CHECK Constraints
├── model.py        Contact/ContactField/Photo Dataclasses (mit UID)
├── reader.py       vCard-Parser (vobject + Regex-Fallback + X-ANNIVERSARY)
├── rawparse.py     Textdatei-Parser (Subtract-and-Classify)
├── classify.py     3-Tier-Klassifizierung: real / stub / spam
├── normalize.py    4-Stufen-Normalisierung
├── sanitize.py     Feld-Cleanup, BDAY-Normalisierung, TYPE-Auto-Erkennung
├── match.py        Blocking + gewichtetes Scoring mit Anchor-Rules
├── merge.py        Merge-Engine mit E.164-Phone-Dedup und vollem Undo
├── export.py       vCard-3.0-Writer (RFC 2426), Category-Export, Vorschau
├── compare.py      Cross-File-Vergleich mit Scoring
├── tui.py          Dedup-TUI mit Score-Recalculation nach Edits
├── rawtui.py       Rawparse-TUI
├── splittui.py     Split-TUI mit atomaren State-Writes und Foto-Persistenz
├── cattui.py       Kategorisierungs-TUI
├── ui_helpers.py   Geteilte UI-Funktionen (truncate, confidence_bar, safe_readchar)
└── pdf.py          PDF-Telefonlisten-Generator
```

## Entwicklung

```bash
source .venv/bin/activate
python -m pytest tests/ -v          # 224 Tests
python -m pytest tests/ --cov=schnabel  # Coverage-Report
```

## Hintergrund

Das Projekt entstand nach einem Produktions-Incident: Base64-PHOTO-Daten landeten in TEL-Feldern, was beim Infomaniak-CardDAV-Import zum Verlust hunderter Kontakte führte. Nach einem vollständigen Audit (38 Bugs dokumentiert) wurde die Software spec-driven neu aufgebaut — mit Roundtrip-Tests, RFC-Compliance und Defense-in-Depth gegen Parser-Fehler.

## Lizenz

MIT
