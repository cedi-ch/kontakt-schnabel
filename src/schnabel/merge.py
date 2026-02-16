"""Merge engine with aggressiveness parameter and undo support."""

import json

from schnabel.db import Database
from schnabel.model import Contact, ContactField


def aggressiveness_to_threshold(aggressiveness: float) -> float:
    """Convert aggressiveness (0.0–1.0) to confidence threshold.

    aggr=0.0 → threshold 0.95 (only exact matches)
    aggr=0.5 → threshold 0.725 (default)
    aggr=1.0 → threshold 0.50 (fuzzy matches)
    """
    return 0.95 - (aggressiveness * 0.45)


def determine_survivor(db: Database, a_id: int, b_id: int) -> tuple[int, int]:
    """Determine which contact is richer (survivor) and which gets absorbed.

    Returns (survivor_id, absorbed_id).
    """
    a = db.get_contact(a_id)
    b = db.get_contact(b_id)
    if not a or not b:
        return a_id, b_id

    # Richer contact survives
    if a.field_count > b.field_count:
        return a_id, b_id
    elif b.field_count > a.field_count:
        return b_id, a_id
    else:
        # Tie-break: longer FN, or lower ID
        if len(a.fn) > len(b.fn):
            return a_id, b_id
        elif len(b.fn) > len(a.fn):
            return b_id, a_id
        return (a_id, b_id) if a_id < b_id else (b_id, a_id)


def merge_contacts(db: Database, survivor_id: int, absorbed_id: int,
                   merge_type: str = "auto", confidence: float = 0.0) -> int:
    """Merge absorbed contact into survivor. Returns merge_history ID."""
    survivor = db.get_contact(survivor_id)
    absorbed = db.get_contact(absorbed_id)
    if not survivor or not absorbed:
        raise ValueError(f"Contact not found: {survivor_id} or {absorbed_id}")

    fields_added = {"emails": [], "phones": [], "fields": [], "photos": 0, "name_updated": False}

    # Union emails
    existing_emails = {e.lower() for e in survivor.emails}
    for f in absorbed.fields:
        if f.field_type == "email" and f.field_value.lower() not in existing_emails:
            db.add_contact_field(survivor_id, f)
            fields_added["emails"].append(f.field_value)

    # Union phones
    existing_phones = set(survivor.phones)
    for f in absorbed.fields:
        if f.field_type == "tel" and f.field_value not in existing_phones:
            db.add_contact_field(survivor_id, f)
            fields_added["phones"].append(f.field_value)

    # Add missing non-duplicate fields (address, org, title, note, url, bday)
    for f in absorbed.fields:
        if f.field_type in ("email", "tel"):
            continue
        # Check if survivor already has this type+value
        has_it = any(
            sf.field_type == f.field_type and sf.field_value == f.field_value
            for sf in survivor.fields
        )
        if not has_it:
            db.add_contact_field(survivor_id, f)
            fields_added["fields"].append(f"{f.field_type}={f.field_value}")

    # Photos: add missing ones by byte_hash
    existing_hashes = {p.byte_hash for p in survivor.photos if p.byte_hash}
    for p in absorbed.photos:
        if p.byte_hash and p.byte_hash not in existing_hashes:
            db.add_photo(survivor_id, p)
            fields_added["photos"] += 1

    # Name: prefer the more complete structured name
    if not survivor.has_structured_name and absorbed.has_structured_name:
        db.update_contact_name(
            survivor_id, absorbed.fn or survivor.fn,
            absorbed.family_name, absorbed.given_name,
        )
        fields_added["name_updated"] = True

    # Deactivate absorbed contact
    db.deactivate_contact(absorbed_id, survivor_id)

    # Record merge
    merge_id = db.insert_merge(survivor_id, absorbed_id, merge_type, confidence, fields_added)

    # Reassign pending similarity pairs
    db.reassign_pairs(absorbed_id, survivor_id)

    db.commit()
    return merge_id


def undo_merge(db: Database, merge_id: int) -> bool:
    """Undo a merge by reactivating the absorbed contact.

    Note: fields that were added to survivor are NOT removed (would require
    tracking exact field IDs). The absorbed contact is simply reactivated.
    """
    merge = db.get_merge(merge_id)
    if not merge:
        return False

    db.reactivate_contact(merge["absorbed_id"])
    db.delete_merge(merge_id)
    db.commit()
    return True


def auto_resolve(db: Database, aggressiveness: float = 0.5,
                 progress_callback=None) -> int:
    """Auto-merge pairs above the confidence threshold.

    Returns number of merges performed.
    """
    threshold = aggressiveness_to_threshold(aggressiveness)
    pairs = db.get_pending_pairs(min_confidence=threshold)
    total = len(pairs)
    merged = 0

    for i, pair in enumerate(pairs):
        # Re-check both contacts are still active
        a = db.get_contact(pair["contact_a_id"])
        b = db.get_contact(pair["contact_b_id"])
        if not a or not b or not a.is_active or not b.is_active:
            db.update_pair_resolution(pair["id"], "skipped")
            continue

        survivor_id, absorbed_id = determine_survivor(
            db, pair["contact_a_id"], pair["contact_b_id"]
        )

        merge_contacts(
            db, survivor_id, absorbed_id,
            merge_type="auto",
            confidence=pair["confidence"],
        )
        db.update_pair_resolution(pair["id"], "auto_merged")
        merged += 1

        if progress_callback and (i % 10 == 0 or i == total - 1):
            progress_callback(i + 1, total, merged)

    return merged
