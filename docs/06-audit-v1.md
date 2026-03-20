# Post-Release Audit: kontakt-schnabel v1.0.0

**Datum:** 2026-03-20
**Scope:** Vollständiger Code-Review aller 19 Source-Files und 18 Test-Files nach Abschluss des Spec-Driven Rebuild
**Methode:** Frisches Audit aus der Vogelperspektive mit tiefen Drill-Downs — kein Rückgriff auf vorherige Analyse

---

## Zusammenfassung

31 Findings, davon **0 P0**, **3 P1**, **12 P2**, **10 P3**, plus **2 Test-Gap-Findings**. Kein kritisches Datenverlust-Risiko mehr (der ursprüngliche PHOTO-Bleed-Bug ist solide behoben). Die verbleibenden P1-Issues betreffen RFC-Compliance bei der Feld-Escaping-Logik und stille Datenverluste bei Adress-Komponenten.

---

## P1 — Hohe Priorität (3)

### BUG-01: FN-Wert wird doppelt escaped
**Datei:** `export.py:86`
**Problem:** `_escape_vcard_value()` escaped Semikola und Kommas in FN, aber RFC 2426 §3.1.1 definiert FN als einfachen Text-Wert — dort müssen nur Backslash und Newline escaped werden. Ergebnis: `Smith\, Jr.` statt `Smith, Jr.` in Adressbüchern.
**Impact:** Jeder exportierte Kontakt mit Komma oder Semikolon im FN zeigt Backslash-Artefakte.
**Fix:** Eigene `_escape_text_value()` für Freitext-Felder (FN, TITLE, NOTE, NICKNAME, ROLE, ORG) die nur `\` und `\n` escaped. `;` und `,` Escaping nur für strukturierte Felder (N, ADR).

### BUG-02: N-Feld escaped Kommas fälschlicherweise
**Datei:** `export.py:89-96`
**Problem:** N-Komponenten werden mit `_escape_vcard_value()` escaped, was auch Kommas escaped. RFC 2426 §3.1.2: N-Komponenten sind `;`-getrennt, Multi-Werte innerhalb einer Komponente sind `,`-getrennt. Komma-Escaping zerstört Multi-Wert-Komponenten (z.B. mehrere Vornamen "Hans,Peter").
**Impact:** Multi-Wert-Name-Komponenten werden korrumpiert.
**Fix:** Für N-Komponenten nur Semikola, Backslash und Newline escapen — nicht Kommas.

### BUG-03: ADR verliert PO Box und Extended Address
**Datei:** `reader.py:471-478`, `export.py:114-124`
**Problem:** Der Reader serialisiert ADR als 5 Teile (Street, City, Region, Code, Country) und lässt PO Box und Extended Address weg. RFC 2426 definiert 7 ADR-Komponenten. Wenn die Original-vCard PO-Box-Daten hat, gehen sie beim Import permanent verloren.
**Impact:** Stiller Datenverlust bei Adressen mit PO Box oder Extended Address.
**Fix:** Alle 7 ADR-Komponenten im internen Format speichern.

---

## P2 — Mittlere Priorität (12)

### BUG-04: ENCODING=BASE64 vs ENCODING=b
**Datei:** `export.py:156`
**Problem:** `ENCODING=BASE64` ist vCard-2.1-Syntax. RFC 2426 (vCard 3.0) spezifiziert `ENCODING=b`. Die meisten Parser akzeptieren beides, aber strenge Validatoren könnten das Foto ablehnen.
**Impact:** Gering, da bewusste Entscheidung für Kompatibilität. Sollte dokumentiert werden.

### BUG-05: PHOTO als einzelne sehr lange Zeile
**Datei:** `export.py:156`
**Problem:** Ein 40KB-Foto produziert ~55'000 Bytes Base64 auf einer logischen Zeile. `_fold_line` faltet zwar, aber das ergibt tausende Continuation-Lines. Manche Parser haben Buffer-Limits.
**Impact:** Sehr grosse Fotos könnten bei speicherbeschränkten Clients Import-Fehler verursachen.

### BUG-06: Undo macht Namensänderungen nicht rückgängig
**Datei:** `merge.py:152-157`
**Problem:** Wenn ein Merge den Namen des Survivors überschreibt (weil der Absorbed einen besseren Namen hat), macht Undo das nicht rückgängig. Der Code erkennt `name_updated` aber tut dann nichts (`pass`).
**Impact:** Nach Undo behält der Survivor den Namen des Absorbed — teilweise Datenkorruption.
**Fix:** Original-Namen des Survivors in `fields_added` speichern und bei Undo wiederherstellen.

### BUG-07: Undo macht Foto-Ergänzungen nicht rückgängig
**Datei:** `merge.py:146-149`
**Problem:** Kopierte Fotos werden bei Undo nicht entfernt (`pass`).
**Impact:** Nach Undo behält der Survivor Fotos vom Absorbed.

### BUG-08: reassign_pairs behält niedrigsten statt höchsten Score
**Datei:** `db.py:444-450`
**Problem:** Bei Duplikat-Bereinigung nach Pair-Reassignment wird `MIN(id)` behalten. Das ist zufällig und könnte den niedrigeren Confidence-Wert behalten.
**Impact:** Falsche Confidence-Anzeige im TUI nach Merges.
**Fix:** `MIN(id)` durch Subquery ersetzen die höchste Confidence bevorzugt.

### BUG-09: Nur erste URL wird geparst
**Datei:** `reader.py:506-510`
**Problem:** `hasattr(vcard, "url")` liefert nur die erste URL. `url_list` wird nicht verwendet.
**Impact:** Kontakte mit mehreren URLs verlieren alle ausser der ersten.

### BUG-10: Nur erstes ORG, TITLE, NOTE wird geparst
**Dateien:** `reader.py:484-503`
**Problem:** Gleiche Single-Instance-Pattern für ORG, TITLE, NOTE. Multiple Instanzen werden ignoriert.
**Impact:** Selten, aber inkonsistent mit Email/Tel-Handling.

### BUG-11: Stale Field-Liste in Sanitize Step 8
**Datei:** `sanitize.py:497-509`
**Problem:** Step 8 (TYPE-Auto-Erkennung) arbeitet auf der Field-Liste die vor Steps 2-6 geladen wurde. Wenn Step 2 ein Telefon-Duplikat gelöscht hat, referenziert Step 8 möglicherweise eine gelöschte `f.id`.
**Impact:** TYPE-Update auf gelöschtem Feld (No-Op) oder falsches Feld.
**Fix:** Contact nach Step 6 aus DB neu laden (wie nach Step 1).

### BUG-12: score_contacts() ist Copy-Paste von score_pair()
**Datei:** `compare.py:33-167` vs `match.py:50-193`
**Problem:** Zwei identische Scoring-Implementierungen. Jede Änderung an einer muss manuell in der anderen repliziert werden.
**Impact:** Code-Drift-Risiko. Compare und Dedup könnten unterschiedliche Scores produzieren.
**Fix:** Scoring in Shared Function extrahieren.

### BUG-13: Nur erste CATEGORIES-Zeile geparst
**Datei:** `reader.py:522-533`
**Problem:** `vcard.categories` liefert nur die erste CATEGORIES-Zeile. `categories_list` wird nicht verwendet.
**Impact:** vCards mit mehreren CATEGORIES-Zeilen verlieren Kategorien.

---

## P3 — Niedrige Priorität (10)

### BUG-14: CATEGORIES-Werte nicht escaped
**Datei:** `export.py:150`
**Problem:** Kategorie-Namen mit Kommas werden als mehrere Kategorien interpretiert.

### BUG-15: URL-Werte nicht escaped
**Datei:** `export.py:132`
**Problem:** Newlines in URLs (aus korrupten Daten) würden die vCard-Struktur brechen.

### BUG-16: BDAY-Format nicht validiert vor Export
**Datei:** `export.py:134`
**Problem:** Nicht-ISO-Daten werden direkt exportiert.

### BUG-17: UTF-16 in Encoding-Chain ist Dead Code
**Datei:** `config.py:33`
**Problem:** ISO-8859-1 akzeptiert alles → UTF-16 wird nie erreicht.

### BUG-18: fix_broken_qp versagt bei Multi-Byte über Zeilengrenzen
**Datei:** `reader.py:105-114`
**Problem:** QP-Sequenzen die über Zeilengrenzen gehen werden nicht dekodiert. Best-Effort, akzeptabel.

### BUG-19: Field-Nummern im TUI auf 0-9 limitiert
**Datei:** `tui.py:264-278`
**Problem:** `readchar.readchar()` liest nur ein Zeichen. Kontakte mit 11+ Feldern können Felder >9 nicht editieren.

### BUG-20: extract_photos überschreibt bei gleichem Namen
**Datei:** `export.py:374-400`
**Problem:** Zwei Kontakte mit gleichem FN überschreiben sich gegenseitig.
**Fix:** Contact-ID an Dateinamen anhängen.

### BUG-21: Rawparse Phone-Re-Removal-Logik inkonsistent
**Datei:** `rawparse.py:250-267`
**Problem:** Bei teilweise invaliden Phone-Matches wird das Removal aus dem originalen Block neu berechnet, ohne vorherige Birthday-Entfernung zu berücksichtigen.

### BUG-22: run_split_tui `key` undefiniert bei leerem Loop
**Datei:** `splittui.py:642`
**Problem:** `key` wird ausserhalb des Inner-Loops geprüft, ist aber undefiniert wenn der Loop nie betreten wurde.
**Fix:** `key = None` initialisieren.

### BUG-23: _parse_bday False-Positive bei "1.1"
**Datei:** `sanitize.py:260`
**Problem:** Strings wie "1.1" werden als Datum interpretiert. Im BDAY-Kontext unrealistisch.

---

## Test-Gaps (2)

### BUG-24: Fehlende End-to-End-Tests
**Beschreibung:** Keine Tests für:
- `run_matching()` E2E (Blocking + Scoring + Storage)
- `normalize_contacts()` E2E
- `auto_resolve()` in merge.py
- Komplette CLI-Pipeline (import → normalize → sanitize → match → dedup → export)
- `_write_split` Chunking-Logik
- ADR-Export mit Legacy-Komma-Format
- `_normalize_contact_photos()` mit echten Bilddaten

### BUG-25: repair_n_field() ist Dead Code
**Datei:** `sanitize.py`
**Problem:** `repair_n_field()` ist definiert und getestet, wird aber nirgends aufgerufen. Die N-Feld-Reparatur-Heuristik ist nie in die Pipeline integriert worden.
**Fix:** In `sanitize_contacts()` aufrufen oder entfernen.

---

## Severity-Übersicht

| Severity | Anzahl | Beschreibung |
|----------|--------|-------------|
| P0 Kritisch | 0 | Keine kritischen Datenverlust-Bugs |
| P1 Hoch | 3 | FN-Escaping, N-Komma-Escaping, ADR-Datenverlust |
| P2 Mittel | 12 | Undo-Lücken, Parser-Lücken, Code-Drift, Stale State |
| P3 Niedrig | 10 | Escaping-Edge-Cases, Dead Code, TUI-Limits |

---

## Prioritäts-Empfehlungen

1. **P1 sofort fixen** — BUG-01/02 betreffen jeden exportierten Kontakt mit Sonderzeichen im Namen. BUG-03 verliert Adress-Daten.
2. **BUG-06/07 (Undo)** — User erwarten vollständiges Undo.
3. **BUG-11 (Stale Fields)** und **BUG-08 (Pair-Confidence)** — Können zu falschen Daten führen.
4. **BUG-25 (Dead Code)** — `repair_n_field` integrieren oder entfernen.
5. **BUG-12 (Score-Duplikat)** — Code-Drift vermeiden durch Shared Function.
6. **BUG-24 (Test-Gaps)** — E2E-Pipeline-Tests vor dem nächsten Daten-Import.
