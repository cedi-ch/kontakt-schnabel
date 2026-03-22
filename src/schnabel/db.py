"""SQLite database layer with similarity graph schema."""

import json
import sqlite3
from pathlib import Path

from schnabel.config import DEFAULT_DB_PATH
from schnabel.model import Contact, ContactField, Photo

SCHEMA = """
CREATE TABLE IF NOT EXISTS import_sources (
    id INTEGER PRIMARY KEY,
    file_path TEXT NOT NULL,
    file_hash TEXT NOT NULL,
    encoding_used TEXT,
    contact_count INTEGER DEFAULT 0,
    imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY,
    category TEXT NOT NULL DEFAULT 'unknown',
    fn TEXT NOT NULL DEFAULT '',
    family_name TEXT NOT NULL DEFAULT '',
    given_name TEXT NOT NULL DEFAULT '',
    additional_names TEXT NOT NULL DEFAULT '',
    prefix TEXT NOT NULL DEFAULT '',
    suffix TEXT NOT NULL DEFAULT '',
    uid TEXT NOT NULL DEFAULT '',
    source_import_id INTEGER REFERENCES import_sources(id),
    raw_vcard TEXT NOT NULL DEFAULT '',
    merged_into_id INTEGER REFERENCES contacts(id),
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS contact_fields (
    id INTEGER PRIMARY KEY,
    contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    field_type TEXT NOT NULL,
    field_value TEXT NOT NULL DEFAULT '',
    field_params TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS photos (
    id INTEGER PRIMARY KEY,
    contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    photo_data BLOB NOT NULL,
    photo_format TEXT NOT NULL DEFAULT 'JPEG',
    byte_hash TEXT NOT NULL DEFAULT '',
    perceptual_hash TEXT NOT NULL DEFAULT '',
    width INTEGER NOT NULL DEFAULT 0,
    height INTEGER NOT NULL DEFAULT 0,
    is_normalized INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS contact_normalized (
    id INTEGER PRIMARY KEY,
    contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    norm_type TEXT NOT NULL,
    norm_value TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS similarity_pairs (
    id INTEGER PRIMARY KEY,
    contact_a_id INTEGER NOT NULL REFERENCES contacts(id),
    contact_b_id INTEGER NOT NULL REFERENCES contacts(id),
    confidence REAL NOT NULL DEFAULT 0.0,
    email_score REAL NOT NULL DEFAULT 0.0,
    phone_score REAL NOT NULL DEFAULT 0.0,
    name_score REAL NOT NULL DEFAULT 0.0,
    bday_score REAL NOT NULL DEFAULT 0.0,
    photo_score REAL NOT NULL DEFAULT 0.0,
    address_score REAL NOT NULL DEFAULT 0.0,
    resolution TEXT NOT NULL DEFAULT 'pending'
        CHECK(resolution IN ('pending', 'auto_merged', 'manual_merged', 'not_dup', 'skipped')),
    UNIQUE(contact_a_id, contact_b_id)
);

CREATE TABLE IF NOT EXISTS merge_history (
    id INTEGER PRIMARY KEY,
    survivor_id INTEGER NOT NULL REFERENCES contacts(id),
    absorbed_id INTEGER NOT NULL REFERENCES contacts(id),
    merge_type TEXT NOT NULL DEFAULT 'auto',
    confidence REAL NOT NULL DEFAULT 0.0,
    fields_added TEXT NOT NULL DEFAULT '{}',
    merged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_contacts_active ON contacts(is_active);
CREATE INDEX IF NOT EXISTS idx_contacts_category ON contacts(category);
CREATE INDEX IF NOT EXISTS idx_contact_fields_contact ON contact_fields(contact_id);
CREATE INDEX IF NOT EXISTS idx_contact_fields_type_value ON contact_fields(field_type, field_value);
CREATE INDEX IF NOT EXISTS idx_photos_contact ON photos(contact_id);
CREATE INDEX IF NOT EXISTS idx_normalized_type_value ON contact_normalized(norm_type, norm_value);
CREATE INDEX IF NOT EXISTS idx_normalized_contact ON contact_normalized(contact_id);
CREATE INDEX IF NOT EXISTS idx_similarity_confidence ON similarity_pairs(confidence DESC);
CREATE INDEX IF NOT EXISTS idx_similarity_resolution ON similarity_pairs(resolution);
CREATE INDEX IF NOT EXISTS idx_similarity_a ON similarity_pairs(contact_a_id);
CREATE INDEX IF NOT EXISTS idx_similarity_b ON similarity_pairs(contact_b_id);

CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


class Database:
    """SQLite database for contact storage and similarity graph."""

    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = db_path
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def _init_schema(self):
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        # Migration: add uid column if missing (existing DBs)
        try:
            self.conn.execute("SELECT uid FROM contacts LIMIT 0")
        except sqlite3.OperationalError:
            self.conn.execute("ALTER TABLE contacts ADD COLUMN uid TEXT NOT NULL DEFAULT ''")
            self.conn.commit()
        # Migration: add bday_score column if missing (existing DBs)
        try:
            self.conn.execute("SELECT bday_score FROM similarity_pairs LIMIT 0")
        except sqlite3.OperationalError:
            self.conn.execute("ALTER TABLE similarity_pairs ADD COLUMN bday_score REAL NOT NULL DEFAULT 0.0")
            self.conn.commit()

    def close(self):
        self.conn.close()

    def create_backup(self, suffix: str = "") -> Path:
        """Create a backup of the database file using SQLite's backup API.

        Returns the path to the backup file.
        """
        backup_name = f"{self.db_path.stem}_backup{('_' + suffix) if suffix else ''}{self.db_path.suffix}"
        backup_path = self.db_path.parent / backup_name
        backup_conn = sqlite3.connect(str(backup_path))
        self.conn.backup(backup_conn)
        backup_conn.close()
        return backup_path

    # -- Import sources --

    def add_import_source(self, file_path: str, file_hash: str, encoding_used: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO import_sources (file_path, file_hash, encoding_used) VALUES (?, ?, ?)",
            (file_path, file_hash, encoding_used),
        )
        self.conn.commit()
        return cur.lastrowid

    def update_import_count(self, import_id: int, count: int):
        self.conn.execute(
            "UPDATE import_sources SET contact_count = ? WHERE id = ?",
            (count, import_id),
        )
        self.conn.commit()

    def get_imported_hashes(self) -> set[str]:
        rows = self.conn.execute("SELECT file_hash FROM import_sources").fetchall()
        return {row["file_hash"] for row in rows}

    def get_import_sources(self) -> list[dict]:
        """Get all import sources ordered by import time."""
        rows = self.conn.execute(
            "SELECT * FROM import_sources ORDER BY id"
        ).fetchall()
        return [dict(row) for row in rows]

    # -- Contacts --

    def insert_contact(self, contact: Contact) -> int:
        cur = self.conn.execute(
            """INSERT INTO contacts
               (category, fn, family_name, given_name, additional_names,
                prefix, suffix, uid, source_import_id, raw_vcard, is_active)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                contact.category,
                contact.fn,
                contact.family_name,
                contact.given_name,
                contact.additional_names,
                contact.prefix,
                contact.suffix,
                contact.uid,
                contact.source_import_id,
                contact.raw_vcard,
                1 if contact.is_active else 0,
            ),
        )
        contact_id = cur.lastrowid

        for f in contact.fields:
            self.conn.execute(
                "INSERT INTO contact_fields (contact_id, field_type, field_value, field_params) "
                "VALUES (?, ?, ?, ?)",
                (contact_id, f.field_type, f.field_value, json.dumps(f.field_params)),
            )

        for p in contact.photos:
            self.conn.execute(
                """INSERT INTO photos
                   (contact_id, photo_data, photo_format, byte_hash, perceptual_hash, width, height)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (contact_id, p.photo_data, p.photo_format, p.byte_hash, p.perceptual_hash,
                 p.width, p.height),
            )

        return contact_id

    def commit(self):
        self.conn.commit()

    def get_contact(self, contact_id: int) -> Contact | None:
        row = self.conn.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone()
        if not row:
            return None
        contact = Contact(
            id=row["id"],
            category=row["category"],
            fn=row["fn"],
            family_name=row["family_name"],
            given_name=row["given_name"],
            additional_names=row["additional_names"],
            prefix=row["prefix"],
            suffix=row["suffix"],
            uid=row["uid"],
            source_import_id=row["source_import_id"],
            raw_vcard=row["raw_vcard"],
            merged_into_id=row["merged_into_id"],
            is_active=bool(row["is_active"]),
        )

        fields = self.conn.execute(
            "SELECT * FROM contact_fields WHERE contact_id = ?", (contact_id,)
        ).fetchall()
        for f in fields:
            contact.fields.append(ContactField(
                field_type=f["field_type"],
                field_value=f["field_value"],
                field_params=json.loads(f["field_params"]),
                id=f["id"],
            ))

        photos = self.conn.execute(
            "SELECT * FROM photos WHERE contact_id = ?", (contact_id,)
        ).fetchall()
        for p in photos:
            contact.photos.append(Photo(
                photo_data=p["photo_data"],
                photo_format=p["photo_format"],
                byte_hash=p["byte_hash"],
                perceptual_hash=p["perceptual_hash"],
                width=p["width"],
                height=p["height"],
            ))

        return contact

    def get_all_active_contacts(self) -> list[Contact]:
        rows = self.conn.execute(
            "SELECT id FROM contacts WHERE is_active = 1"
        ).fetchall()
        contacts = []
        for row in rows:
            c = self.get_contact(row["id"])
            if c:
                contacts.append(c)
        return contacts

    def get_active_contact_ids(self) -> list[int]:
        rows = self.conn.execute(
            "SELECT id FROM contacts WHERE is_active = 1 ORDER BY id"
        ).fetchall()
        return [row["id"] for row in rows]

    def update_contact_category(self, contact_id: int, category: str):
        self.conn.execute(
            "UPDATE contacts SET category = ? WHERE id = ?", (category, contact_id)
        )

    def deactivate_contact(self, contact_id: int, merged_into_id: int):
        self.conn.execute(
            "UPDATE contacts SET is_active = 0, merged_into_id = ? WHERE id = ?",
            (merged_into_id, contact_id),
        )

    def reactivate_contact(self, contact_id: int):
        self.conn.execute(
            "UPDATE contacts SET is_active = 1, merged_into_id = NULL WHERE id = ?",
            (contact_id,),
        )

    def delete_contact(self, contact_id: int):
        self.conn.execute(
            "UPDATE contacts SET category = 'deleted', is_active = 0 WHERE id = ?",
            (contact_id,),
        )

    # -- Contact fields (for merge) --

    def add_contact_field(self, contact_id: int, field: ContactField):
        self.conn.execute(
            "INSERT INTO contact_fields (contact_id, field_type, field_value, field_params) "
            "VALUES (?, ?, ?, ?)",
            (contact_id, field.field_type, field.field_value, json.dumps(field.field_params)),
        )

    def add_photo(self, contact_id: int, photo: Photo):
        self.conn.execute(
            """INSERT INTO photos
               (contact_id, photo_data, photo_format, byte_hash, perceptual_hash, width, height)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (contact_id, photo.photo_data, photo.photo_format, photo.byte_hash,
             photo.perceptual_hash, photo.width, photo.height),
        )

    def remove_photo_by_hash(self, contact_id: int, byte_hash: str):
        """Remove a photo from a contact by byte_hash (for merge undo)."""
        row = self.conn.execute(
            "SELECT id FROM photos WHERE contact_id = ? AND byte_hash = ? LIMIT 1",
            (contact_id, byte_hash),
        ).fetchone()
        if row:
            self.conn.execute("DELETE FROM photos WHERE id = ?", (row["id"],))

    def remove_latest_photo(self, contact_id: int):
        """Remove the most recently added photo (highest id) for a contact."""
        row = self.conn.execute(
            "SELECT id FROM photos WHERE contact_id = ? ORDER BY id DESC LIMIT 1",
            (contact_id,),
        ).fetchone()
        if row:
            self.conn.execute("DELETE FROM photos WHERE id = ?", (row["id"],))

    def update_contact_name(self, contact_id: int, fn: str, family_name: str, given_name: str):
        self.conn.execute(
            "UPDATE contacts SET fn = ?, family_name = ?, given_name = ? WHERE id = ?",
            (fn, family_name, given_name, contact_id),
        )

    def update_contact_field(self, field_id: int, field_value: str):
        self.conn.execute(
            "UPDATE contact_fields SET field_value = ? WHERE id = ?",
            (field_value, field_id),
        )

    def update_contact_field_params(self, field_id: int, field_params: dict):
        self.conn.execute(
            "UPDATE contact_fields SET field_params = ? WHERE id = ?",
            (json.dumps(field_params), field_id),
        )

    def delete_contact_field(self, field_id: int):
        self.conn.execute(
            "DELETE FROM contact_fields WHERE id = ?",
            (field_id,),
        )

    def remove_contact_field_by_value(self, contact_id: int, field_type: str, field_value: str):
        """Remove a specific field by contact_id, type, and value (for merge undo)."""
        row = self.conn.execute(
            "SELECT id FROM contact_fields WHERE contact_id = ? AND field_type = ? AND field_value = ? LIMIT 1",
            (contact_id, field_type, field_value),
        ).fetchone()
        if row:
            self.conn.execute("DELETE FROM contact_fields WHERE id = ?", (row["id"],))

    # -- Normalized values --

    def insert_normalized(self, contact_id: int, norm_type: str, norm_value: str):
        self.conn.execute(
            "INSERT INTO contact_normalized (contact_id, norm_type, norm_value) VALUES (?, ?, ?)",
            (contact_id, norm_type, norm_value),
        )

    def clear_normalized(self):
        self.conn.execute("DELETE FROM contact_normalized")
        self.conn.commit()

    def get_normalized_groups(self, norm_type: str) -> dict[str, list[int]]:
        """Get groups of contact IDs sharing the same normalized value."""
        rows = self.conn.execute(
            """SELECT norm_value, contact_id FROM contact_normalized cn
               JOIN contacts c ON cn.contact_id = c.id
               WHERE cn.norm_type = ? AND c.is_active = 1
               ORDER BY norm_value""",
            (norm_type,),
        ).fetchall()
        groups: dict[str, list[int]] = {}
        for row in rows:
            groups.setdefault(row["norm_value"], []).append(row["contact_id"])
        return groups

    # -- Similarity pairs --

    def insert_similarity_pair(
        self, a_id: int, b_id: int, confidence: float,
        email_score: float, phone_score: float, name_score: float,
        photo_score: float, address_score: float,
        bday_score: float = 0.0,
    ):
        lo, hi = min(a_id, b_id), max(a_id, b_id)
        self.conn.execute(
            """INSERT OR REPLACE INTO similarity_pairs
               (contact_a_id, contact_b_id, confidence,
                email_score, phone_score, name_score, bday_score,
                photo_score, address_score)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (lo, hi, confidence, email_score, phone_score, name_score,
             bday_score, photo_score, address_score),
        )

    def clear_similarity_pairs(self):
        self.conn.execute("DELETE FROM similarity_pairs")
        self.conn.commit()

    def get_pending_pairs(self, min_confidence: float = 0.0) -> list[dict]:
        rows = self.conn.execute(
            """SELECT sp.*, ca.fn as fn_a, cb.fn as fn_b
               FROM similarity_pairs sp
               JOIN contacts ca ON sp.contact_a_id = ca.id
               JOIN contacts cb ON sp.contact_b_id = cb.id
               WHERE sp.resolution = 'pending'
               AND sp.confidence >= ?
               AND ca.is_active = 1 AND cb.is_active = 1
               ORDER BY sp.confidence DESC""",
            (min_confidence,),
        ).fetchall()
        return [dict(row) for row in rows]

    def update_pair_resolution(self, pair_id: int, resolution: str):
        self.conn.execute(
            "UPDATE similarity_pairs SET resolution = ? WHERE id = ?",
            (resolution, pair_id),
        )

    def reassign_pairs(self, old_id: int, new_id: int):
        """Reassign similarity pairs from absorbed contact to survivor.

        Updates contact references so that pairs involving the absorbed contact
        now point to the survivor. Removes duplicates and self-pairs.

        Uses a safe delete-then-skip approach to avoid UNIQUE constraint
        violations when the target pair already exists.
        """
        # First: delete self-pairs that would result from reassignment
        self.conn.execute(
            """DELETE FROM similarity_pairs
               WHERE resolution = 'pending'
               AND ((contact_a_id = ? AND contact_b_id = ?)
                 OR (contact_a_id = ? AND contact_b_id = ?))""",
            (old_id, new_id, new_id, old_id),
        )

        # Delete pending pairs involving old_id where the equivalent pair
        # with new_id already exists (to avoid UNIQUE violations on UPDATE)
        self.conn.execute(
            """DELETE FROM similarity_pairs
               WHERE resolution = 'pending' AND contact_a_id = ?
               AND contact_b_id IN (
                   SELECT contact_b_id FROM similarity_pairs
                   WHERE contact_a_id = ?
               )""",
            (old_id, new_id),
        )
        self.conn.execute(
            """DELETE FROM similarity_pairs
               WHERE resolution = 'pending' AND contact_b_id = ?
               AND contact_a_id IN (
                   SELECT contact_a_id FROM similarity_pairs
                   WHERE contact_b_id = ?
               )""",
            (old_id, new_id),
        )

        # Now safely update remaining pairs
        self.conn.execute(
            """UPDATE similarity_pairs SET contact_a_id = ?
               WHERE contact_a_id = ? AND resolution = 'pending'""",
            (new_id, old_id),
        )
        self.conn.execute(
            """UPDATE similarity_pairs SET contact_b_id = ?
               WHERE contact_b_id = ? AND resolution = 'pending'""",
            (new_id, old_id),
        )

        # Remove any self-pairs that slipped through
        self.conn.execute(
            "DELETE FROM similarity_pairs WHERE contact_a_id = contact_b_id",
        )

        # Normalize: ensure contact_a_id < contact_b_id
        self.conn.execute(
            """UPDATE OR IGNORE similarity_pairs
               SET contact_a_id = contact_b_id, contact_b_id = contact_a_id
               WHERE contact_a_id > contact_b_id""",
        )
        # Clean up rows that couldn't be normalized (duplicates after swap)
        self.conn.execute(
            """DELETE FROM similarity_pairs WHERE contact_a_id > contact_b_id""",
        )

        # Final dedup — keep highest confidence per pair
        self.conn.execute(
            """DELETE FROM similarity_pairs
               WHERE id NOT IN (
                   SELECT id FROM (
                       SELECT id, ROW_NUMBER() OVER (
                           PARTITION BY contact_a_id, contact_b_id
                           ORDER BY confidence DESC, id ASC
                       ) as rn
                       FROM similarity_pairs
                   ) WHERE rn = 1
               )""",
        )

    # -- Merge history --

    def insert_merge(self, survivor_id: int, absorbed_id: int, merge_type: str,
                     confidence: float, fields_added: dict) -> int:
        cur = self.conn.execute(
            """INSERT INTO merge_history
               (survivor_id, absorbed_id, merge_type, confidence, fields_added)
               VALUES (?, ?, ?, ?, ?)""",
            (survivor_id, absorbed_id, merge_type, confidence, json.dumps(fields_added)),
        )
        return cur.lastrowid

    def get_merge(self, merge_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM merge_history WHERE id = ?", (merge_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_recent_merges(self, limit: int = 20) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM merge_history ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(row) for row in rows]

    def delete_merge(self, merge_id: int):
        self.conn.execute("DELETE FROM merge_history WHERE id = ?", (merge_id,))

    # -- Categories --

    def get_all_category_values(self) -> list[str]:
        """Get all distinct category values, sorted."""
        rows = self.conn.execute(
            """SELECT DISTINCT cf.field_value FROM contact_fields cf
               JOIN contacts c ON cf.contact_id = c.id
               WHERE cf.field_type = 'categories' AND c.is_active = 1
               ORDER BY cf.field_value COLLATE NOCASE"""
        ).fetchall()
        return [row["field_value"] for row in rows]

    def get_categorized_contact_ids(self, uncategorized_only: bool = False) -> list[int]:
        """Get active real contact IDs, optionally only those without categories."""
        if uncategorized_only:
            rows = self.conn.execute(
                """SELECT c.id FROM contacts c
                   WHERE c.is_active = 1 AND c.category = 'real'
                   AND c.id NOT IN (
                       SELECT DISTINCT contact_id FROM contact_fields
                       WHERE field_type = 'categories'
                   )
                   ORDER BY c.fn COLLATE NOCASE"""
            ).fetchall()
        else:
            rows = self.conn.execute(
                """SELECT c.id FROM contacts c
                   WHERE c.is_active = 1 AND c.category = 'real'
                   ORDER BY c.fn COLLATE NOCASE"""
            ).fetchall()
        return [row["id"] for row in rows]

    # -- Stats --

    def get_stats(self) -> dict:
        stats = {}
        row = self.conn.execute("SELECT COUNT(*) as n FROM contacts").fetchone()
        stats["total"] = row["n"]

        row = self.conn.execute(
            "SELECT COUNT(*) as n FROM contacts WHERE is_active = 1"
        ).fetchone()
        stats["active"] = row["n"]

        for cat in ("real", "stub", "spam", "unknown", "deleted"):
            row = self.conn.execute(
                "SELECT COUNT(*) as n FROM contacts WHERE category = ? AND is_active = 1",
                (cat,),
            ).fetchone()
            stats[cat] = row["n"]

        row = self.conn.execute(
            """SELECT COUNT(DISTINCT p.contact_id) as n FROM photos p
               JOIN contacts c ON p.contact_id = c.id WHERE c.is_active = 1"""
        ).fetchone()
        stats["with_photos"] = row["n"]

        row = self.conn.execute(
            """SELECT COUNT(DISTINCT cf.field_value) as n FROM contact_fields cf
               JOIN contacts c ON cf.contact_id = c.id
               WHERE cf.field_type = 'email' AND c.is_active = 1"""
        ).fetchone()
        stats["unique_emails"] = row["n"]

        row = self.conn.execute(
            """SELECT COUNT(DISTINCT cf.field_value) as n FROM contact_fields cf
               JOIN contacts c ON cf.contact_id = c.id
               WHERE cf.field_type = 'tel' AND c.is_active = 1"""
        ).fetchone()
        stats["unique_phones"] = row["n"]

        row = self.conn.execute(
            "SELECT COUNT(*) as n FROM similarity_pairs WHERE resolution = 'pending'"
        ).fetchone()
        stats["pending_pairs"] = row["n"]

        row = self.conn.execute(
            "SELECT COUNT(*) as n FROM merge_history"
        ).fetchone()
        stats["merges"] = row["n"]

        row = self.conn.execute("SELECT COUNT(*) as n FROM import_sources").fetchone()
        stats["import_sources"] = row["n"]

        row = self.conn.execute(
            """SELECT COUNT(DISTINCT cf.field_value) as n FROM contact_fields cf
               JOIN contacts c ON cf.contact_id = c.id
               WHERE cf.field_type = 'categories' AND c.is_active = 1"""
        ).fetchone()
        stats["unique_categories"] = row["n"]

        row = self.conn.execute(
            """SELECT COUNT(DISTINCT cf.contact_id) as n FROM contact_fields cf
               JOIN contacts c ON cf.contact_id = c.id
               WHERE cf.field_type = 'categories' AND c.is_active = 1"""
        ).fetchone()
        stats["contacts_with_categories"] = row["n"]

        # Field presence counts (active contacts)
        for field_type in ("bday", "adr", "org", "note", "url"):
            row = self.conn.execute(
                """SELECT COUNT(DISTINCT cf.contact_id) as n FROM contact_fields cf
                   JOIN contacts c ON cf.contact_id = c.id
                   WHERE cf.field_type = ? AND c.is_active = 1""",
                (field_type,),
            ).fetchone()
            stats[f"with_{field_type}"] = row["n"]

        # Per-category field counts
        for cat, field_type in [
            ("real", "email"), ("real", "tel"), ("real", "photo"),
            ("real", "bday"), ("real", "adr"),
            ("stub", "email"), ("stub", "tel"),
            ("spam", "email"),
        ]:
            if field_type == "photo":
                row = self.conn.execute(
                    """SELECT COUNT(DISTINCT p.contact_id) as n FROM photos p
                       JOIN contacts c ON p.contact_id = c.id
                       WHERE c.is_active = 1 AND c.category = ?""",
                    (cat,),
                ).fetchone()
            else:
                row = self.conn.execute(
                    """SELECT COUNT(DISTINCT cf.contact_id) as n FROM contact_fields cf
                       JOIN contacts c ON cf.contact_id = c.id
                       WHERE cf.field_type = ? AND c.is_active = 1 AND c.category = ?""",
                    (field_type, cat),
                ).fetchone()
            stats[f"{cat}_with_{field_type}"] = row["n"]

        return stats

    def log_pipeline_run(self, command: str, data: dict):
        """Store pipeline run data as metadata with timestamp."""
        from datetime import datetime
        data["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.set_metadata(f"run_{command}", json.dumps(data))

    def get_pipeline_runs(self) -> dict:
        """Read all run_* metadata keys, return {command: data_dict}."""
        rows = self.conn.execute(
            "SELECT key, value FROM metadata WHERE key LIKE 'run_%'"
        ).fetchall()
        runs = {}
        for row in rows:
            command = row["key"][4:]  # strip "run_"
            runs[command] = json.loads(row["value"])
        return runs

    def get_category_breakdown(self) -> dict[str, int]:
        """Count contacts per CATEGORIES value (active real contacts)."""
        rows = self.conn.execute(
            """SELECT cf.field_value, COUNT(DISTINCT cf.contact_id) as n
               FROM contact_fields cf
               JOIN contacts c ON cf.contact_id = c.id
               WHERE cf.field_type = 'categories' AND c.is_active = 1 AND c.category = 'real'
               GROUP BY cf.field_value
               ORDER BY n DESC"""
        ).fetchall()
        return {row["field_value"]: row["n"] for row in rows}

    def delete_category_from_all(self, category_value: str) -> int:
        """Delete a specific CATEGORIES field value from all contacts.

        Returns the number of fields deleted.
        """
        cur = self.conn.execute(
            """DELETE FROM contact_fields
               WHERE field_type = 'categories' AND field_value = ?""",
            (category_value,),
        )
        self.conn.commit()
        return cur.rowcount

    def search_contacts(self, query: str) -> list[Contact]:
        """Search active contacts by FN, email, phone, or org (case-insensitive)."""
        pattern = f"%{query}%"
        rows = self.conn.execute(
            """SELECT DISTINCT c.id FROM contacts c
               LEFT JOIN contact_fields cf ON cf.contact_id = c.id
               WHERE c.is_active = 1
               AND (c.fn LIKE ? COLLATE NOCASE
                    OR c.family_name LIKE ? COLLATE NOCASE
                    OR c.given_name LIKE ? COLLATE NOCASE
                    OR (cf.field_type IN ('email', 'tel', 'org', 'nickname')
                        AND cf.field_value LIKE ? COLLATE NOCASE))
               ORDER BY c.fn COLLATE NOCASE
               LIMIT 50""",
            (pattern, pattern, pattern, pattern),
        ).fetchall()
        contacts = []
        for row in rows:
            c = self.get_contact(row["id"])
            if c:
                contacts.append(c)
        return contacts

    def get_contacts_by_category(self, category: str) -> list[Contact]:
        rows = self.conn.execute(
            "SELECT id FROM contacts WHERE category = ? AND is_active = 1 ORDER BY fn",
            (category,),
        ).fetchall()
        contacts = []
        for row in rows:
            c = self.get_contact(row["id"])
            if c:
                contacts.append(c)
        return contacts

    # -- Metadata --

    def set_metadata(self, key: str, value: str):
        self.conn.execute(
            """INSERT INTO metadata (key, value, updated_at)
               VALUES (?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(key) DO UPDATE SET value = ?, updated_at = CURRENT_TIMESTAMP""",
            (key, value, value),
        )
        self.conn.commit()

    def get_metadata(self, key: str) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM metadata WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    def get_session_stats(self) -> dict:
        """Get session statistics: imports, merges by type, deleted contacts."""
        stats = {}
        row = self.conn.execute("SELECT COUNT(*) as n FROM import_sources").fetchone()
        stats["imports"] = row["n"]

        row = self.conn.execute(
            "SELECT COUNT(*) as n FROM merge_history WHERE merge_type = 'auto'"
        ).fetchone()
        stats["auto_merges"] = row["n"]

        row = self.conn.execute(
            "SELECT COUNT(*) as n FROM merge_history WHERE merge_type = 'manual'"
        ).fetchone()
        stats["manual_merges"] = row["n"]

        row = self.conn.execute(
            "SELECT COUNT(*) as n FROM contacts WHERE category = 'deleted'"
        ).fetchone()
        stats["deleted"] = row["n"]

        row = self.conn.execute(
            "SELECT COUNT(*) as n FROM similarity_pairs WHERE resolution = 'pending'"
        ).fetchone()
        stats["pending_pairs"] = row["n"]

        return stats
