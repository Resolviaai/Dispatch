"""Migration hygiene guard: every table created in the public schema must be
accompanied by explicit REVOKE ALLs for anon/authenticated (and TRUNCATE must
never remain granted), because Postgres grants default privileges and TRUNCATE
bypasses RLS. Also enforces least-privilege table grants and safe
SECURITY DEFINER search_path / EXECUTE revokes on Wave 1+ migrations."""

import re
import unittest
from pathlib import Path

MIGRATIONS = sorted(Path("supabase/migrations").glob("*.sql"))

# Historical pre-Wave-1 migrations whose missing TRUNCATE/authenticated revokes
# are forward-remediated in 20261105000031_wave1_repair.sql (forward-only rule).
REMEDIATED_IN_0031 = {
    "20261102000026_project_document_and_revisions.sql": "clip_revisions",
    "20261102000027_publishing_queue.sql": "publishing_jobs",
}


def _strip_sql_comments(sql: str) -> str:
    """Remove -- and /* */ comments while preserving quoted strings and $$ blocks."""
    out: list[str] = []
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        # Dollar-quoted string ($tag$...$tag$)
        if ch == "$":
            m = re.match(r"\$[A-Za-z0-9_]*\$", sql[i:])
            if m:
                tag = m.group(0)
                end = sql.find(tag, i + len(tag))
                if end == -1:
                    out.append(sql[i:])
                    break
                out.append(sql[i : end + len(tag)])
                i = end + len(tag)
                continue
        # Single-quoted string
        if ch == "'":
            j = i + 1
            while j < n:
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        j += 2
                        continue
                    j += 1
                    break
                j += 1
            out.append(sql[i:j])
            i = j
            continue
        # Line comment
        if ch == "-" and i + 1 < n and sql[i + 1] == "-":
            end = sql.find("\n", i)
            if end == -1:
                break
            out.append("\n")
            i = end + 1
            continue
        # Block comment
        if ch == "/" and i + 1 < n and sql[i + 1] == "*":
            end = sql.find("*/", i + 2)
            if end == -1:
                break
            i = end + 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _migration_num(path: Path) -> int:
    m = re.match(r"^\d{8}(\d{6})_", path.name)
    return int(m.group(1)) if m else 0


def _has_full_client_revoke(sql_text: str, table: str, require_public: bool = False) -> bool:
    """Return True if sql_text contains a REVOKE ALL covering anon and authenticated."""
    per_table = re.finditer(
        r"revoke\s+all\s+(?:privileges\s+)?on\s+(?:table\s+)?public\."
        + re.escape(table)
        + r"\s+from\s+([^;]+);",
        sql_text,
        re.I,
    )
    schema_wide = re.finditer(
        r"revoke\s+all\s+(?:privileges\s+)?on\s+all\s+tables\s+in\s+schema\s+public\s+from\s+([^;]+);",
        sql_text,
        re.I,
    )
    for m in list(per_table) + list(schema_wide):
        roles_l = m.group(1).lower()
        has_anon = re.search(r"\banon\b", roles_l) is not None
        has_auth = re.search(r"\bauthenticated\b", roles_l) is not None
        has_pub = re.search(r"\bpublic\b", roles_l) is not None
        if has_anon and has_auth and (not require_public or has_pub):
            return True
    return False


class TestMigrationGrantHygiene(unittest.TestCase):
    def test_migrations_exist(self):
        self.assertGreaterEqual(len(MIGRATIONS), 8)
        self.assertTrue(
            any(p.name == "20261105000031_wave1_repair.sql" for p in MIGRATIONS),
            "Expected 20261105000031_wave1_repair.sql to exist",
        )

    def test_every_created_table_has_client_revokes_without_prior_masking(self):
        problems = []
        for idx, path in enumerate(MIGRATIONS):
            raw_sql = path.read_text(encoding="utf-8")
            sql = _strip_sql_comments(raw_sql)
            num = _migration_num(path)

            # Only consider THIS migration and LATER migrations (never earlier ones like 0001).
            later = "\n".join(
                _strip_sql_comments(p.read_text(encoding="utf-8")) for p in MIGRATIONS[idx:]
            )

            for m in re.finditer(
                r"create\s+table\s+(?:if\s+not\s+exists\s+)?public\.(\w+)", sql, re.I
            ):
                table = m.group(1)
                same_file_tail = sql[m.end() :]
                later_from_create = same_file_tail + "\n" + "\n".join(
                    _strip_sql_comments(p.read_text(encoding="utf-8"))
                    for p in MIGRATIONS[idx + 1 :]
                )

                if not _has_full_client_revoke(later_from_create, table):
                    problems.append(
                        f"{path.name}: public.{table} lacks REVOKE ALL ... FROM anon, authenticated in this or later migration"
                    )
                    continue

                # Every migration >= 0009 (except historical 0026/0027 remediated in 0031)
                # must carry its full client REVOKE ALL in the SAME file as CREATE TABLE.
                if num >= 9 and path.name not in REMEDIATED_IN_0031:
                    require_pub = num >= 31
                    if not _has_full_client_revoke(same_file_tail, table, require_public=require_pub):
                        problems.append(
                            f"{path.name}: public.{table} (>= 0009) must revoke ALL from anon, authenticated in the same migration"
                        )

                # Migrations >= 0031 must also explicitly prevent TRUNCATE via REVOKE ALL/TRUNCATE.
                if num >= 31:
                    has_truncate_revoke = re.search(
                        r"revoke\s+(?:all|truncate)\s+on\s+(?:table\s+)?public\."
                        + re.escape(table)
                        + r"\s+from\s+[^;]*\banon\b",
                        same_file_tail,
                        re.I,
                    )
                    if not has_truncate_revoke:
                        problems.append(
                            f"{path.name}: public.{table} (>= 0031) missing explicit TRUNCATE/ALL revoke"
                        )

        self.assertEqual(
            problems, [], "Tables missing explicit client revokes: " + "; ".join(problems)
        )

    def test_0031_remediates_0026_and_0027_truncate_and_authenticated_leaks(self):
        repair_path = Path("supabase/migrations/20261105000031_wave1_repair.sql")
        repair_sql = _strip_sql_comments(repair_path.read_text(encoding="utf-8"))
        for tbl in ("clip_revisions", "publishing_jobs", "publishing_job_targets"):
            self.assertTrue(
                _has_full_client_revoke(repair_sql, tbl, require_public=True),
                f"{repair_path.name} must REVOKE ALL on public.{tbl} from public, anon, authenticated",
            )
            self.assertRegex(
                repair_sql,
                re.compile(
                    r"revoke\s+truncate\s+on\s+(?:table\s+)?public\."
                    + re.escape(tbl)
                    + r"\s+from\s+[^;]*\bpublic\b[^;]*\banon\b[^;]*\bauthenticated\b",
                    re.I,
                ),
                f"{repair_path.name} must explicitly REVOKE TRUNCATE on public.{tbl}",
            )

    def test_no_table_grants_writes_to_anon_or_authenticated(self):
        grant_re = re.compile(
            r"grant\s+(.+?)\s+on\s+(?:(all\s+tables\s+in\s+schema\s+public)|(?:table\s+)?([^;]+?))\s+to\s+([^;]+);",
            re.I | re.S,
        )
        for path in MIGRATIONS:
            sql = _strip_sql_comments(path.read_text(encoding="utf-8"))
            num = _migration_num(path)
            for m in grant_re.finditer(sql):
                privs_raw, all_tables, target_raw, roles_raw = m.groups()
                privs_norm = " ".join(privs_raw.strip().lower().split())
                # Skip routine/function/schema/sequence grants
                if target_raw and re.match(r"^(?:function|procedure|routine|schema|sequence|type)\b", target_raw.strip(), re.I):
                    continue
                if privs_norm.startswith("execute") or privs_norm.startswith("usage"):
                    continue

                roles_l = roles_raw.lower()
                targets_client = bool(
                    re.search(r"\b(?:anon|authenticated|public)\b", roles_l)
                )
                if not targets_client:
                    continue

                if all_tables:
                    self.fail(f"{path.name}: broad table grant '{privs_raw.strip()}' on ALL TABLES to {roles_raw.strip()}")

                # Allowed: SELECT (plain or column-scoped), plus the single 0001 profiles.display_name UPDATE.
                is_select_only = privs_norm == "select" or re.match(r"^select\s*\([^)]+\)$", privs_norm) is not None
                is_profile_display_name_update = (
                    path.name == "20261023000001_init.sql"
                    and target_raw.strip().lower() == "public.profiles"
                    and privs_norm == "update (display_name)"
                    and roles_l.strip() == "authenticated"
                )
                if not (is_select_only or is_profile_display_name_update):
                    self.fail(
                        f"{path.name}: grants '{privs_raw.strip()}' on {target_raw.strip()} to {roles_raw.strip()}"
                    )

                # Guard against accidentally re-exposing connected_accounts.credentials_encrypted after 0018.
                if num >= 19 and target_raw and "connected_accounts" in target_raw.lower() and privs_norm == "select":
                    self.fail(
                        f"{path.name}: full SELECT grant on connected_accounts would re-expose credentials_encrypted"
                    )

    def test_wave1_security_definer_functions_use_safe_search_path_and_revokes(self):
        # Verify every function defined in >= 0031 sets pg_catalog first (or '') and
        # revokes EXECUTE from public, anon, authenticated.
        fn_re = re.compile(
            r"create\s+(?:or\s+replace\s+)?function\s+(public\.\w+)\s*\((.*?)\)\s*returns\s+.*?\bas\s+\$([A-Za-z0-9_]*)\$.*?\$\3\$\s*;",
            re.I | re.S,
        )
        for path in MIGRATIONS:
            if _migration_num(path) < 31:
                continue
            sql = _strip_sql_comments(path.read_text(encoding="utf-8"))
            for m in fn_re.finditer(sql):
                header = m.group(0)[: m.group(0).find("$$") if "$$" in m.group(0) else len(m.group(0))]
                fn_name = m.group(1)
                if "security definer" in header.lower():
                    self.assertRegex(
                        header,
                        re.compile(r"set\s+search_path\s*=\s*(?:pg_catalog\s*,\s*public|'')", re.I),
                        f"{path.name}: {fn_name} SECURITY DEFINER must set search_path = pg_catalog, public (or '')",
                    )
                # Every function in public created in >= 0031 must be revoked from public, anon, authenticated
                rev_re = re.compile(
                    r"revoke\s+all\s+on\s+function\s+"
                    + re.escape(fn_name)
                    + r"\s*\([^)]*\)\s+from\s+([^;]+);",
                    re.I,
                )
                rev = rev_re.search(sql[m.end() :])
                self.assertIsNotNone(
                    rev, f"{path.name}: {fn_name} missing REVOKE ALL ON FUNCTION ... FROM public, anon, authenticated"
                )
                roles_l = rev.group(1).lower()
                for role in ("public", "anon", "authenticated"):
                    self.assertRegex(
                        roles_l,
                        rf"\b{role}\b",
                        f"{path.name}: {fn_name} REVOKE must include {role}",
                    )


if __name__ == "__main__":
    unittest.main()
