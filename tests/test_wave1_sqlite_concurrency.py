"""Wave 1 Slice 1: SQLite concurrency, WAL lifecycle, transaction safety, and index tests."""

import os
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from dispatch import db


class _RecordingCursor(sqlite3.Cursor):
    def execute(self, sql, parameters=(), /):
        self.connection.executed_sql.append(str(sql))
        return super().execute(sql, parameters)


class _RecordingConnection(sqlite3.Connection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.executed_sql = []

    def cursor(self, factory=None):
        if factory is None:
            return super().cursor(_RecordingCursor)
        return super().cursor(factory)

    def execute(self, sql, parameters=(), /):
        self.executed_sql.append(str(sql))
        return super().execute(sql, parameters)


class TestWave1SqliteConcurrency(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="wave1_sqlite_")
        self.tmp_path = Path(self.temp_dir.name)
        self.db_path = self.tmp_path / "wave1.db"
        self.orig_db_path = db.DB_PATH
        db.DB_PATH = self.db_path
        db.init_db()

    def tearDown(self):
        db.close_db_connections()
        db.DB_PATH = self.orig_db_path
        self.temp_dir.cleanup()

    def test_wal_and_pragmas_not_repeated_per_connection_checkout(self):
        db.close_db_connections()
        fresh_db = self.tmp_path / "fresh_spy.db"
        opened_conns = []
        real_connect = sqlite3.connect

        def spy_connect(*args, **kwargs):
            kwargs.setdefault("factory", _RecordingConnection)
            conn = real_connect(*args, **kwargs)
            opened_conns.append(conn)
            return conn

        try:
            with patch.object(db, "DB_PATH", fresh_db), patch("dispatch.db.sqlite3.connect", side_effect=spy_connect):
                db.init_db()
                # Second init_db() sees journal_mode already 'wal' and must not re-run PRAGMA journal_mode = WAL
                db.init_db()

                conns_seen = []
                for i in range(6):
                    with db.get_db_connection() as conn:
                        conns_seen.append(conn)
                        conn.execute(
                            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                            (f"k_{i}", "v"),
                        )

                # Same persistent connection reused on main thread
                self.assertEqual(len({id(c) for c in conns_seen}), 1)

                # New worker thread opens one connection without PRAGMA journal_mode = WAL
                worker_conns = []

                def worker():
                    for _ in range(4):
                        with db.get_db_connection() as wconn:
                            worker_conns.append(wconn)
                            wconn.execute("SELECT value FROM settings WHERE key = 'k_0'").fetchone()

                t = threading.Thread(target=worker)
                t.start()
                t.join()

                self.assertEqual(len({id(c) for c in worker_conns}), 1)
                self.assertNotEqual(id(conns_seen[0]), id(worker_conns[0]))

                # Verify PRAGMA journal_mode = WAL ran exactly once across init_db + all checkouts
                all_sql = [stmt.strip().upper() for c in opened_conns for stmt in c.executed_sql]
                wal_set_calls = [s for s in all_sql if "PRAGMA JOURNAL_MODE = WAL" in s]
                self.assertEqual(len(wal_set_calls), 1, f"Expected WAL set once, saw: {wal_set_calls}")

                # Verify per-connection PRAGMAs ran once per opened connection (2 connections: main + worker)
                self.assertEqual(len(opened_conns), 2)
                for c in opened_conns:
                    sqls = [s.strip().upper() for s in c.executed_sql]
                    self.assertEqual(sum("PRAGMA BUSY_TIMEOUT = 5000" in s for s in sqls), 1)
                    self.assertEqual(sum("PRAGMA FOREIGN_KEYS = ON" in s for s in sqls), 1)
                    self.assertEqual(sum("PRAGMA SYNCHRONOUS = NORMAL" in s for s in sqls), 1)
                    self.assertEqual(sum("PRAGMA JOURNAL_MODE = WAL" in s for s in sqls), 1 if c is opened_conns[0] else 0)
        finally:
            db.close_db_connections()

        # Verify actual PRAGMA values active on the connection
        with db.get_db_connection() as conn:
            self.assertEqual(str(conn.execute("PRAGMA journal_mode;").fetchone()[0]).lower(), "wal")
            self.assertEqual(int(conn.execute("PRAGMA busy_timeout;").fetchone()[0]), 5000)
            self.assertEqual(int(conn.execute("PRAGMA foreign_keys;").fetchone()[0]), 1)
            # synchronous = NORMAL -> 1
            self.assertEqual(int(conn.execute("PRAGMA synchronous;").fetchone()[0]), 1)

    def test_concurrent_multi_thread_writes_no_lost_updates_or_busy(self):
        with db.write_transaction() as conn:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('counter', '0')")

        num_threads = 8
        increments_per_thread = 25
        barrier = threading.Barrier(num_threads)
        errors = []
        err_lock = threading.Lock()

        def worker(tid: int):
            try:
                barrier.wait()
                for j in range(increments_per_thread):
                    with db.write_transaction() as wconn:
                        cur = wconn.execute("SELECT value FROM settings WHERE key = 'counter'").fetchone()
                        nxt = int(cur["value"]) + 1
                        wconn.execute("UPDATE settings SET value = ? WHERE key = 'counter'", (str(nxt),))
                        wconn.execute(
                            "INSERT INTO settings (key, value) VALUES (?, ?)",
                            (f"thread_{tid}_{j}", "ok"),
                        )
            except Exception as exc:
                with err_lock:
                    errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"Concurrent write errors: {errors}")
        self.assertEqual(db.get_setting("counter"), str(num_threads * increments_per_thread))

    def test_concurrent_reads_during_active_write_transaction(self):
        # Seed baseline row
        clip_id = db.save_clip(
            session_id=None,
            chunk_id=None,
            start_time=0.0,
            end_time=15.0,
            title="Baseline Clip",
            hook="Hook",
            description="Desc",
            hashtags="#wal",
            virality_score=80,
            layout_mode="fit_blur",
        )

        writer_locked = threading.Event()
        reader_done = threading.Event()
        reader_results = []
        reader_errors = []

        def writer():
            with db.write_transaction() as conn:
                conn.execute("UPDATE clips SET title = 'Uncommitted Update' WHERE id = ?", (clip_id,))
                writer_locked.set()
                # Hold BEGIN IMMEDIATE lock briefly while concurrent readers query WAL snapshot
                reader_done.wait(timeout=3.0)
                time.sleep(0.05)

        def reader():
            writer_locked.wait(timeout=3.0)
            try:
                for _ in range(10):
                    row = db.get_clip_by_id(clip_id)
                    reader_results.append(row["title"])
            except Exception as exc:
                reader_errors.append(exc)
            finally:
                reader_done.set()

        wt = threading.Thread(target=writer)
        rt = threading.Thread(target=reader)
        wt.start()
        rt.start()
        wt.join()
        rt.join()

        self.assertEqual(reader_errors, [])
        self.assertEqual(len(reader_results), 10)
        # Readers in WAL mode see the committed pre-commit snapshot without blocking on writer
        self.assertTrue(all(title == "Baseline Clip" for title in reader_results))
        self.assertEqual(db.get_clip_by_id(clip_id)["title"], "Uncommitted Update")

    def test_rollback_on_error_top_level_and_nested_savepoint(self):
        db.set_setting("tx_outer", "initial")

        # 1. Top-level write_transaction rollback on exception
        with self.assertRaises(RuntimeError):
            with db.write_transaction() as conn:
                conn.execute("UPDATE settings SET value = 'dirty' WHERE key = 'tx_outer'")
                raise RuntimeError("boom")
        self.assertEqual(db.get_setting("tx_outer"), "initial")

        # 2. Top-level get_db_connection rollback on exception
        with self.assertRaises(ValueError):
            with db.get_db_connection() as conn:
                conn.execute("UPDATE settings SET value = 'dirty2' WHERE key = 'tx_outer'")
                raise ValueError("boom2")
        self.assertEqual(db.get_setting("tx_outer"), "initial")

        # 3. Nested transaction SAVEPOINT rollback when outer catches inner failure
        with db.write_transaction() as outer:
            outer.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('outer_key', 'kept')")
            try:
                with db.write_transaction() as inner:
                    inner.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('inner_key', 'rolled_back')")
                    raise RuntimeError("inner fail")
            except RuntimeError:
                pass
            outer.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('after_inner', 'also_kept')")

        self.assertEqual(db.get_setting("outer_key"), "kept")
        self.assertIsNone(db.get_setting("inner_key"))
        self.assertEqual(db.get_setting("after_inner"), "also_kept")

    def test_busy_timeout_waits_and_retries_across_connections(self):
        db.set_setting("busy_test", "0")
        lock_acquired = threading.Event()
        errors = []

        def holder():
            with db.write_transaction() as conn:
                conn.execute("UPDATE settings SET value = '1' WHERE key = 'busy_test'")
                lock_acquired.set()
                # Hold IMMEDIATE lock for 350ms (well within 5000ms busy_timeout)
                time.sleep(0.35)

        def contender():
            lock_acquired.wait(timeout=2.0)
            try:
                with db.write_transaction() as conn:
                    val = int(conn.execute("SELECT value FROM settings WHERE key = 'busy_test'").fetchone()["value"])
                    conn.execute("UPDATE settings SET value = ? WHERE key = 'busy_test'", (str(val + 1),))
            except Exception as exc:
                errors.append(exc)

        t1 = threading.Thread(target=holder)
        t2 = threading.Thread(target=contender)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        self.assertEqual(errors, [])
        self.assertEqual(db.get_setting("busy_test"), "2")

    def test_db_path_and_env_switch_isolation_and_windows_cleanup(self):
        # Write unique marker in primary DB
        db.set_setting("isolation_marker", "db_one")

        # 1. Switch via patch.object(db, "DB_PATH", ...) inside TemporaryDirectory
        with tempfile.TemporaryDirectory(prefix="wave1_iso_patch_") as td1:
            db2 = Path(td1) / "second.db"
            with patch.object(db, "DB_PATH", db2):
                db.init_db()
                self.assertIsNone(db.get_setting("isolation_marker"))
                db.set_setting("isolation_marker", "db_two")
                self.assertEqual(db.get_setting("isolation_marker"), "db_two")
                # Even with worker thread opening a connection inside TempDir, restoring DB_PATH closes it
                def bg():
                    db.set_setting("bg_key", "bg_val")
                t = threading.Thread(target=bg)
                t.start()
                t.join()
                self.assertEqual(db.get_setting("bg_key"), "bg_val")
            # Leaving TemporaryDirectory immediately without explicit close_db_connections() must succeed on Windows

        self.assertEqual(db.get_setting("isolation_marker"), "db_one")

        # 2. Switch via DISPATCH_DB_PATH env var when DB_PATH == _default_db_path
        with tempfile.TemporaryDirectory(prefix="wave1_iso_env_") as td2:
            db3 = Path(td2) / "third.db"
            orig_env = os.environ.get("DISPATCH_DB_PATH")
            try:
                db.DB_PATH = db._default_db_path
                os.environ["DISPATCH_DB_PATH"] = str(db3)
                db.init_db()
                self.assertIsNone(db.get_setting("isolation_marker"))
                db.set_setting("isolation_marker", "db_three")
                self.assertEqual(db.get_setting("isolation_marker"), "db_three")
            finally:
                db.close_db_connections()
                if orig_env is not None:
                    os.environ["DISPATCH_DB_PATH"] = orig_env
                else:
                    os.environ.pop("DISPATCH_DB_PATH", None)
                db.DB_PATH = self.db_path

        self.assertEqual(db.get_setting("isolation_marker"), "db_one")

    def test_close_db_connections_defers_active_and_closes_on_exit(self):
        with db.get_db_connection() as conn:
            conn.execute("SELECT 1").fetchone()
            # Closing while active marks slot stale instead of closing mid-block
            closed_now = db.close_db_connections()
            self.assertEqual(closed_now, 0)
            # Connection remains usable until context exits
            self.assertEqual(conn.execute("SELECT 2").fetchone()[0], 2)

        # After exiting, slot was closed; next checkout opens a fresh connection
        with db.get_db_connection() as conn2:
            self.assertEqual(conn2.execute("SELECT 3").fetchone()[0], 3)
        self.assertGreaterEqual(db.close_db_connections(), 1)

    def test_high_churn_sqlite_indexes_exist(self):
        expected_indexes = {
            "idx_outbox_status_retry",
            "idx_outbox_clip_status",
            "idx_outbox_clip_platform",
            "idx_clips_user_status",
            "idx_clips_session_status",
            "idx_clips_status_purge",
            "idx_jobs_claim",
            "idx_jobs_chunk",
            "idx_jobs_status_created",
            "idx_chunks_file_hash",
            "idx_chunks_status",
            "idx_transcripts_chunk",
            "idx_transcripts_session",
            "idx_rejections_clip_id",
            "idx_rejections_user_created",
            "idx_youtube_user_status",
        }
        with db.get_db_connection() as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
            present = {row["name"] for row in rows}
        missing = sorted(expected_indexes - present)
        self.assertEqual(missing, [], f"Missing expected indexes: {missing}")


if __name__ == "__main__":
    unittest.main()
