import importlib.util
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


def load_sheets_module():
    telebot = types.ModuleType("telebot")
    telebot.__all__ = []
    modules = {
        "telebot": telebot,
        "pygsheets": types.ModuleType("pygsheets"),
        "pandas": types.ModuleType("pandas"),
        "pytz": types.ModuleType("pytz"),
    }
    with patch.dict(sys.modules, modules):
        spec = importlib.util.spec_from_file_location("sheets_under_test", "sheets.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


class SheetsSqlTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sheets = load_sheets_module()

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.sql"
        conn = sqlite3.connect(self.db_path)
        conn.execute("""CREATE TABLE afterparty (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dt_rep TEXT, who TEXT, club TEXT, desc TEXT, status TEXT
        )""")
        conn.commit()
        conn.close()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_user_text_with_quotes_is_saved_as_data(self):
        real_connect = sqlite3.connect
        description = "Клиент сказал 'да'; DROP TABLE afterparty; --"

        with patch.object(self.sheets.sqlite3, "connect", side_effect=lambda _path: real_connect(self.db_path)):
            self.sheets.Insert("afterparty", "2026-07-21", "@employee", "Марьино", description)

        conn = real_connect(self.db_path)
        row = conn.execute("SELECT desc FROM afterparty").fetchone()
        table = conn.execute("SELECT name FROM sqlite_master WHERE name='afterparty'").fetchone()
        conn.close()
        self.assertEqual(row[0], description)
        self.assertEqual(table[0], "afterparty")

    def test_unknown_table_is_rejected(self):
        with self.assertRaises(ValueError):
            self.sheets.validate_table("afterparty; DROP TABLE users_new")

    def test_update_table_exports_only_last_three_months(self):
        real_connect = sqlite3.connect
        conn = real_connect(self.db_path)
        old_date, recent_date = conn.execute(
            "SELECT date('now', '+3 hours', '-4 months'), "
            "date('now', '+3 hours', '-1 month')"
        ).fetchone()
        conn.executemany(
            "INSERT INTO afterparty (dt_rep, who, club, desc, status) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (old_date, "@old", "Марьино", "old", "Одобрено"),
                (recent_date, "@recent", "Марьино", "recent", "Одобрено"),
            ],
        )
        conn.commit()
        conn.close()

        worksheet = unittest.mock.Mock(rows=100)
        worksheet.get_values.return_value = unittest.mock.Mock()
        spreadsheet = unittest.mock.Mock()
        spreadsheet.worksheet_by_title.return_value = worksheet
        client = unittest.mock.Mock()
        client.open.return_value = spreadsheet

        with patch.object(
            self.sheets.sqlite3,
            "connect",
            side_effect=lambda _path: real_connect(self.db_path),
        ), patch.object(
            self.sheets.pygsheets,
            "authorize",
            return_value=client,
            create=True,
        ):
            self.sheets.update_table("afterparty")

        exported = worksheet.update_values.call_args.args[1]
        self.assertEqual(len(exported), 1)
        self.assertEqual(exported[0][1], recent_date)
        self.assertEqual(exported[0][2], "@recent")


if __name__ == "__main__":
    unittest.main()
