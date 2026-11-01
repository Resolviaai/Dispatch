import unittest
from dispatch.db import init_db, get_db_connection

class TestDatabaseSchema(unittest.TestCase):
    def test_schema_columns(self):
        init_db()
        with get_db_connection() as conn:
            cols = [col[1] for col in conn.cursor().execute('PRAGMA table_info(clips);').fetchall()]
            self.assertIn('project_document', cols)
            self.assertIn('segments_json', cols)
            self.assertIn('kept_duration', cols)
            self.assertIn('segments_revision', cols)
            self.assertIn('active_state_revision', cols)
            self.assertIn('redo_stack', cols)
            self.assertIn('can_undo', cols)
            self.assertIn('can_redo', cols)
            self.assertIn('media_fingerprint', cols)
            self.assertIn('render_fingerprint', cols)
            self.assertIn('rendered_fingerprint', cols)

            rev_tables = conn.cursor().execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='clip_revisions';"
            ).fetchall()
            self.assertEqual(len(rev_tables), 1)

if __name__ == '__main__':
    unittest.main()
