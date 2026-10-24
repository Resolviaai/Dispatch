"""Migration hygiene guard (audit 2026-10-24): every table created in the public
schema must be accompanied by explicit REVOKEs for anon/authenticated (or an
explicit grant-only-select), because Postgres grants ALL to PUBLIC by default on
new tables and TRUNCATE bypasses RLS. Catches the 0004/0006/0007 defect class at
authoring time instead of in a live audit."""

import re
import unittest
from pathlib import Path

MIGRATIONS = sorted(Path("supabase/migrations").glob("*.sql"))


class TestMigrationGrantHygiene(unittest.TestCase):
    def test_migrations_exist(self):
        self.assertGreaterEqual(len(MIGRATIONS), 8)

    def test_every_created_table_has_anonymous_revokes(self):
        problems = []
        for path in MIGRATIONS:
            sql = path.read_text(encoding="utf-8")
            created = re.findall(
                r"create\s+table\s+(?:if\s+not\s+exists\s+)?public\.(\w+)", sql, re.I)
            for table in created:
                # each table must appear in a revoke targeting anon/authenticated
                # somewhere in the migration set (this or any later file)
                later = "\n".join(p.read_text(encoding="utf-8") for p in MIGRATIONS)
                per_table = re.compile(
                    r"revoke\s+all\s+on\s+public\." + re.escape(table) +
                    r"\s+from\s+[^;]*anon", re.I)
                schema_wide = re.compile(
                    r"revoke\s+all\s+on\s+all\s+tables\s+in\s+schema\s+public\s+from\s+[^;]*anon", re.I)
                if not (per_table.search(later) or schema_wide.search(later)):
                    problems.append(f"{path.name}: public.{table} has no REVOKE ... from anon")
        self.assertEqual(problems, [], "Tables missing explicit client revokes: " + "; ".join(problems))

    def test_no_table_grants_writes_to_anon_or_authenticated(self):
        for path in MIGRATIONS:
            sql = path.read_text(encoding="utf-8")
            for m in re.finditer(
                r"grant\s+([\w,\s]+?)\s+on\s+(?:table\s+)?public\.(\w+)\s+to\s+([^;]+)", sql, re.I):
                privs, table, roles = m.groups()
                roles_l = roles.lower()
                if ("anon" in roles_l or "authenticated" in roles_l) and "select" != privs.strip().lower():
                    self.fail(f"{path.name}: grants '{privs.strip()}' on public.{table} to {roles.strip()}")


if __name__ == "__main__":
    unittest.main()
