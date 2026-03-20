"""Blocking + scoring engine for duplicate detection."""

from collections import defaultdict

import jellyfish

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
from schnabel.db import Database
from schnabel.normalize import normalize_email, normalize_phone, name_simplified, fn_simplified


def _token_sort_ratio(s1: str, s2: str) -> float:
    """Simple token-sort similarity: sort tokens then compare with Jaro-Winkler."""
    if not s1 or not s2:
        return 0.0
    t1 = " ".join(sorted(s1.lower().split()))
    t2 = " ".join(sorted(s2.lower().split()))
    return jellyfish.jaro_winkler_similarity(t1, t2)


def find_candidate_pairs(db: Database) -> set[tuple[int, int]]:
    """Find candidate pairs using blocking on shared normalized values."""
    candidates: set[tuple[int, int]] = set()

    for norm_type in ("email", "phone_e164", "phone_last7", "name_simplified"):
        groups = db.get_normalized_groups(norm_type)
        for _value, contact_ids in groups.items():
            if len(contact_ids) < 2 or len(contact_ids) > 100:
                # Skip groups that are too large (generic names) or singletons
                continue
            # All pairs within the group
            for i in range(len(contact_ids)):
                for j in range(i + 1, len(contact_ids)):
                    a, b = min(contact_ids[i], contact_ids[j]), max(contact_ids[i], contact_ids[j])
                    candidates.add((a, b))

    return candidates


def score_contacts(a, b) -> dict:
    """Score two Contact objects on multiple dimensions.

    This is the single source of truth for contact scoring — used by both
    match.score_pair (DB-backed) and compare.score_contacts.
    """
    from schnabel.model import Contact

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


def score_pair(db: Database, a_id: int, b_id: int) -> dict:
    """Score a candidate pair by loading contacts from DB."""
    contact_a = db.get_contact(a_id)
    contact_b = db.get_contact(b_id)
    if not contact_a or not contact_b:
        return {"confidence": 0.0}
    return score_contacts(contact_a, contact_b)


def _hamming_distance(hash1: str, hash2: str) -> int:
    """Hamming distance between two hex-encoded hashes."""
    if len(hash1) != len(hash2):
        raise ValueError("Hash lengths differ")
    return bin(int(hash1, 16) ^ int(hash2, 16)).count("1")


def run_matching(db: Database, min_confidence: float = 0.10, progress_callback=None):
    """Find all candidate pairs, score them, store in similarity_pairs."""
    db.clear_similarity_pairs()

    candidates = find_candidate_pairs(db)
    total = len(candidates)

    stored = 0
    for i, (a_id, b_id) in enumerate(candidates):
        scores = score_pair(db, a_id, b_id)

        if scores["confidence"] >= min_confidence:
            db.insert_similarity_pair(
                a_id, b_id,
                confidence=scores["confidence"],
                email_score=scores["email_score"],
                phone_score=scores["phone_score"],
                name_score=scores["name_score"],
                photo_score=scores["photo_score"],
                address_score=scores["address_score"],
            )
            stored += 1

        if progress_callback and (i % 100 == 0 or i == total - 1):
            progress_callback(i + 1, total, stored)

    db.commit()
    return stored
