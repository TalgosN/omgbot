import os
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import permissions


def update(user_id):
    return SimpleNamespace(from_user=SimpleNamespace(id=user_id, is_bot=False))


class PermissionsTest(unittest.TestCase):
    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix='.sql')
        os.close(handle)
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            '''CREATE TABLE users (
                   ID INTEGER PRIMARY KEY AUTOINCREMENT,
                   login TEXT, first_name TEXT, second_name TEXT, nick_name TEXT,
                   bday TEXT, phone TEXT, email TEXT, status INTEGER, chatid TEXT
               )'''
        )
        conn.executemany(
            'INSERT INTO users (login, status, chatid) VALUES (?, ?, ?)',
            [
                ('@employee', 0, '10'),
                ('@technician', 1, '15'),
                ('@manager', 2, '20'),
                ('@owner', 3, '30'),
            ],
        )
        conn.commit()
        conn.close()
        self.db_patch = patch.object(permissions, 'DB_PATH', self.db_path)
        self.db_patch.start()

    def tearDown(self):
        self.db_patch.stop()
        os.remove(self.db_path)

    def test_initialization_preserves_current_roles_and_creates_audit(self):
        permissions.initialize_permissions_schema()
        permissions.initialize_permissions_schema()
        conn = sqlite3.connect(self.db_path)
        statuses = dict(conn.execute('SELECT login, status FROM users'))
        audit_count = conn.execute('SELECT COUNT(*) FROM role_audit').fetchone()[0]
        migration_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        ).fetchone()
        conn.close()
        self.assertEqual(statuses, {
            '@employee': 0,
            '@technician': 1,
            '@manager': 2,
            '@owner': 3,
        })
        self.assertEqual(audit_count, 0)
        self.assertIsNone(migration_table)

    def test_permissions_use_numeric_telegram_id(self):
        permissions.initialize_permissions_schema()
        bot = Mock()
        self.assertIsNotNone(permissions.require_role(update(30), bot, permissions.ROLE_OWNER))
        self.assertIsNotNone(permissions.require_role(update(20), bot, permissions.ROLE_MANAGER))
        self.assertIsNone(permissions.require_role(update(20), bot, permissions.ROLE_OWNER))
        self.assertIsNone(permissions.require_role(update(10), bot, permissions.ROLE_TECHNICIAN))

    def test_role_change_is_audited_and_last_owner_is_protected(self):
        permissions.initialize_permissions_schema()
        conn = sqlite3.connect(self.db_path)
        employee_id = conn.execute("SELECT ID FROM users WHERE login='@employee'").fetchone()[0]
        conn.close()
        permissions.change_role(update(30), employee_id, permissions.ROLE_TECHNICIAN)

        conn = sqlite3.connect(self.db_path)
        status = conn.execute('SELECT status FROM users WHERE ID=?', (employee_id,)).fetchone()[0]
        audit_count = conn.execute(
            "SELECT COUNT(*) FROM role_audit WHERE actor_login='@owner'"
        ).fetchone()[0]
        owner_id = conn.execute("SELECT ID FROM users WHERE login='@owner'").fetchone()[0]
        conn.close()
        self.assertEqual(status, permissions.ROLE_TECHNICIAN)
        self.assertEqual(audit_count, 1)
        with self.assertRaisesRegex(ValueError, 'последнего'):
            permissions.change_role(update(30), owner_id, permissions.ROLE_MANAGER)


if __name__ == '__main__':
    unittest.main()
