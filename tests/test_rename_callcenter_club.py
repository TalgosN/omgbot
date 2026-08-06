import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.rename_callcenter_club import rename_callcenter


class RenameCallcenterClubTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / 'omgbot.sql'
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            'CREATE TABLE clubs (id INTEGER PRIMARY KEY, club TEXT, status TEXT)'
        )
        conn.execute(
            'INSERT INTO clubs (club, status) VALUES (?, ?)',
            ('КЦ\t', 'Открыт'),
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        self.temp_dir.cleanup()

    def club_row(self):
        conn = sqlite3.connect(self.db_path)
        try:
            return conn.execute('SELECT club, status FROM clubs').fetchone()
        finally:
            conn.close()

    def test_preview_does_not_change_database(self):
        result = rename_callcenter(self.db_path)

        self.assertEqual(result['status'], 'ready')
        self.assertFalse(result['applied'])
        self.assertEqual(self.club_row(), ('КЦ\t', 'Открыт'))

    def test_apply_renames_club_preserves_status_and_creates_backup(self):
        result = rename_callcenter(self.db_path, apply=True)

        self.assertEqual(result['status'], 'renamed')
        self.assertEqual(self.club_row(), ('Коллцентр', 'Открыт'))
        self.assertTrue(Path(result['backup']).is_file())

    def test_repeated_run_is_idempotent(self):
        rename_callcenter(self.db_path, apply=True)

        result = rename_callcenter(self.db_path, apply=True)

        self.assertEqual(result['status'], 'already_renamed')
        self.assertFalse(result['applied'])
        self.assertEqual(self.club_row(), ('Коллцентр', 'Открыт'))


if __name__ == '__main__':
    unittest.main()
