import sqlite3
import unittest

import sql_scripts


class SqlScriptsTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute(
            "CREATE TABLE users (login TEXT, first_name TEXT, second_name TEXT)"
        )
        self.conn.execute(
            "CREATE TABLE shifts (shift_second_name TEXT, shift_first_name TEXT, "
            "dt_shift TEXT, club TEXT, dur REAL, shift_login TEXT)"
        )
        self.conn.execute("CREATE TABLE anketi (ID TEXT, dt_ank TEXT, club_ank TEXT)")
        self.conn.execute(
            "CREATE TABLE birthday (id INTEGER, dt_rep TEXT, who TEXT, status TEXT)"
        )
        self.conn.execute(
            "CREATE TABLE afterparty (id INTEGER, dt_rep TEXT, who TEXT, status TEXT)"
        )
        self.conn.execute("CREATE TABLE sert (d_rep TEXT, who TEXT, bonus REAL)")
        self.conn.execute("CREATE TABLE abik (d_rep TEXT, who TEXT, bonus REAL)")
        self.conn.execute(
            "CREATE TABLE initiative (id INTEGER, dt_rep TEXT, who TEXT, status TEXT)"
        )
        self.conn.execute("CREATE TABLE bs (id_bs INTEGER, dt_bs TEXT, name_bs TEXT)")
        self.conn.execute("CREATE TABLE penalty (ID INTEGER, dt TEXT, name TEXT)")
        self.conn.execute("CREATE TABLE reviews (d_rep TEXT, who TEXT, amount REAL)")

    def tearDown(self):
        self.conn.close()

    def test_questionnaires_use_shift_login_for_renamed_employee(self):
        self.conn.execute(
            "INSERT INTO users VALUES (?, ?, ?)",
            ("@maxon", "Максон", "Песков"),
        )
        self.conn.executemany(
            "INSERT INTO shifts VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("Песков", "Максим", "2026-07-20 0:00:00", "Марьино", 6.0, "@maxon"),
                ("Песков", "Максон", "2026-07-20", "Марьино", 6.0, "@maxon"),
            ],
        )
        self.conn.execute(
            "INSERT INTO anketi VALUES (?, ?, ?)",
            ("anketa-1", "2026-07-20", "Марьино"),
        )

        data_rows = self.conn.execute(sql_scripts.union).fetchall()
        raw_rows = self.conn.execute(sql_scripts.sheets_records).fetchall()
        shift_rows = self.conn.execute(sql_scripts.sheets_shifts).fetchall()

        self.assertEqual(data_rows, [("2026-07-20", "@maxon", "Анкеты", 1.0)])
        self.assertEqual(raw_rows, [("@maxon", "Анкеты", 1.0)])
        self.assertEqual(shift_rows, [("2026-07-31", "@maxon", 12.0, 2.0)])

    def test_birthday_is_not_part_of_kpi_data(self):
        self.conn.execute(
            "INSERT INTO birthday VALUES (?, ?, ?, ?)",
            (1, "2026-07-20", "@employee", "Одобрено"),
        )

        data_rows = self.conn.execute(sql_scripts.union).fetchall()

        self.assertFalse(any(row[2] == "ДР" for row in data_rows))

    def test_google_sheet_queries_keep_only_last_three_months(self):
        old_date, recent_date = self.conn.execute(
            "SELECT date('now', '+3 hours', '-4 months'), "
            "date('now', '+3 hours', '-1 month')"
        ).fetchone()
        self.conn.execute(
            "INSERT INTO users VALUES (?, ?, ?)",
            ("@employee", "Иван", "Иванов"),
        )
        self.conn.executemany(
            "INSERT INTO shifts VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("Иванов", "Иван", old_date, "Марьино", 6.0, "@employee"),
                ("Иванов", "Иван", recent_date, "Марьино", 6.0, "@employee"),
            ],
        )
        self.conn.executemany(
            "INSERT INTO afterparty VALUES (?, ?, ?, ?)",
            [
                (1, old_date, "@employee", "Одобрено"),
                (2, recent_date, "@employee", "Одобрено"),
            ],
        )

        sheet_shift_rows = self.conn.execute(sql_scripts.sheets_shifts_ext).fetchall()
        sheet_data_rows = self.conn.execute(sql_scripts.sheets_union).fetchall()
        sheet_month_rows = self.conn.execute(sql_scripts.sheets_shifts).fetchall()
        sheet_raw_rows = self.conn.execute(sql_scripts.sheets_records).fetchall()

        self.assertEqual(len(sheet_shift_rows), 1)
        self.assertEqual(sheet_shift_rows[0][2], recent_date)
        self.assertEqual({row[0] for row in sheet_data_rows}, {recent_date})
        self.assertEqual(len(sheet_month_rows), 1)
        self.assertEqual(sheet_month_rows[0][2:], (6.0, 1.0))
        self.assertEqual(len(sheet_raw_rows), 2)

        all_shift_dates = {
            row[2] for row in self.conn.execute(sql_scripts.shifts_ext).fetchall()
        }
        all_kpi_dates = {
            row[0] for row in self.conn.execute(sql_scripts.union).fetchall()
        }
        self.assertEqual(all_shift_dates, {old_date, recent_date})
        self.assertEqual(all_kpi_dates, {old_date, recent_date})

    def test_rejected_local_actions_are_excluded_from_kpi(self):
        self.conn.executemany(
            "INSERT INTO afterparty VALUES (?, ?, ?, ?)",
            [
                (1, "2026-07-20", "@employee", "Одобрено"),
                (2, "2026-07-20", "@employee", "Отклонено"),
            ],
        )
        self.conn.executemany(
            "INSERT INTO initiative VALUES (?, ?, ?, ?)",
            [
                (1, "2026-07-20", "@employee", "Одобрено"),
                (2, "2026-07-20", "@employee", "Отклонено"),
            ],
        )

        rows = self.conn.execute(sql_scripts.union).fetchall()
        values = {row[2]: row[3] for row in rows}

        self.assertEqual(values["Продления"], 1)
        self.assertEqual(values["Инициативы"], 1)


if __name__ == "__main__":
    unittest.main()
