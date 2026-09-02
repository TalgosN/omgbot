import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import repair_sqlite_integrity as repair


class RepairSqliteIntegrityTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / 'omgbot.sql'
        connection = sqlite3.connect(self.db_path)
        connection.execute(
            'CREATE TABLE bukza_orders ('
            'id INTEGER PRIMARY KEY, reservation_at TEXT)'
        )
        connection.execute(
            'CREATE INDEX idx_bukza_orders_reservation_at '
            'ON bukza_orders(reservation_at)'
        )
        connection.execute(
            'CREATE TABLE bukza_order_history ('
            'id INTEGER PRIMARY KEY, order_id TEXT, changed_at TEXT)'
        )
        connection.execute(
            'CREATE INDEX idx_bukza_order_history_order '
            'ON bukza_order_history(order_id, changed_at)'
        )
        connection.execute(
            "INSERT INTO bukza_orders VALUES (1, '2026-09-02')"
        )
        connection.execute(
            "INSERT INTO bukza_order_history VALUES (1, 'order-1', '2026-09-02')"
        )
        connection.commit()
        connection.close()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_healthy_database_is_not_rewritten(self):
        result = repair.repair_sqlite_integrity(self.db_path, apply=True)

        self.assertEqual(result['status'], 'healthy')
        self.assertFalse(result['applied'])
        self.assertFalse((self.db_path.parent / 'backups').exists())

    def test_unknown_damage_is_not_repaired_automatically(self):
        with patch.object(
            repair, 'integrity_issues', return_value=['page 10 is corrupt'],
        ):
            with self.assertRaisesRegex(RuntimeError, 'неизвестные повреждения'):
                repair.repair_sqlite_integrity(self.db_path, apply=True)

        self.assertFalse((self.db_path.parent / 'backups').exists())

    def test_known_repair_creates_backup_and_verifies_result(self):
        known = [
            '*** in database main ***\n'
            'Freelist: size is 1 but should be 2\nPage 10: never used',
            'wrong # of entries in index idx_bukza_order_history_order',
            'wrong # of entries in index idx_bukza_orders_reservation_at',
        ]
        with patch.object(
            repair, 'integrity_issues', side_effect=[known, []],
        ):
            result = repair.repair_sqlite_integrity(
                self.db_path, apply=True,
            )

        self.assertEqual(result['status'], 'repaired')
        self.assertTrue(result['applied'])
        self.assertEqual(result['rows_before'], result['rows_after'])
        self.assertTrue(Path(result['backup']).is_file())


if __name__ == '__main__':
    unittest.main()
