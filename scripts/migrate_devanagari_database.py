#!/usr/bin/env python3
"""Database Migration: Transliterate all Devanagari characters in storage/database/dispatch.db.

Exhaustively transliterates:
- clips: title, hook, description, caption_data (words and groups/words), segments_json, project_document
- transcripts: full_text, segments_json
- rejections, youtube_inbox, clip_revisions, and any other table containing Devanagari text.
"""
import sqlite3
import shutil
import json
import logging
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from dispatch.ai_clips.highlight_finder import (
    phonetic_devanagari_to_roman,
    has_devanagari,
    DEVANAGARI_REGEX
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("devanagari_migration")

DB_PATH = Path("storage/database/dispatch.db")
BACKUP_PATH = Path("storage/database/dispatch.db.bak")


def clean_text(text: str) -> str:
    if not text or not has_devanagari(text):
        return text
    return phonetic_devanagari_to_roman(text)


def clean_data_structure(obj):
    if isinstance(obj, str):
        return clean_text(obj)
    elif isinstance(obj, dict):
        return {k: clean_data_structure(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [clean_data_structure(item) for item in obj]
    return obj


def clean_json_field(val: str) -> str:
    if not val or not has_devanagari(val):
        return val
    try:
        parsed = json.loads(val)
        cleaned = clean_data_structure(parsed)
        return json.dumps(cleaned, ensure_ascii=False)
    except Exception:
        return clean_text(val)


def migrate_database():
    if not DB_PATH.exists():
        logger.error("Database not found at %s", DB_PATH)
        return

    # Backup database
    shutil.copy2(DB_PATH, BACKUP_PATH)
    logger.info("Created backup at %s", BACKUP_PATH)

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # 1. Migrate clips table
    logger.info("Migrating 'clips' table...")
    cur.execute("PRAGMA table_info(clips)")
    clip_cols = [c[1] for c in cur.fetchall()]
    cur.execute("SELECT * FROM clips")
    clip_rows = cur.fetchall()

    for row in clip_rows:
        row_dict = dict(zip(clip_cols, row))
        cid = row_dict["id"]
        updates = {}

        # title, hook, description
        for field in ("title", "hook", "description"):
            if field in row_dict and row_dict[field] and has_devanagari(row_dict[field]):
                updates[field] = clean_text(row_dict[field])

        # caption_data
        if "caption_data" in row_dict and row_dict["caption_data"] and has_devanagari(row_dict["caption_data"]):
            updates["caption_data"] = clean_json_field(row_dict["caption_data"])

        # segments_json
        if "segments_json" in row_dict and row_dict["segments_json"] and has_devanagari(row_dict["segments_json"]):
            updates["segments_json"] = clean_json_field(row_dict["segments_json"])

        # project_document
        if "project_document" in row_dict and row_dict["project_document"] and has_devanagari(row_dict["project_document"]):
            updates["project_document"] = clean_json_field(row_dict["project_document"])

        # Any other string column in clips
        for cname in clip_cols:
            if cname not in updates and cname not in ("title", "hook", "description", "caption_data", "segments_json", "project_document"):
                val = row_dict[cname]
                if isinstance(val, str) and has_devanagari(val):
                    updates[cname] = clean_json_field(val)

        if updates:
            set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
            values = list(updates.values()) + [cid]
            cur.execute(f"UPDATE clips SET {set_clause} WHERE id = ?", values)
            logger.info("Updated clip %s fields: %s", cid, list(updates.keys()))

    # 2. Migrate transcripts table
    logger.info("Migrating 'transcripts' table...")
    cur.execute("PRAGMA table_info(transcripts)")
    tx_cols = [c[1] for c in cur.fetchall()]
    cur.execute("SELECT * FROM transcripts")
    tx_rows = cur.fetchall()

    for row in tx_rows:
        row_dict = dict(zip(tx_cols, row))
        tid = row_dict["id"]
        updates = {}

        if "full_text" in row_dict and row_dict["full_text"] and has_devanagari(row_dict["full_text"]):
            updates["full_text"] = clean_text(row_dict["full_text"])

        if "segments_json" in row_dict and row_dict["segments_json"] and has_devanagari(row_dict["segments_json"]):
            updates["segments_json"] = clean_json_field(row_dict["segments_json"])

        for cname in tx_cols:
            if cname not in updates and cname not in ("full_text", "segments_json"):
                val = row_dict[cname]
                if isinstance(val, str) and has_devanagari(val):
                    updates[cname] = clean_json_field(val)

        if updates:
            set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
            values = list(updates.values()) + [tid]
            cur.execute(f"UPDATE transcripts SET {set_clause} WHERE id = ?", values)
            logger.info("Updated transcript %s fields: %s", tid, list(updates.keys()))

    # 3. Migrate any remaining tables (rejections, youtube_inbox, clip_revisions, etc.)
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    all_tables = [r[0] for r in cur.fetchall()]

    for table in all_tables:
        if table in ("clips", "transcripts"):
            continue
        cur.execute(f"PRAGMA table_info({table})")
        cols = [c[1] for c in cur.fetchall()]
        cur.execute(f"SELECT * FROM {table}")
        rows = cur.fetchall()

        for row in rows:
            row_dict = dict(zip(cols, row))
            row_pk = cols[0]
            pk_val = row_dict[row_pk]
            updates = {}

            for cname in cols:
                val = row_dict[cname]
                if isinstance(val, str) and has_devanagari(val):
                    updates[cname] = clean_json_field(val)

            if updates:
                set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
                values = list(updates.values()) + [pk_val]
                cur.execute(f"UPDATE {table} SET {set_clause} WHERE {row_pk} = ?", values)
                logger.info("Updated %s (pk=%s) fields: %s", table, pk_val, list(updates.keys()))

    conn.commit()
    conn.close()
    logger.info("Migration transaction committed successfully.")


def verify_database():
    logger.info("Verifying database has 0 Devanagari characters...")
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    all_tables = [r[0] for r in cur.fetchall()]

    violations = []
    for table in all_tables:
        cur.execute(f"PRAGMA table_info({table})")
        cols = [c[1] for c in cur.fetchall()]
        cur.execute(f"SELECT * FROM {table}")
        rows = cur.fetchall()

        for row in rows:
            pk_val = row[0]
            for col_name, val in zip(cols, row):
                if isinstance(val, str) and DEVANAGARI_REGEX.search(val):
                    matches = DEVANAGARI_REGEX.findall(val)
                    violations.append((table, col_name, pk_val, len(matches), [hex(ord(c)) for c in set(matches)]))

    conn.close()

    if violations:
        logger.error("VERIFICATION FAILED: Found %d Devanagari occurrences:", len(violations))
        for t, c, pk, count, hexes in violations:
            logger.error("  Table: %s, Column: %s, PK: %s, Count: %d, Chars: %s", t, c, pk, count, hexes)
        raise RuntimeError(f"Database contains {len(violations)} Devanagari violations!")
    else:
        logger.info("VERIFICATION SUCCESS: Exactly 0 Devanagari characters found anywhere in %s!", DB_PATH)


if __name__ == "__main__":
    migrate_database()
    verify_database()
