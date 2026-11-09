"""Verify database hygiene: ensure 0 Devanagari characters exist in storage/database/dispatch.db."""
import re
import sqlite3
import unittest
from pathlib import Path

DB_PATH = Path("storage/database/dispatch.db")
DEVANAGARI_REGEX = re.compile(r"[\u0900-\u097F]")


class TestDatabaseDevanagariHygiene(unittest.TestCase):

    def test_zero_devanagari_characters_in_database(self):
        """Verify that exactly 0 Devanagari characters exist anywhere in storage/database/dispatch.db."""
        if not DB_PATH.exists():
            self.skipTest(f"Database file not found at {DB_PATH}")

        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()

        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cur.fetchall()]

        violations = []
        for table in tables:
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

        self.assertEqual(
            len(violations),
            0,
            f"Found {len(violations)} Devanagari violations in database: {violations}"
        )


if __name__ == "__main__":
    unittest.main()
