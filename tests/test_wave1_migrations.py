"""Wave 1 (§9.1–§9.7) Supabase migration contract & live PostgreSQL verification tests."""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
import uuid
from pathlib import Path

from tests.test_migration_grant_hygiene import _strip_sql_comments

MIGRATIONS_DIR = Path("supabase/migrations")
MIGRATIONS = sorted(MIGRATIONS_DIR.glob("*.sql"))
REPAIR_MIGRATION = MIGRATIONS_DIR / "20261105000031_wave1_repair.sql"

STUB_SUPABASE_PREAMBLE = """
create extension if not exists pgcrypto;

do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'anon') then
    create role anon nologin;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then
    create role authenticated nologin;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'service_role') then
    create role service_role nologin bypassrls;
  end if;
end
$$;

create schema if not exists auth;
grant usage on schema public to anon, authenticated, service_role;
grant usage on schema auth to anon, authenticated, service_role;

create table if not exists auth.users (
  id uuid primary key,
  email text,
  raw_user_meta_data jsonb not null default '{}'::jsonb
);

create or replace function auth.uid() returns uuid
language sql stable as $$
  select nullif(
    coalesce(
      nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub',
      ''
    ),
    ''
  )::uuid;
$$;

grant execute on function auth.uid() to anon, authenticated, service_role;
grant anon, authenticated, service_role to postgres;
"""


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        res = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return res.returncode == 0
    except Exception:
        return False


def _find_local_pg_bin() -> Path | None:
    initdb = shutil.which("initdb")
    pg_ctl = shutil.which("pg_ctl")
    if initdb and pg_ctl:
        return Path(initdb).parent
    if os.name == "nt":
        for root in (
            Path(r"C:\Program Files\PostgreSQL"),
            Path(r"C:\Program Files (x86)\PostgreSQL"),
        ):
            if root.is_dir():
                for ver_dir in sorted(root.iterdir(), reverse=True):
                    cand = ver_dir / "bin"
                    if (cand / "initdb.exe").is_file() and (cand / "pg_ctl.exe").is_file():
                        return cand
    return None


class _EphemeralPostgresCluster:
    """Starts an isolated PostgreSQL instance (Docker postgres:16 or local initdb)."""

    def __init__(self) -> None:
        self.backend: str = "none"
        self.container_id: str | None = None
        self.temp_dir: tempfile.TemporaryDirectory[str] | None = None
        self.pg_bin: Path | None = None
        self.proc: subprocess.Popen[bytes] | None = None
        self.log_fh = None
        self.port: int = _free_port()
        self.host: str = "127.0.0.1"
        self.user: str = "postgres"
        self.password: str = "dispatch_test_pw"
        self.dbname: str = "postgres"

    def __enter__(self) -> "_EphemeralPostgresCluster":
        import psycopg2

        if _docker_available():
            self.backend = "docker-postgres-16"
            name = f"dispatch_wave1_test_{uuid.uuid4().hex[:10]}"
            cmd = [
                "docker",
                "run",
                "-d",
                "--rm",
                "--name",
                name,
                "-e",
                f"POSTGRES_PASSWORD={self.password}",
                "-p",
                f"127.0.0.1:{self.port}:5432",
                "postgres:16-alpine",
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if res.returncode != 0:
                raise RuntimeError(f"docker run postgres:16-alpine failed: {res.stderr}")
            self.container_id = res.stdout.strip()
        else:
            pg_bin = _find_local_pg_bin()
            if pg_bin is None:
                raise unittest.SkipTest(
                    "Docker daemon unavailable and local PostgreSQL binaries not installed"
                )
            self.backend = f"local-postgres ({pg_bin.parent.name})"
            self.pg_bin = pg_bin
            self.temp_dir = tempfile.TemporaryDirectory(prefix="dispatch_wave1_pg_")
            data_dir = Path(self.temp_dir.name) / "data"
            log_file = Path(self.temp_dir.name) / "postgres.log"
            ext = ".exe" if os.name == "nt" else ""
            subprocess.run(
                [
                    str(pg_bin / f"initdb{ext}"),
                    "-D",
                    str(data_dir),
                    "-U",
                    self.user,
                    "-A",
                    "trust",
                    "-E",
                    "UTF8",
                    "--locale=C",
                ],
                check=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                timeout=60,
            )
            self.log_fh = open(log_file, "wb")
            self.proc = subprocess.Popen(
                [
                    str(pg_bin / f"postgres{ext}"),
                    "-D",
                    str(data_dir),
                    "-p",
                    str(self.port),
                    "-k",
                    self.temp_dir.name,
                    "-h",
                    "127.0.0.1",
                ],
                stdin=subprocess.DEVNULL,
                stdout=self.log_fh,
                stderr=subprocess.STDOUT,
            )

        deadline = time.monotonic() + 25.0
        last_err: Exception | None = None
        while time.monotonic() < deadline:
            try:
                conn = self.connect()
                conn.close()
                return self
            except psycopg2.OperationalError as exc:
                last_err = exc
                time.sleep(0.25)
        raise RuntimeError(f"PostgreSQL failed to accept connections: {last_err}")

    def connect(self):
        import psycopg2

        kwargs = dict(
            host=self.host,
            port=self.port,
            user=self.user,
            dbname=self.dbname,
        )
        if self.container_id:
            kwargs["password"] = self.password
        return psycopg2.connect(**kwargs)

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.container_id:
            subprocess.run(
                ["docker", "rm", "-f", self.container_id],
                capture_output=True,
                timeout=15,
            )
        if self.pg_bin and self.temp_dir:
            ext = ".exe" if os.name == "nt" else ""
            data_dir = Path(self.temp_dir.name) / "data"
            subprocess.run(
                [
                    str(self.pg_bin / f"pg_ctl{ext}"),
                    "-D",
                    str(data_dir),
                    "-m",
                    "immediate",
                    "-w",
                    "stop",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
            )
            if self.proc is not None:
                try:
                    self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    self.proc.wait()
            if self.log_fh is not None:
                self.log_fh.close()
            self.temp_dir.cleanup()


class TestWave1MigrationsStatic(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.raw_sql = REPAIR_MIGRATION.read_text(encoding="utf-8")
        cls.sql = _strip_sql_comments(cls.raw_sql)

    def test_0031_migration_file_exists_and_historical_untouched_count(self):
        self.assertTrue(REPAIR_MIGRATION.is_file())
        self.assertGreaterEqual(len(MIGRATIONS), 31)
        names = [p.name for p in MIGRATIONS]
        self.assertEqual(names[-1], "20261113000032_wave2b_straggler.sql")

    def test_9_1_cloud_quota_usage_filters_expired_reservations(self):
        m = re.search(
            r"create\s+or\s+replace\s+function\s+public\.cloud_quota_usage\s*\(\s*p_user_id\s+uuid\s*\)"
            r"\s*returns\s+table\s*\(([^)]+)\)(.*?)\$\$;" ,
            self.sql,
            re.I | re.S,
        )
        self.assertIsNotNone(m, "cloud_quota_usage(p_user_id uuid) definition missing")
        ret_cols, body = m.group(1), m.group(2)
        for expected_col in (
            "user_stored_bytes bigint",
            "user_reserved_bytes bigint",
            "user_outstanding int",
            "global_stored_bytes bigint",
            "global_reserved_bytes bigint",
        ):
            self.assertIn(expected_col, " ".join(ret_cols.lower().split()))
        self.assertGreaterEqual(
            len(re.findall(r"expires_at\s*>\s*now\(\)", body, re.I)),
            3,
            "user_reserved_bytes, user_outstanding, and global_reserved_bytes must all filter expires_at > now()",
        )

    def test_9_2_autocut_rev0_cas_and_undo_redo_sync(self):
        self.assertRegex(
            self.sql,
            re.compile(
                r"alter\s+table\s+public\.clip_revisions\s+add\s+constraint\s+\w+\s+check\s*\(\s*action_type\s+in\s*\([^)]*'auto_cut'",
                re.I | re.S,
            ),
        )
        self.assertRegex(
            self.sql,
            re.compile(r"if\s+v_curr_rev\s*=\s*0\s+then.*?p_base_revision\s*<>\s*0", re.I | re.S),
        )
        for fn in ("cloud_undo_project_document", "cloud_redo_project_document"):
            m = re.search(
                rf"create\s+or\s+replace\s+function\s+public\.{fn}\b(.*?)\$\$;",
                self.sql,
                re.I | re.S,
            )
            self.assertIsNotNone(m, f"{fn} missing in 0031")
            fn_text = m.group(1)
            self.assertIn("caption_data", fn_text)
            self.assertIn("start_time", fn_text)
            self.assertIn("end_time", fn_text)
            self.assertIn("range_revision", fn_text)

    def test_9_3_and_9_4_publishing_targets_and_publications(self):
        self.assertRegex(
            self.sql,
            re.compile(
                r"create\s+table\s+if\s+not\s+exists\s+public\.publishing_job_targets\s*\(.*?"
                r"unique\s*\(\s*job_id\s*,\s*platform\s*,\s*account_id\s*\)",
                re.I | re.S,
            ),
        )
        self.assertRegex(
            self.sql,
            re.compile(
                r"alter\s+table\s+public\.publishing_jobs\s+add\s+column\s+if\s+not\s+exists\s+account_ids\s+jsonb\s+not\s+null\s+default\s+'\{\}'::jsonb",
                re.I,
            ),
        )
        self.assertRegex(
            self.sql,
            re.compile(
                r"alter\s+table\s+public\.publications\s+add\s+constraint\s+publications_platform_check\s+check\s*\(\s*platform\s+in\s*\(\s*'youtube'\s*,\s*'instagram'\s*,\s*'linkedin'\s*,\s*'twitter'\s*,\s*'facebook'\s*\)\s*\)",
                re.I,
            ),
        )
        self.assertRegex(
            self.sql,
            re.compile(r"alter\s+table\s+public\.publications\s+alter\s+column\s+clip_id\s+drop\s+not\s+null", re.I),
        )
        self.assertRegex(
            self.sql,
            re.compile(r"clip_id\s+is\s+not\s+null\s+or\s+local_clip_id\s+is\s+not\s+null", re.I),
        )
        self.assertIn("cloud_update_publishing_target", self.sql)
        self.assertIn("cloud_claim_publishing_targets", self.sql)

    def test_9_5_search_path_revokes_and_tenant_backfill(self):
        for fn in (
            "cloud_quota_usage",
            "cloud_put_project_document",
            "cloud_undo_project_document",
            "cloud_redo_project_document",
            "backfill_legacy_clip_to_project_document",
            "cloud_enqueue_publishing_job",
            "cloud_claim_publishing_jobs",
            "cloud_claim_publishing_targets",
            "cloud_update_publishing_target",
            "cloud_submit_job",
        ):
            m = re.search(
                rf"create\s+or\s+replace\s+function\s+public\.{fn}\b.*?\bas\s+\$\$",
                self.sql,
                re.I | re.S,
            )
            self.assertIsNotNone(m, f"Function public.{fn} header missing in 0031")
            self.assertRegex(
                m.group(0),
                re.compile(r"set\s+search_path\s*=\s*pg_catalog\s*,\s*public", re.I),
                f"{fn} must set search_path = pg_catalog, public",
            )
        self.assertRegex(
            self.sql,
            re.compile(
                r"alter\s+function\s+public\.cloud_submit_direct_job\s*\(\s*uuid\s*,\s*uuid\s*,\s*text\s*\)\s+set\s+search_path\s*=\s*pg_catalog\s*,\s*public",
                re.I,
            ),
        )
        self.assertRegex(
            self.sql,
            re.compile(r"ew\.user_id\s*=\s*r\.user_id", re.I),
            "backfill_legacy_clip_to_project_document must scope edit_windows by user_id",
        )

    def test_9_6_and_9_7_url_regex_and_indexes(self):
        self.assertIn(
            r"(?:[?&]v=|shorts/|live/|embed/|youtu\.be/)([A-Za-z0-9_-]{11})",
            self.raw_sql,
        )
        for tbl in (
            "publishing_jobs",
            "publishing_job_targets",
            "clip_revisions",
            "clips_cloud",
            "media_objects",
            "upload_reservations",
            "edit_windows",
            "processing_jobs",
            "publications",
        ):
            self.assertRegex(
                self.sql,
                re.compile(
                    rf"create\s+(?:unique\s+)?index\s+if\s+not\s+exists\s+\w+\s+on\s+public\.{tbl}\b",
                    re.I,
                ),
                f"Expected new high-churn index for public.{tbl} in 0031",
            )


class TestWave1MigrationsLivePostgres(unittest.TestCase):
    cluster: _EphemeralPostgresCluster | None = None
    conn = None

    @classmethod
    def setUpClass(cls) -> None:
        try:
            import psycopg2  # noqa: F401
        except ImportError as exc:
            raise unittest.SkipTest(f"psycopg2 not installed: {exc}")

        cls.cluster = _EphemeralPostgresCluster()
        try:
            cls.cluster.__enter__()
            cls.conn = cls.cluster.connect()
            cls.conn.autocommit = True
            with cls.conn.cursor() as cur:
                cur.execute(STUB_SUPABASE_PREAMBLE)
                for path in MIGRATIONS:
                    cur.execute(path.read_text(encoding="utf-8"))
                # Re-run 0031 a second time to verify idempotency.
                cur.execute(REPAIR_MIGRATION.read_text(encoding="utf-8"))
        except Exception:
            cls.tearDownClass()
            raise

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.conn is not None:
            try:
                cls.conn.close()
            except Exception:
                pass
        if cls.cluster is not None:
            cls.cluster.__exit__(None, None, None)

    def _create_user(self, cur, email: str) -> str:
        uid = str(uuid.uuid4())
        cur.execute(
            "insert into auth.users (id, email, raw_user_meta_data) values (%s, %s, %s::jsonb)",
            (uid, email, json.dumps({"display_name": email.split("@")[0]})),
        )
        return uid

    def test_00_rls_two_user_checklist_passes(self):
        checklist_path = Path("tests/rls_two_user_checklist.sql")
        self.assertTrue(checklist_path.is_file())
        with self.conn.cursor() as cur:
            user_a = self._create_user(cur, "rls_a@example.com")
            user_b = self._create_user(cur, "rls_b@example.com")
            sql = checklist_path.read_text(encoding="utf-8")
            sql = sql.replace("@USER_A_UUID@", user_a).replace("@USER_B_UUID@", user_b)
            # Strip trailing rollback/begin? Execute directly; script runs BEGIN...ROLLBACK.
            cur.execute(sql)

    def test_9_1_expired_reservations_excluded_from_quota_usage(self):
        with self.conn.cursor() as cur:
            u1 = self._create_user(cur, "quota1@example.com")
            u2 = self._create_user(cur, "quota2@example.com")

            # Active stored object for u1: 1000 bytes (staging_key=null -> no promotion duplicate)
            cur.execute(
                """
                insert into public.media_objects (user_id, kind, status, final_key, mime_type, byte_count)
                values (%s, 'clip_video', 'active', %s, 'video/mp4', 1000)
                """,
                (u1, f"final/{u1}/active1.mp4"),
            )
            # Pending media + active reservation (500 bytes) + expired reservation (700 bytes) for u1
            cur.execute(
                "insert into public.media_objects (user_id, kind, status, staging_key, mime_type) values (%s, 'clip_video', 'pending', %s, 'video/mp4') returning id",
                (u1, f"staging/{u1}/p1"),
            )
            m1_active_res = cur.fetchone()[0]
            cur.execute(
                "insert into public.media_objects (user_id, kind, status, staging_key, mime_type) values (%s, 'clip_video', 'pending', %s, 'video/mp4') returning id",
                (u1, f"staging/{u1}/p2"),
            )
            m1_expired_res = cur.fetchone()[0]
            cur.execute(
                """
                insert into public.upload_reservations (user_id, media_object_id, client_token, expected_bytes, status, expires_at)
                values
                  (%s, %s, 'tok-u1-live', 500, 'pending', now() + interval '15 minutes'),
                  (%s, %s, 'tok-u1-exp', 700, 'pending', now() - interval '1 minute')
                """,
                (u1, m1_active_res, u1, m1_expired_res),
            )

            # Pending media + active reservation (300 bytes) + expired reservation (9000 bytes) for u2
            cur.execute(
                "insert into public.media_objects (user_id, kind, status, staging_key, mime_type) values (%s, 'clip_video', 'pending', %s, 'video/mp4') returning id",
                (u2, f"staging/{u2}/p1"),
            )
            m2_active_res = cur.fetchone()[0]
            cur.execute(
                "insert into public.media_objects (user_id, kind, status, staging_key, mime_type) values (%s, 'clip_video', 'pending', %s, 'video/mp4') returning id",
                (u2, f"staging/{u2}/p2"),
            )
            m2_expired_res = cur.fetchone()[0]
            cur.execute(
                """
                insert into public.upload_reservations (user_id, media_object_id, client_token, expected_bytes, status, expires_at)
                values
                  (%s, %s, 'tok-u2-live', 300, 'pending', now() + interval '15 minutes'),
                  (%s, %s, 'tok-u2-exp', 9000, 'pending', now() - interval '5 minutes')
                """,
                (u2, m2_active_res, u2, m2_expired_res),
            )

            cur.execute("select * from public.cloud_quota_usage(%s)", (u1,))
            u_stored, u_resv, u_out, g_stored, g_resv = cur.fetchone()
            self.assertEqual(u_stored, 1000)
            self.assertEqual(u_resv, 500)
            self.assertEqual(u_out, 1)
            self.assertGreaterEqual(g_stored, 1000)
            self.assertEqual(g_resv, 800)  # 500 + 300; excludes 700 and 9000 expired reservations

            # Also verify cloud_reserve_upload does not count expired reservations against u1's quota
            cur.execute("update public.profiles set storage_quota_bytes = 2000 where user_id = %s", (u1,))
            # u1 uses 1000 + 500 = 1500; reserving 500 more (=2000) must succeed even though 700 expired sits pending!
            cur.execute(
                "select * from public.cloud_reserve_upload(%s, 'clip_video', 'video/mp4', 500, 'tok-u1-tight')",
                (u1,),
            )
            row = cur.fetchone()
            self.assertIsNotNone(row[0])

    def test_9_2_autocut_rev0_cas_rev1_undo_and_caption_hull_sync(self):
        import psycopg2

        with self.conn.cursor() as cur:
            uid = self._create_user(cur, "editor@example.com")
            cur.execute(
                """
                insert into public.clips_cloud (
                  user_id, local_clip_id, title, status,
                  ai_suggested_start_time, ai_suggested_end_time,
                  start_time, end_time, range_revision
                ) values (%s, 'clip_edl_1', 'EDL Test', 'ready_review', 10, 50, 10, 50, 1)
                returning id
                """,
                (uid,),
            )
            clip_uuid = cur.fetchone()[0]

            doc1 = {
                "schema_version": 1,
                "timeline": {
                    "segments": [{"id": "s1", "source_start": 10.0, "source_end": 40.0}],
                    "kept_duration": 30.0,
                },
                "tracks": [
                    {
                        "id": "cap_main",
                        "kind": "captions",
                        "is_primary": True,
                        "coordinate_system": "source_relative",
                        "groups": [{"id": "g1", "start": 10.0, "end": 12.0, "text": "Original"}],
                    }
                ],
                "metadata": {"media_fingerprint": "mf1", "render_fingerprint": "rf1"},
            }
            doc2 = {
                "schema_version": 1,
                "timeline": {
                    "segments": [{"id": "s1", "source_start": 12.0, "source_end": 36.0}],
                    "kept_duration": 24.0,
                },
                "tracks": [
                    {
                        "id": "cap_main",
                        "kind": "captions",
                        "is_primary": True,
                        "coordinate_system": "source_relative",
                        "groups": [{"id": "g1", "start": 12.0, "end": 14.0, "text": "Edited"}],
                    }
                ],
                "metadata": {"media_fingerprint": "mf2", "render_fingerprint": "rf2"},
            }

            # 1. Rev-0 CAS must reject non-zero base_revision
            cur.execute(
                "select * from public.cloud_put_project_document(%s, 'clip_edl_1', %s::jsonb, 1, 'op-bad', 'auto_cut')",
                (uid, json.dumps(doc1)),
            )
            rev, conflict, cur_doc = cur.fetchone()
            self.assertEqual(rev, 0)
            self.assertTrue(conflict)
            self.assertIsNone(cur_doc)

            # 2. Valid rev-0 put with base_revision=0 and action_type='auto_cut'
            cur.execute(
                "select * from public.cloud_put_project_document(%s, 'clip_edl_1', %s::jsonb, 0, 'op-1', 'auto_cut')",
                (uid, json.dumps(doc1)),
            )
            rev, conflict, _ = cur.fetchone()
            self.assertEqual(rev, 1)
            self.assertFalse(conflict)

            cur.execute(
                "select start_time, end_time, range_revision, can_undo, caption_data from public.clips_cloud where id = %s",
                (clip_uuid,),
            )
            st, en, rrev, can_undo, cap_data = cur.fetchone()
            self.assertEqual(float(st), 10.0)
            self.assertEqual(float(en), 40.0)
            self.assertEqual(rrev, 2)  # bumped because 10..50 -> 10..40
            self.assertFalse(can_undo)
            self.assertEqual(cap_data["groups"][0]["text"], "Original")

            cur.execute(
                "select action_type, state_parent_revision from public.clip_revisions where clip_id = %s and revision = 1",
                (clip_uuid,),
            )
            act, spr = cur.fetchone()
            self.assertEqual(act, "auto_cut")
            self.assertIsNone(spr)

            # 3. Undo at rev 1 fails gracefully with business exception (not 'target revision document not found')
            with self.assertRaises(psycopg2.errors.RaiseException) as ctx:
                cur.execute(
                    "select * from public.cloud_undo_project_document(%s, 'clip_edl_1', 'op-undo-root')",
                    (uid,),
                )
            self.assertIn("oldest revision", str(ctx.exception))

            # 3b. Simulated pre-0031 rev-1 row (state_parent_revision = 0, can_undo = true) also fails gracefully
            cur.execute(
                """
                insert into public.clips_cloud (
                  user_id, local_clip_id, title, status,
                  ai_suggested_start_time, ai_suggested_end_time,
                  start_time, end_time, range_revision,
                  segments_revision, active_state_revision, can_undo
                ) values (%s, 'clip_legacy_rev0_parent', 'Legacy Rev1', 'ready_review', 0, 30, 0, 30, 0, 1, 1, true)
                returning id
                """,
                (uid,),
            )
            legacy_clip_uuid = cur.fetchone()[0]
            cur.execute(
                """
                insert into public.clip_revisions (
                  clip_id, user_id, revision, log_parent_revision, state_parent_revision,
                  active_state_revision, redo_stack, operation_id, project_document,
                  media_fingerprint, render_fingerprint, action_type, author
                ) values (
                  %s, %s, 1, 0, 0, 1, '[]'::jsonb, 'op-legacy-1', %s::jsonb, 'mf', 'rf', 'split', 'mobile'
                )
                """,
                (legacy_clip_uuid, uid, json.dumps(doc1)),
            )
            with self.assertRaises(psycopg2.errors.RaiseException) as ctx_leg:
                cur.execute(
                    "select * from public.cloud_undo_project_document(%s, 'clip_legacy_rev0_parent', 'op-undo-legacy')",
                    (uid,),
                )
            self.assertIn("oldest revision", str(ctx_leg.exception))

            # 4. Also verify rev-0 put with NULL base_revision succeeds on another clip
            cur.execute(
                """
                insert into public.clips_cloud (
                  user_id, local_clip_id, title, status,
                  ai_suggested_start_time, ai_suggested_end_time,
                  start_time, end_time, range_revision
                ) values (%s, 'clip_edl_null', 'EDL Null', 'ready_review', 0, 30, 0, 30, 0)
                """,
                (uid,),
            )
            cur.execute(
                "select * from public.cloud_put_project_document(%s, 'clip_edl_null', %s::jsonb, null, 'op-null-1', 'initial')",
                (uid, json.dumps(doc1)),
            )
            self.assertEqual(cur.fetchone()[:2], (1, False))

            # 5. Rev 1 -> Rev 2 edit changes hull and captions, then Undo -> Redo syncs both + range_revision
            cur.execute(
                "select * from public.cloud_put_project_document(%s, 'clip_edl_1', %s::jsonb, 1, 'op-2', 'trim_edge')",
                (uid, json.dumps(doc2)),
            )
            self.assertEqual(cur.fetchone()[:2], (2, False))

            cur.execute(
                "select * from public.cloud_undo_project_document(%s, 'clip_edl_1', 'op-undo-2')",
                (uid,),
            )
            u_rev, u_conf, u_doc = cur.fetchone()
            self.assertEqual(u_rev, 3)
            self.assertFalse(u_conf)
            self.assertEqual(u_doc["tracks"][0]["groups"][0]["text"], "Original")

            cur.execute(
                "select start_time, end_time, range_revision, active_state_revision, can_undo, can_redo, caption_data from public.clips_cloud where id = %s",
                (clip_uuid,),
            )
            st, en, rrev, active_rev, can_undo, can_redo, cap_data = cur.fetchone()
            self.assertEqual((float(st), float(en)), (10.0, 40.0))
            self.assertEqual(rrev, 4)
            self.assertEqual(active_rev, 1)
            self.assertFalse(can_undo)
            self.assertTrue(can_redo)
            self.assertEqual(cap_data["groups"][0]["text"], "Original")

            cur.execute(
                "select * from public.cloud_redo_project_document(%s, 'clip_edl_1', 'op-redo-2')",
                (uid,),
            )
            r_rev, r_conf, _ = cur.fetchone()
            self.assertEqual((r_rev, r_conf), (4, False))
            cur.execute(
                "select start_time, end_time, range_revision, active_state_revision, can_undo, can_redo, caption_data from public.clips_cloud where id = %s",
                (clip_uuid,),
            )
            st, en, rrev, active_rev, can_undo, can_redo, cap_data = cur.fetchone()
            self.assertEqual((float(st), float(en)), (12.0, 36.0))
            self.assertEqual(rrev, 5)
            self.assertEqual(active_rev, 2)
            self.assertTrue(can_undo)
            self.assertFalse(can_redo)
            self.assertEqual(cap_data["groups"][0]["text"], "Edited")

    def test_9_3_and_9_4_publishing_targets_and_publications(self):
        import psycopg2

        with self.conn.cursor() as cur:
            uid = self._create_user(cur, "pub@example.com")
            cur.execute(
                """
                insert into public.clips_cloud (
                  user_id, local_clip_id, title, status,
                  ai_suggested_start_time, ai_suggested_end_time,
                  start_time, end_time, range_revision
                ) values (%s, 'clip_pub_1', 'Pub Clip', 'approved', 0, 20, 0, 20, 0)
                returning id
                """,
                (uid,),
            )
            clip_uuid = cur.fetchone()[0]

            # 8-arg backward-compatible enqueue
            cur.execute(
                """
                select * from public.cloud_enqueue_publishing_job(
                  %s, %s, 'Title 8-arg', 'Desc', '["youtube"]'::jsonb, 'private',
                  %s, 'fp-1'
                )
                """,
                (uid, str(clip_uuid), f"final/{uid}/a.mp4"),
            )
            job8_id, job8_status = cur.fetchone()
            self.assertEqual(job8_status, "queued")

            # 9-arg enqueue with multi-platform + account_ids ('x' normalized -> 'twitter')
            cur.execute(
                """
                select * from public.cloud_enqueue_publishing_job(
                  %s, %s, 'Title 9-arg', 'Desc',
                  '["youtube", "instagram", "linkedin", "x", "facebook"]'::jsonb,
                  'public', %s, 'fp-2',
                  '{"youtube": "yt_ch_1", "instagram": "ig_acct_1", "twitter": "x_acct_1"}'::jsonb
                )
                """,
                (uid, str(clip_uuid), f"final/{uid}/b.mp4"),
            )
            job9_id, _ = cur.fetchone()
            cur.execute(
                "select platform, account_id, status from public.publishing_job_targets where job_id = %s order by platform",
                (job9_id,),
            )
            targets = cur.fetchall()
            self.assertEqual(
                targets,
                [
                    ("facebook", "", "queued"),
                    ("instagram", "ig_acct_1", "queued"),
                    ("linkedin", "", "queued"),
                    ("twitter", "x_acct_1", "queued"),
                    ("youtube", "yt_ch_1", "queued"),
                ],
            )

            # Test target claim + update + attempt cap + published terminal protection
            cur.execute(
                "select id, platform, status, attempt_count from public.cloud_claim_publishing_targets(%s, 10, 2)",
                (job9_id,),
            )
            claimed_targets = cur.fetchall()
            self.assertEqual(len(claimed_targets), 5)
            yt_target_id = next(r[0] for r in claimed_targets if r[1] == "youtube")
            ig_target_id = next(r[0] for r in claimed_targets if r[1] == "instagram")

            # Publish YouTube target and verify it cannot regress
            cur.execute(
                "select status, remote_id from public.cloud_update_publishing_target(%s, 'published', 'yt123', 'https://youtu.be/yt123')",
                (yt_target_id,),
            )
            self.assertEqual(cur.fetchone(), ("published", "yt123"))
            cur.execute(
                "select status, remote_id from public.cloud_update_publishing_target(%s, 'failed', null, null, 'late err')",
                (yt_target_id,),
            )
            self.assertEqual(cur.fetchone(), ("published", "yt123"))

            # Instagram target hits max_attempts=2 on requeue -> transitions to 'failed'
            cur.execute(
                "select status, attempt_count from public.cloud_update_publishing_target(%s, 'queued', null, null, 'retry err', null, now(), true, 2)",
                (ig_target_id,),
            )
            self.assertEqual(cur.fetchone(), ("failed", 2))

            # Job-level attempt cap in cloud_claim_publishing_jobs
            cur.execute(
                "update public.publishing_jobs set status = 'processing', attempt_count = 5, updated_at = now() - interval '20 minutes' where id = %s",
                (job8_id,),
            )
            cur.execute("select id from public.cloud_claim_publishing_jobs(10, 600, 5)")
            claimed_job_ids = {r[0] for r in cur.fetchall()}
            self.assertNotIn(job8_id, claimed_job_ids)
            cur.execute("select status from public.publishing_jobs where id = %s", (job8_id,))
            self.assertEqual(cur.fetchone()[0], "failed")

            # Publications: all 5 platforms allowed + local_clip_id without clip_id
            for plat in ("youtube", "instagram", "linkedin", "twitter", "facebook"):
                cur.execute(
                    """
                    insert into public.publications (user_id, clip_id, local_clip_id, platform, account_id, status, published_at)
                    values (%s, null, 'local_only_1', %s, 'acct_1', 'published', now())
                    """,
                    (uid, plat),
                )
            # Duplicate (user_id, local_clip_id, platform, account_id) when clip_id is null rejected
            with self.assertRaises(psycopg2.errors.UniqueViolation):
                cur.execute(
                    """
                    insert into public.publications (user_id, clip_id, local_clip_id, platform, account_id, status, published_at)
                    values (%s, null, 'local_only_1', 'instagram', 'acct_1', 'published', now())
                    """,
                    (uid,),
                )
            # 0024 ON CONFLICT (clip_id, platform, account_id) still works
            cur.execute(
                """
                insert into public.publications (clip_id, user_id, platform, account_id, remote_id, status, published_at)
                values (%s, %s, 'instagram', 'ig_1', 'r1', 'published', now())
                on conflict (clip_id, platform, account_id) do update set remote_id = excluded.remote_id
                """,
                (clip_uuid, uid),
            )
            # Invalid platform and null-both / empty local_clip_id rejected by CHECK constraints
            with self.assertRaises(psycopg2.errors.CheckViolation):
                cur.execute(
                    "insert into public.publications (clip_id, user_id, platform, status) values (%s, %s, 'tiktok', 'queued')",
                    (clip_uuid, uid),
                )
            with self.assertRaises(psycopg2.errors.CheckViolation):
                cur.execute(
                    "insert into public.publications (clip_id, local_clip_id, user_id, platform, status) values (null, null, %s, 'youtube', 'queued')",
                    (uid,),
                )
            with self.assertRaises(psycopg2.errors.CheckViolation):
                cur.execute(
                    "insert into public.publications (clip_id, local_clip_id, user_id, platform, status) values (null, '   ', %s, 'youtube', 'queued')",
                    (uid,),
                )

    def test_9_5_tenant_isolated_backfill_search_path_and_privilege_denial(self):
        import psycopg2

        with self.conn.cursor() as cur:
            user_a = self._create_user(cur, "tenant_a@example.com")
            user_b = self._create_user(cur, "tenant_b@example.com")

            # User B has an edit_window for local_clip_id='shared_1' at window_start=999.0
            cur.execute(
                """
                insert into public.edit_windows (user_id, local_clip_id, window_start, window_end, status)
                values (%s, 'shared_1', 999.0, 1020.0, 'ready')
                """,
                (user_b,),
            )
            # User A has a legacy window_relative clip with the SAME local_clip_id='shared_1' and NO own window
            cur.execute(
                """
                insert into public.clips_cloud (
                  user_id, local_clip_id, title, status,
                  ai_suggested_start_time, ai_suggested_end_time,
                  start_time, end_time, caption_data
                ) values (
                  %s, 'shared_1', 'A Clip', 'ready_review', 10, 30, 10, 30,
                  '{"coordinate_system": "window_relative", "groups": [{"start": 1.0, "end": 2.0, "words": [{"w": "hi", "start": 1.0, "end": 2.0}]}]}'::jsonb
                ) returning id
                """,
                (user_a,),
            )
            clip_a = cur.fetchone()[0]

            # Must fail rather than borrowing User B's 999.0 window_start
            with self.assertRaises(psycopg2.errors.RaiseException) as ctx:
                cur.execute("select public.backfill_legacy_clip_to_project_document(%s)", (clip_a,))
            self.assertIn("unambiguous edit window not found", str(ctx.exception))

            # Now give User A their own edit_window at 100.0 -> backfill must use 100.0, not 999.0
            cur.execute(
                """
                insert into public.edit_windows (user_id, local_clip_id, window_start, window_end, status)
                values (%s, 'shared_1', 100.0, 140.0, 'ready')
                """,
                (user_a,),
            )
            cur.execute("select public.backfill_legacy_clip_to_project_document(%s)", (clip_a,))
            cur.execute("select project_document from public.clips_cloud where id = %s", (clip_a,))
            doc = cur.fetchone()[0]
            cap_track = next(t for t in doc["tracks"] if t["kind"] == "captions")
            self.assertEqual(float(cap_track["groups"][0]["start"]), 101.0)

            # Audit pg_proc for all Wave 1 + 0026..0030 SECURITY DEFINER functions
            cur.execute(
                """
                select p.proname, p.proconfig,
                       pg_catalog.has_function_privilege('public', p.oid, 'EXECUTE'),
                       pg_catalog.has_function_privilege('anon', p.oid, 'EXECUTE'),
                       pg_catalog.has_function_privilege('authenticated', p.oid, 'EXECUTE'),
                       pg_catalog.has_function_privilege('service_role', p.oid, 'EXECUTE')
                from pg_catalog.pg_proc p
                join pg_catalog.pg_namespace n on n.oid = p.pronamespace
                where n.nspname = 'public'
                  and p.prosecdef = true
                  and p.proname in (
                    'cloud_quota_usage',
                    'cloud_put_project_document',
                    'cloud_undo_project_document',
                    'cloud_redo_project_document',
                    'backfill_legacy_clip_to_project_document',
                    'cloud_enqueue_publishing_job',
                    'cloud_claim_publishing_jobs',
                    'cloud_claim_publishing_targets',
                    'cloud_update_publishing_target',
                    'cloud_submit_job',
                    'cloud_submit_direct_job'
                  )
                """
            )
            rows = cur.fetchall()
            self.assertEqual(len(rows), 11)
            for proname, proconfig, pub_exec, anon_exec, auth_exec, svc_exec in rows:
                self.assertIn("search_path=pg_catalog, public", proconfig or [], f"{proname} unsafe search_path: {proconfig}")
                self.assertFalse(pub_exec, f"{proname} must not be executable by PUBLIC")
                self.assertFalse(anon_exec, f"{proname} must not be executable by anon")
                self.assertFalse(auth_exec, f"{proname} must not be executable by authenticated")
                self.assertTrue(svc_exec, f"{proname} must be executable by service_role")

            # Privilege & TRUNCATE denial checks via pg_catalog + SET LOCAL ROLE
            for tbl in (
                "public.clip_revisions",
                "public.publishing_jobs",
                "public.publishing_job_targets",
                "public.publications",
                "public.clips_cloud",
            ):
                for priv in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
                    cur.execute("select has_table_privilege('anon', %s, %s)", (tbl, priv))
                    self.assertFalse(cur.fetchone()[0], f"anon unexpectedly has {priv} on {tbl}")
                    cur.execute("select has_table_privilege('authenticated', %s, %s)", (tbl, priv))
                    self.assertFalse(cur.fetchone()[0], f"authenticated unexpectedly has {priv} on {tbl}")

            # Live role-switch TRUNCATE + write denial test inside a transaction
            cur.execute(
                """
                select * from public.cloud_enqueue_publishing_job(
                  %s, %s, 'Job A', '', '["youtube"]'::jsonb, 'private', %s, 'fp-a'
                )
                """,
                (user_a, str(clip_a), f"final/{user_a}/a.mp4"),
            )
            cur.execute("begin")
            try:
                cur.execute(
                    "select set_config('request.jwt.claims', %s, true)",
                    (json.dumps({"sub": user_b, "role": "authenticated"}),),
                )
                cur.execute("set local role authenticated")
                # RLS hides User A's publishing_jobs and publishing_job_targets from User B
                cur.execute("select count(*) from public.publishing_jobs where user_id = %s", (user_a,))
                self.assertEqual(cur.fetchone()[0], 0)
                cur.execute("select count(*) from public.publishing_job_targets where user_id = %s", (user_a,))
                self.assertEqual(cur.fetchone()[0], 0)
                # Direct TRUNCATE fails with 42501
                for tbl in ("public.clip_revisions", "public.publishing_jobs", "public.publishing_job_targets"):
                    with self.assertRaises(psycopg2.errors.InsufficientPrivilege):
                        cur.execute("savepoint sp_trunc")
                        cur.execute(f"truncate {tbl}")
                    cur.execute("rollback to savepoint sp_trunc")
            finally:
                cur.execute("rollback")

    def test_9_6_youtube_url_parsing_and_source_type_provenance(self):
        with self.conn.cursor() as cur:
            uid = self._create_user(cur, "yturl@example.com")
            cases = [
                ("https://www.youtube.com/watch?feature=share&v=dQw4w9WgXcQ&t=12", "dQw4w9WgXcQ", "youtube_import", None),
                ("https://www.youtube.com/live/LiveId12345?si=abc", "LiveId12345", "youtube_import", None),
                ("https://www.youtube.com/embed/EmbedId6789?autoplay=1", "EmbedId6789", "youtube_import", None),
                ("https://youtu.be/ShortBe1234?t=5", "ShortBe1234", "camera", "sess_cam_1"),
                ("https://youtube.com/shorts/ShortsId999", "ShortsId999", "youtube_import", None),
            ]
            for url, expected_vid, src_type, sess_id in cases:
                cur.execute(
                    "select id, status from public.cloud_submit_job(%s, %s, %s, %s)",
                    (uid, url, src_type, sess_id),
                )
                job_id, status = cur.fetchone()
                self.assertEqual(status, "queued")
                cur.execute(
                    "select video_id, source_type, session_id from public.processing_jobs where id = %s",
                    (job_id,),
                )
                vid, got_src, got_sess = cur.fetchone()
                self.assertEqual(vid, expected_vid, f"Failed parsing {url}")
                self.assertEqual(got_src, src_type)
                self.assertEqual(got_sess, sess_id)

    def test_9_7_high_churn_indexes_exist(self):
        expected_indexes = {
            "idx_pub_jobs_processing_updated",
            "idx_pub_jobs_user_created",
            "idx_pub_targets_job_status",
            "idx_pub_targets_retry_queue",
            "idx_pub_targets_user_status",
            "idx_clip_revisions_user_created",
            "idx_clips_cloud_session_id",
            "idx_clips_cloud_user_updated",
            "idx_media_objects_clip_id",
            "idx_media_objects_user_active_created",
            "idx_media_objects_active_staging",
            "idx_upload_reservations_media_obj",
            "idx_upload_reservations_user_status_expires",
            "idx_edit_windows_user_clip_created",
            "idx_edit_windows_media_obj",
            "idx_pjobs_claimed_at",
            "idx_pjobs_user_video",
            "idx_pjobs_user_created",
            "publications_user_local_clip_platform_account_uidx",
            "idx_publications_user_local_clip",
            "idx_publications_user_published_at",
        }
        with self.conn.cursor() as cur:
            cur.execute(
                "select indexname from pg_catalog.pg_indexes where schemaname = 'public'"
            )
            found = {row[0] for row in cur.fetchall()}
        missing = sorted(expected_indexes - found)
        self.assertEqual(missing, [], f"Missing expected Wave 1 indexes: {missing}")


if __name__ == "__main__":
    unittest.main()
