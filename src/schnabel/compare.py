"""Cross-file contact comparison: matching + read-only TUI."""

from pathlib import Path

import jellyfish
import readchar
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from schnabel.config import (
    ANCHOR_MAX_NAME_ONLY,
    ANCHOR_MIN_SHARED_CONTACT_AND_NAME,
    ANCHOR_MIN_SHARED_EMAIL_AND_PHONE,
    ANCHOR_MIN_SHARED_EMAIL_OR_PHONE,
    WEIGHT_ADDRESS,
    WEIGHT_EMAIL,
    WEIGHT_NAME,
    WEIGHT_PHONE,
    WEIGHT_PHOTO,
)
from schnabel.match import _hamming_distance, _token_sort_ratio
from schnabel.model import Contact
from schnabel.normalize import normalize_email, normalize_phone, name_simplified, fn_simplified
from schnabel.tui import _compare_symbol, _confidence_bar, _truncate

console = Console()


# ── Scoring (works on Contact objects directly, no DB) ───────────────────────

def score_contacts(a: Contact, b: Contact) -> dict:
    """Score a pair of contacts on multiple dimensions. Same algorithm as match.score_pair."""
    # -- Email score --
    emails_a = {normalize_email(e) for e in a.emails}
    emails_b = {normalize_email(e) for e in b.emails}
    email_score = 0.0
    has_shared_email = False
    if emails_a and emails_b:
        shared = emails_a & emails_b
        if shared:
            email_score = 1.0
            has_shared_email = True
        else:
            generic_domains = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com",
                               "gmx.ch", "gmx.net", "gmx.de", "bluewin.ch", "protonmail.com",
                               "icloud.com", "me.com", "live.com", "aol.com", "mail.com"}
            domains_a = {e.split("@")[1] for e in emails_a if "@" in e}
            domains_b = {e.split("@")[1] for e in emails_b if "@" in e}
            shared_domains = (domains_a & domains_b) - generic_domains
            if shared_domains:
                email_score = 0.3

    # -- Phone score --
    phones_a_e164 = set()
    phones_a_last7 = set()
    for p in a.phones:
        e164 = normalize_phone(p)
        if e164:
            phones_a_e164.add(e164)
        digits = "".join(c for c in p if c.isdigit())
        if len(digits) >= 7:
            phones_a_last7.add(digits[-7:])

    phones_b_e164 = set()
    phones_b_last7 = set()
    for p in b.phones:
        e164 = normalize_phone(p)
        if e164:
            phones_b_e164.add(e164)
        digits = "".join(c for c in p if c.isdigit())
        if len(digits) >= 7:
            phones_b_last7.add(digits[-7:])

    phone_score = 0.0
    has_shared_phone = False
    if phones_a_e164 and phones_b_e164:
        if phones_a_e164 & phones_b_e164:
            phone_score = 1.0
            has_shared_phone = True
    if phone_score == 0.0 and phones_a_last7 and phones_b_last7:
        if phones_a_last7 & phones_b_last7:
            phone_score = 0.7
            has_shared_phone = True

    # -- Name score --
    name_a = name_simplified(a)
    name_b = name_simplified(b)
    if not name_a and a.fn:
        name_a = fn_simplified(a.fn)
    if not name_b and b.fn:
        name_b = fn_simplified(b.fn)

    name_score = 0.0
    if name_a and name_b:
        jw = jellyfish.jaro_winkler_similarity(name_a, name_b)
        tsr = _token_sort_ratio(name_a, name_b)
        name_score = max(jw, tsr)

    # -- Photo score --
    photo_score = 0.0
    if a.photos and b.photos:
        hashes_a = {p.byte_hash for p in a.photos if p.byte_hash}
        hashes_b = {p.byte_hash for p in b.photos if p.byte_hash}
        if hashes_a & hashes_b:
            photo_score = 1.0
        else:
            for pa in a.photos:
                for pb in b.photos:
                    if pa.perceptual_hash and pb.perceptual_hash:
                        try:
                            dist = _hamming_distance(pa.perceptual_hash, pb.perceptual_hash)
                            if dist <= 5:
                                photo_score = max(photo_score, 0.8)
                            elif dist <= 10:
                                photo_score = max(photo_score, 0.5)
                        except (ValueError, TypeError):
                            pass

    # -- Address score --
    address_score = 0.0
    if a.addresses and b.addresses:
        best = 0.0
        for addr_a in a.addresses:
            for addr_b in b.addresses:
                sim = jellyfish.jaro_winkler_similarity(addr_a.lower(), addr_b.lower())
                best = max(best, sim)
        address_score = best

    # -- Weighted confidence --
    confidence = (
        email_score * WEIGHT_EMAIL
        + phone_score * WEIGHT_PHONE
        + name_score * WEIGHT_NAME
        + photo_score * WEIGHT_PHOTO
        + address_score * WEIGHT_ADDRESS
    )

    # -- Anchor rules --
    has_name_match = name_score > 0.7
    name_only = (not has_shared_email and not has_shared_phone and has_name_match
                 and email_score == 0.0 and phone_score == 0.0)

    if has_shared_email and has_shared_phone:
        confidence = max(confidence, ANCHOR_MIN_SHARED_EMAIL_AND_PHONE)
    elif (has_shared_email or has_shared_phone) and name_score >= 0.85:
        confidence = max(confidence, ANCHOR_MIN_SHARED_CONTACT_AND_NAME)
    elif has_shared_email or has_shared_phone:
        confidence = max(confidence, ANCHOR_MIN_SHARED_EMAIL_OR_PHONE)

    if name_only:
        if name_score >= 0.98:
            confidence = max(confidence, 0.80)
        else:
            confidence = min(confidence, ANCHOR_MAX_NAME_ONLY)

    return {
        "confidence": round(confidence, 4),
        "email_score": round(email_score, 4),
        "phone_score": round(phone_score, 4),
        "name_score": round(name_score, 4),
        "photo_score": round(photo_score, 4),
        "address_score": round(address_score, 4),
        "has_shared_email": has_shared_email,
        "has_shared_phone": has_shared_phone,
    }


# ── Cross-file matching ─────────────────────────────────────────────────────

def _contacts_are_identical(a: Contact, b: Contact) -> bool:
    """Check if two contacts have identical user-visible content."""
    if (a.fn or "") != (b.fn or ""):
        return False
    if (a.family_name or "") != (b.family_name or ""):
        return False
    if (a.given_name or "") != (b.given_name or ""):
        return False

    # Compare fields by type+value sets
    fields_a = sorted((f.field_type, f.field_value) for f in a.fields)
    fields_b = sorted((f.field_type, f.field_value) for f in b.fields)
    if fields_a != fields_b:
        return False

    # Compare photos by hash
    photo_hashes_a = sorted(p.byte_hash for p in a.photos if p.byte_hash)
    photo_hashes_b = sorted(p.byte_hash for p in b.photos if p.byte_hash)
    return photo_hashes_a == photo_hashes_b


def find_cross_file_pairs(contacts_a: list[Contact], contacts_b: list[Contact],
                          min_confidence: float = 0.40) -> dict:
    """Match contacts across two lists using blocking + scoring.

    Returns dict with:
        matched_identical: list of (contact_a, contact_b, scores)
        matched_different: list of (contact_a, contact_b, scores)
        only_a: list of Contact
        only_b: list of Contact
    """
    # Build blocking indexes for file B
    b_by_email: dict[str, list[int]] = {}
    b_by_phone_e164: dict[str, list[int]] = {}
    b_by_phone_last7: dict[str, list[int]] = {}
    b_by_name: dict[str, list[int]] = {}

    for i, c in enumerate(contacts_b):
        for e in c.emails:
            norm = normalize_email(e)
            if norm:
                b_by_email.setdefault(norm, []).append(i)
        for p in c.phones:
            e164 = normalize_phone(p)
            if e164:
                b_by_phone_e164.setdefault(e164, []).append(i)
            digits = "".join(ch for ch in p if ch.isdigit())
            if len(digits) >= 7:
                b_by_phone_last7.setdefault(digits[-7:], []).append(i)
        ns = name_simplified(c)
        if not ns and c.fn:
            ns = fn_simplified(c.fn)
        if ns:
            b_by_name.setdefault(ns, []).append(i)

    # Find candidate pairs via blocking
    candidates: set[tuple[int, int]] = set()  # (index_a, index_b)

    for i, c in enumerate(contacts_a):
        for e in c.emails:
            norm = normalize_email(e)
            if norm and norm in b_by_email:
                for j in b_by_email[norm]:
                    candidates.add((i, j))
        for p in c.phones:
            e164 = normalize_phone(p)
            if e164 and e164 in b_by_phone_e164:
                for j in b_by_phone_e164[e164]:
                    candidates.add((i, j))
            digits = "".join(ch for ch in p if ch.isdigit())
            if len(digits) >= 7:
                last7 = digits[-7:]
                if last7 in b_by_phone_last7:
                    for j in b_by_phone_last7[last7]:
                        candidates.add((i, j))
        ns = name_simplified(c)
        if not ns and c.fn:
            ns = fn_simplified(c.fn)
        if ns and ns in b_by_name:
            for j in b_by_name[ns]:
                candidates.add((i, j))

    # Score candidates and find best matches
    # Each contact can match at most one in the other file (best score wins)
    pair_scores: list[tuple[int, int, dict]] = []
    for i, j in candidates:
        scores = score_contacts(contacts_a[i], contacts_b[j])
        if scores["confidence"] >= min_confidence:
            pair_scores.append((i, j, scores))

    # Sort by confidence descending, then greedily assign (each contact matched once)
    pair_scores.sort(key=lambda x: x[2]["confidence"], reverse=True)
    matched_a: set[int] = set()
    matched_b: set[int] = set()
    matched_identical: list[tuple[Contact, Contact, dict]] = []
    matched_different: list[tuple[Contact, Contact, dict]] = []

    for i, j, scores in pair_scores:
        if i in matched_a or j in matched_b:
            continue
        matched_a.add(i)
        matched_b.add(j)
        if _contacts_are_identical(contacts_a[i], contacts_b[j]):
            matched_identical.append((contacts_a[i], contacts_b[j], scores))
        else:
            matched_different.append((contacts_a[i], contacts_b[j], scores))

    only_a = [contacts_a[i] for i in range(len(contacts_a)) if i not in matched_a]
    only_b = [contacts_b[j] for j in range(len(contacts_b)) if j not in matched_b]

    return {
        "matched_identical": matched_identical,
        "matched_different": matched_different,
        "only_a": only_a,
        "only_b": only_b,
    }


# ── TUI rendering ───────────────────────────────────────────────────────────

def _render_compare(contact_a: Contact | None, contact_b: Contact | None,
                    entry_idx: int, total: int, confidence: float,
                    name_a: str, name_b: str, section: str):
    """Render a comparison view for a pair of contacts (read-only)."""
    console.clear()

    # Header
    header = Text()
    header.append("schnabel compare", style="bold cyan")
    header.append(f" — {total} Unterschiede")
    header.append("        [q]uit [?]hilfe", style="dim")
    console.print(header)
    console.print("─" * console.width)

    # Entry info
    info = Text()
    info.append(f"  {section} {entry_idx + 1}/{total}", style="bold")
    if confidence > 0:
        info.append("  │  Confidence: ")
        info.append_text(_confidence_bar(confidence))
    console.print(info)
    console.print("─" * console.width)

    # Source names
    col_w = max(30, (console.width - 10) // 2)
    console.print(f"  {_truncate(name_a, col_w):<{col_w}}║   {name_b}")
    console.print("─" * console.width)

    if contact_a is None and contact_b is not None:
        # Only in B
        _render_single_side(contact_b, "right", name_a)
    elif contact_b is None and contact_a is not None:
        # Only in A
        _render_single_side(contact_a, "left", name_b)
    elif contact_a is not None and contact_b is not None:
        # Both sides — show diff
        _render_diff(contact_a, contact_b)

    console.print("─" * console.width)

    # Navigation bar
    nav = Text()
    nav.append(" n", style="bold cyan")
    nav.append("=weiter  ")
    nav.append("b", style="bold cyan")
    nav.append("=zurück  ")
    nav.append("q", style="dim")
    nav.append("=quit  ")
    nav.append("?", style="dim")
    nav.append("=hilfe")
    console.print(nav)


def _render_diff(contact_a: Contact, contact_b: Contact):
    """Render side-by-side diff of two matched contacts."""
    # Build field maps
    left_by_type: dict[str, list[str]] = {}
    for f in contact_a.fields:
        left_by_type.setdefault(f.field_type, []).append(f.field_value)

    right_by_type: dict[str, list[str]] = {}
    for f in contact_b.fields:
        right_by_type.setdefault(f.field_type, []).append(f.field_value)

    rows: list[tuple[str, str, str, str, str]] = []  # (label, left, sym, right, style_hint)

    # FN
    sym = _compare_symbol(contact_a.fn, contact_b.fn)
    rows.append(("FN:", contact_a.fn or "(nicht vorhanden)", sym,
                 contact_b.fn or "(nicht vorhanden)", _style_for_symbol(sym)))

    def _field_rows(field_type: str, label: str, normalize_fn=None):
        la = left_by_type.get(field_type, [])
        ra = right_by_type.get(field_type, [])
        n = max(len(la), len(ra))
        if n == 0:
            return
        for i in range(n):
            lv = la[i] if i < len(la) else "(nicht vorhanden)"
            rv = ra[i] if i < len(ra) else "(nicht vorhanden)"
            na = (normalize_fn(lv) or "") if normalize_fn and lv != "(nicht vorhanden)" else ""
            nb = (normalize_fn(rv) or "") if normalize_fn and rv != "(nicht vorhanden)" else ""
            s = _compare_symbol(lv, rv, na, nb)
            lbl = f"{label}:" if i == 0 else ""
            rows.append((lbl, lv, s, rv, _style_for_symbol(s)))

    _field_rows("email", "EMAIL", normalize_email)
    _field_rows("tel", "TEL", normalize_phone)

    # Photo
    photo_a = (f"[{contact_a.photos[0].photo_format} "
               f"{contact_a.photos[0].width}x{contact_a.photos[0].height}]"
               if contact_a.photos else "(nicht vorhanden)")
    photo_b = (f"[{contact_b.photos[0].photo_format} "
               f"{contact_b.photos[0].width}x{contact_b.photos[0].height}]"
               if contact_b.photos else "(nicht vorhanden)")
    sym = _compare_symbol(photo_a, photo_b)
    rows.append(("PHOTO:", photo_a, sym, photo_b, _style_for_symbol(sym)))

    _field_rows("org", "ORG")
    _field_rows("adr", "ADR")
    _field_rows("url", "URL")
    _field_rows("note", "NOTE")
    _field_rows("bday", "BDAY")
    _field_rows("title", "TITLE")
    _field_rows("nickname", "NICK")
    _field_rows("role", "ROLE")

    _print_rows(rows)


def _render_single_side(contact: Contact, side: str, missing_label: str):
    """Render a contact that only exists on one side."""
    rows: list[tuple[str, str, str, str, str]] = []
    missing = f"(nicht in {missing_label})"

    if side == "left":
        sym = "⊇"
        make_row = lambda lbl, val: (lbl, val, sym, missing, "only_left")
    else:
        sym = "⊆"
        make_row = lambda lbl, val: (lbl, missing, sym, val, "only_right")

    if contact.fn:
        rows.append(make_row("FN:", contact.fn))

    for f in contact.fields:
        label = {
            "email": "EMAIL", "tel": "TEL", "adr": "ADR", "org": "ORG",
            "url": "URL", "note": "NOTE", "bday": "BDAY", "title": "TITLE",
            "nickname": "NICK", "role": "ROLE",
        }.get(f.field_type, f.field_type.upper())
        rows.append(make_row(f"{label}:", f.field_value))

    if contact.photos:
        photo_info = (f"[{contact.photos[0].photo_format} "
                      f"{contact.photos[0].width}x{contact.photos[0].height}]")
        rows.append(make_row("PHOTO:", photo_info))

    _print_rows(rows)


def _style_for_symbol(sym: str) -> str:
    """Return a style hint string for a comparison symbol."""
    if sym == "≡":
        return "identical"
    if sym == "≃":
        return "equivalent"
    if sym == "≠":
        return "different"
    if sym == "⊇":
        return "only_left"
    if sym == "⊆":
        return "only_right"
    return ""


def _print_rows(rows: list[tuple[str, str, str, str, str]]):
    """Print comparison rows with color-coded diff highlighting."""
    for label, left, sym, right, style_hint in rows:
        left_str = _truncate(left, 28)
        right_str = _truncate(right, 28)
        line = Text()
        line.append(f"  {label:<10}")

        if style_hint == "identical":
            line.append(f"{left_str:<28}", style="dim")
            line.append(f" {sym} ", style="dim")
            line.append(f"  {right_str:<28}", style="dim")
        elif style_hint == "equivalent":
            line.append(f"{left_str:<28}", style="dim yellow")
            line.append(f" {sym} ", style="dim yellow")
            line.append(f"  {right_str:<28}", style="dim yellow")
        elif style_hint == "different":
            line.append(f"{left_str:<28}", style="bold red")
            line.append(f" {sym} ", style="bold")
            line.append(f"  {right_str:<28}", style="bold green")
        elif style_hint == "only_left":
            line.append(f"{left_str:<28}", style="bold yellow")
            line.append(f" {sym} ", style="bold yellow")
            line.append(f"  {right_str:<28}", style="dim")
        elif style_hint == "only_right":
            line.append(f"{left_str:<28}", style="dim")
            line.append(f" {sym} ", style="bold yellow")
            line.append(f"  {right_str:<28}", style="bold yellow")
        else:
            line.append(f"{left_str:<28}")
            line.append(f" {sym} ")
            line.append(f"  {right_str:<28}")

        console.print(line)


# ── TUI main loop ───────────────────────────────────────────────────────────

def run_compare_tui(matched_different: list[tuple[Contact, Contact, dict]],
                    only_a: list[Contact], only_b: list[Contact],
                    name_a: str, name_b: str):
    """Run the read-only comparison TUI."""
    # Build flat entry list: (contact_a_or_None, contact_b_or_None, confidence, section_label)
    entries: list[tuple[Contact | None, Contact | None, float, str]] = []

    for ca, cb, scores in matched_different:
        entries.append((ca, cb, scores["confidence"], "Paar"))

    for c in only_a:
        entries.append((c, None, 0.0, f"Nur {name_a}"))

    for c in only_b:
        entries.append((None, c, 0.0, f"Nur {name_b}"))

    if not entries:
        console.print("[green]Keine Unterschiede gefunden.[/green]")
        return

    total = len(entries)
    idx = 0

    while 0 <= idx < total:
        ca, cb, conf, section = entries[idx]
        _render_compare(ca, cb, idx, total, conf, name_a, name_b, section)

        key = readchar.readchar().lower()

        if key in ("q", "\x03"):  # q or Ctrl-C
            return
        elif key in ("n", "s", " ", "\r"):
            idx += 1
        elif key == "b":
            if idx > 0:
                idx -= 1
        elif key == "?":
            console.clear()
            console.print(Panel(
                "[bold]Tastaturkürzel[/bold]\n\n"
                "[cyan]n[/cyan]  weiter (nächster Unterschied)\n"
                "[cyan]b[/cyan]  zurück (vorheriger Unterschied)\n"
                "[dim]q[/dim]  beenden\n\n"
                "[bold]Symbole:[/bold]\n"
                "  [dim]≡[/dim]  identisch\n"
                "  [dim yellow]≃[/dim yellow]  äquivalent (normalisiert gleich)\n"
                "  [bold red]≠[/bold red]  verschieden\n"
                "  [bold yellow]⊇[/bold yellow]  nur links vorhanden\n"
                "  [bold yellow]⊆[/bold yellow]  nur rechts vorhanden\n\n"
                "Beliebige Taste zum Fortfahren...",
                title="Hilfe",
            ))
            readchar.readchar()

    # End of list
    console.clear()
    console.print(f"\n[bold green]Alle {total} Unterschiede durchgesehen.[/bold green]")
