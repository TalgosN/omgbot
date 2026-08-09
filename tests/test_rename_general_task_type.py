import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.rename_general_task_type import rename_general_task_type
from task_notifications import GENERAL_TASK_TYPE, LEGACY_GENERAL_TASK_TYPE


class RenameGeneralTaskTypeTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / 'omgbot.sql'
        connection = sqlite3.connect(self.db_path)
        connection.execute(
            'CREATE TABLE tasks (id INTEGER PRIMARY KEY, type TEXT, status TEXT)'
        )
        connection.executemany(
            'INSERT INTO tasks (type, status) VALUES (?, ?)',
            [
                (LEGACY_GENERAL_TASK_TYPE, 'В работе'),
                (LEGACY_GENERAL_TASK_TYPE, 'Архив'),
                ('Ремонт', 'В работе'),
            ],
        )
        connection.commit()
        connection.close()

    def tearDown(self):
        self.temp_dir.cleanup()

    def task_types(self):
        connection = sqlite3.connect(self.db_path)
        try:
            return connection.execute(
                'SELECT type, status FROM tasks ORDER BY id'
            ).fetchall()
        finally:
            connection.close()

    def test_preview_reports_rows_without_changing_database(self):
        result = rename_general_task_type(self.db_path)

        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['matching_rows'], 2)
        self.assertEqual(result['rows_by_status'], {'Архив': 1, 'В работе': 1})
        self.assertIn((LEGACY_GENERAL_TASK_TYPE, 'В работе'), self.task_types())

    def test_apply_renames_only_general_tasks_and_creates_backup(self):
        result = rename_general_task_type(self.db_path, apply=True)

        self.assertEqual(result['status'], 'renamed')
        self.assertEqual(result['updated_rows'], 2)
        self.assertTrue(Path(result['backup']).is_file())
        self.assertEqual(self.task_types(), [
            (GENERAL_TASK_TYPE, 'В работе'),
            (GENERAL_TASK_TYPE, 'Архив'),
            ('Ремонт', 'В работе'),
        ])

    def test_repeated_apply_is_idempotent(self):
        rename_general_task_type(self.db_path, apply=True)

        result = rename_general_task_type(self.db_path, apply=True)

        self.assertEqual(result['status'], 'already_renamed')
        self.assertFalse(result['applied'])


if __name__ == '__main__':
    unittest.main()
