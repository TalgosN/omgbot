import sqlite3
import unittest

import sql_scripts


class SqlScriptsTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute(
            "CREATE TABLE users_new (login TEXT, first_name TEXT, second_name TEXT)"
        )
        self.conn.execute(
            "CREATE TABLE shifts (shift_second_name TEXT, shift_first_name TEXT, "
            "dt_shift TEXT, club TEXT, dur REAL, shift_login TEXT)"
        )
        self.conn.execute("CREATE TABLE anketi (ID TEXT, dt_ank TEXT, club_ank TEXT)")
        self.conn.execute(
            "CREATE TABLE birthday (id INTEGER, dt_rep TEXT, who TEXT, status TEXT)"
        )
        self.conn.execute("CREATE TABLE afterparty (id INTEGER, dt_rep TEXT, who TEXT)")
        self.conn.execute("CREATE TABLE sert (d_rep TEXT, who TEXT, bonus REAL)")
        self.conn.execute("CREATE TABLE abik (d_rep TEXT, who TEXT, bonus REAL)")
        self.conn.execute("CREATE TABLE initiative (id INTEGER, dt_rep TEXT, who TEXT)")
        self.conn.execute("CREATE TABLE bs (id_bs INTEGER, dt_bs TEXT, name_bs TEXT)")
        self.conn.execute("CREATE TABLE penalty (ID INTEGER, dt TEXT, name TEXT)")
        self.conn.execute("CREATE TABLE reviews (d_rep TEXT, who TEXT, amount REAL)")

    def tearDown(self):
        self.conn.close()

    def test_questionnaires_use_shift_login_for_renamed_employee(self):
        self.conn.execute(
            "INSERT INTO users_new VALUES (?, ?, ?)",
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
        raw_rows = self.conn.execute(sql_scripts.records).fetchall()
        shift_rows = self.conn.execute(sql_scripts.shifts).fetchall()

        self.assertEqual(data_rows, [("2026-07-20", "@maxon", "Анкеты", 1.0)])
        self.assertEqual(raw_rows, [("@maxon", "Анкеты", 1.0)])
        self.assertEqual(shift_rows, [("2026-07-31", "@maxon", 12.0, 2.0)])


if __name__ == "__main__":
    unittest.main()
