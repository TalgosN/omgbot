import os
import sqlite3
import tempfile
import unittest

import db_migrations


class TableNamesMigrationTest(unittest.TestCase):
    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix='.sql')
        os.close(handle)

    def tearDown(self):
        os.remove(self.db_path)

    def tables(self, conn):
        return {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }

    def test_legacy_tables_are_replaced_once(self):
        conn = sqlite3.connect(self.db_path)
        conn.executescript('''
            CREATE TABLE users (ID INTEGER PRIMARY KEY, login TEXT, name TEXT, chatid TEXT);
            CREATE TABLE users_new (
                ID INTEGER PRIMARY KEY, login TEXT, first_name TEXT, second_name TEXT,
                nick_name TEXT, bday TEXT, phone TEXT, email TEXT, status INTEGER,
                chatid TEXT
            );
            INSERT INTO users VALUES (1, '@legacy', 'Legacy', '10');
            INSERT INTO users_new VALUES (
                7, '@current', 'Иван', 'Иванов', 'Ваня', NULL, NULL, NULL, 2, '70'
            );
            CREATE TABLE admins (ID INTEGER PRIMARY KEY, login TEXT, name TEXT);
            CREATE TABLE schema_migrations (name TEXT PRIMARY KEY, applied_at TEXT);
            CREATE TABLE broadcasts (
                ID INTEGER PRIMARY KEY, text TEXT, photo TEXT, trep TEXT,
                freq INTEGER, status INTEGER
            );
            CREATE TABLE broadcasts_new (
                id INTEGER PRIMARY KEY, text TEXT, photo TEXT, time TEXT,
                freq_type TEXT, freq_days TEXT, status INTEGER
            );
            INSERT INTO broadcasts VALUES (1, 'old', NULL, '10:00', 1, 1);
            INSERT INTO broadcasts_new VALUES (5, 'current', NULL, '11:00', 'daily', '', 1);
        ''')
        conn.commit()
        conn.close()

        actions = db_migrations.migrate_table_names(self.db_path)
        second_actions = db_migrations.migrate_table_names(self.db_path)

        conn = sqlite3.connect(self.db_path)
        tables = self.tables(conn)
        user = conn.execute('SELECT ID, login, status FROM users').fetchone()
        broadcast = conn.execute('SELECT id, text, time FROM broadcasts').fetchone()
        version = conn.execute('PRAGMA user_version').fetchone()[0]
        conn.close()

        self.assertIn('users_new переименована в users', actions)
        self.assertIn('broadcasts_new переименована в broadcasts', actions)
        self.assertEqual(second_actions, [])
        self.assertNotIn('users_new', tables)
        self.assertNotIn('broadcasts_new', tables)
        self.assertNotIn('admins', tables)
        self.assertNotIn('schema_migrations', tables)
        self.assertEqual(user, (7, '@current', 2))
        self.assertEqual(broadcast, (5, 'current', '11:00'))
        self.assertEqual(version, db_migrations.TABLE_NAMES_VERSION)

    def test_unexpected_old_only_schema_is_not_deleted(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            'CREATE TABLE users (ID INTEGER PRIMARY KEY, login TEXT, name TEXT, chatid TEXT)'
        )
        conn.execute("INSERT INTO users VALUES (1, '@legacy', 'Legacy', '10')")
        conn.commit()
        conn.close()

        with self.assertRaisesRegex(RuntimeError, 'неожиданную схему'):
            db_migrations.migrate_table_names(self.db_path)

        conn = sqlite3.connect(self.db_path)
        self.assertEqual(conn.execute('SELECT COUNT(*) FROM users').fetchone()[0], 1)
        self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0], 0)
        conn.close()


if __name__ == '__main__':
    unittest.main()
