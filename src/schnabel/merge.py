"""Merge engine with aggressiveness parameter and undo support."""

import json

from schnabel.db import Database
from schnabel.model import Contact, ContactField
from schnabel.normalize import normalize_phone


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
                   merge_type: str = "auto", confidence: float = 0.0,
                   skip_pair_reassign: bool = False) -> int:
    """Merge absorbed contact into survivor. Returns merge_history ID.

    Set skip_pair_reassign=True during batch auto-merge for performance.
    Pairs involving deactivated contacts are filtered by is_active checks.
    """
    survivor = db.get_contact(survivor_id)
    absorbed = db.get_contact(absorbed_id)
    if not survivor or not absorbed:
        raise ValueError(f"Contact not found: {survivor_id} or {absorbed_id}")

    fields_added = {
        "emails": [], "phones": [], "fields": [],
        "photos": [],  # list of byte_hashes
        "name_updated": False,
        "original_name": None,  # (fn, family_name, given_name) before name adoption
    }

    # Union emails
    existing_emails = {e.lower() for e in survivor.emails}
    for f in absorbed.fields:
        if f.field_type == "email" and f.field_value.lower() not in existing_emails:
            db.add_contact_field(survivor_id, f)
            fields_added["emails"].append(f.field_value)

    # Union phones (compare via E.164 normalization to avoid duplicates)
    existing_phones_e164 = set()
    existing_phones_raw = set()
    for p in survivor.phones:
        existing_phones_raw.add(p)
        e164 = normalize_phone(p)
        if e164:
            existing_phones_e164.add(e164)

    for f in absorbed.fields:
        if f.field_type == "tel":
            if f.field_value in existing_phones_raw:
                continue
            e164 = normalize_phone(f.field_value)
            if e164 and e164 in existing_phones_e164:
                continue
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

    # Photos: add missing ones by byte_hash (photos without hash are always copied)
    existing_hashes = {p.byte_hash for p in survivor.photos if p.byte_hash}
    for p in absorbed.photos:
        if p.byte_hash and p.byte_hash in existing_hashes:
            continue  # duplicate photo, skip
        db.add_photo(survivor_id, p)
        fields_added["photos"].append(p.byte_hash or "")

    # Name: prefer the more complete structured name
    if not survivor.has_structured_name and absorbed.has_structured_name:
        fields_added["original_name"] = [survivor.fn, survivor.family_name, survivor.given_name]
        db.update_contact_name(
            survivor_id, absorbed.fn or survivor.fn,
            absorbed.family_name, absorbed.given_name,
        )
        fields_added["name_updated"] = True

    # Deactivate absorbed contact
    db.deactivate_contact(absorbed_id, survivor_id)

    # Record merge
    merge_id = db.insert_merge(survivor_id, absorbed_id, merge_type, confidence, fields_added)

    # Reassign pending similarity pairs (skip during batch for performance)
    if not skip_pair_reassign:
        db.reassign_pairs(absorbed_id, survivor_id)

    db.commit()
    return merge_id


def undo_merge(db: Database, merge_id: int) -> bool:
    """Undo a merge: reactivate absorbed contact AND remove fields added to survivor."""
    merge = db.get_merge(merge_id)
    if not merge:
        return False

    survivor_id = merge["survivor_id"]
    fields_added = json.loads(merge["fields_added"]) if isinstance(merge["fields_added"], str) else merge["fields_added"]

    # Remove emails that were added during merge
    for email in fields_added.get("emails", []):
        db.remove_contact_field_by_value(survivor_id, "email", email)

    # Remove phones that were added during merge
    for phone in fields_added.get("phones", []):
        db.remove_contact_field_by_value(survivor_id, "tel", phone)

    # Remove other fields that were added during merge
    for field_str in fields_added.get("fields", []):
        if "=" in field_str:
            ftype, fvalue = field_str.split("=", 1)
            db.remove_contact_field_by_value(survivor_id, ftype, fvalue)

    # Remove photos that were added (by byte_hash)
    photo_hashes = fields_added.get("photos", [])
    if isinstance(photo_hashes, int):
        # Legacy format (just a count) — can't undo
        pass
    elif photo_hashes:
        for bhash in photo_hashes:
            if bhash:
                db.remove_photo_by_hash(survivor_id, bhash)
            else:
                # Photo without hash — remove the most recently added photo
                db.remove_latest_photo(survivor_id)

    # Revert name if it was updated
    if fields_added.get("name_updated"):
        original = fields_added.get("original_name")
        if original and len(original) == 3:
            db.update_contact_name(survivor_id, original[0], original[1], original[2])

    db.reactivate_contact(merge["absorbed_id"])
    db.delete_merge(merge_id)
    db.commit()
    return True


def auto_resolve(db: Database, aggressiveness: float = 0.5,
                 progress_callback=None) -> int:
    """Auto-merge pairs above the confidence threshold.

    Returns number of merges performed.
    Skips pair reassignment during batch merge for performance — inactive
    contacts are filtered by is_active checks in get_pending_pairs.
    """
    threshold = aggressiveness_to_threshold(aggressiveness)
    pairs = db.get_pending_pairs(min_confidence=threshold)
    total = len(pairs)
    merged = 0

    # Map absorbed contact ID → its survivor (for chain resolution)
    absorbed_to_survivor: dict[int, int] = {}

    for i, pair in enumerate(pairs):
        a_id = pair["contact_a_id"]
        b_id = pair["contact_b_id"]

        # Resolve chain: if a contact was absorbed, follow to its survivor
        while a_id in absorbed_to_survivor:
            a_id = absorbed_to_survivor[a_id]
        while b_id in absorbed_to_survivor:
            b_id = absorbed_to_survivor[b_id]

        # Skip self-pairs (both resolved to the same survivor)
        if a_id == b_id:
            db.update_pair_resolution(pair["id"], "skipped")
            continue

        survivor_id, absorbed_id = determine_survivor(db, a_id, b_id)

        merge_contacts(
            db, survivor_id, absorbed_id,
            merge_type="auto",
            confidence=pair["confidence"],
            skip_pair_reassign=True,
        )
        absorbed_to_survivor[absorbed_id] = survivor_id
        db.update_pair_resolution(pair["id"], "auto_merged")
        merged += 1

        # Batch commit every 100 merges
        if merged % 100 == 0:
            db.commit()

        if progress_callback and (i % 100 == 0 or i == total - 1):
            progress_callback(i + 1, total, merged)

    db.commit()
    return merged
