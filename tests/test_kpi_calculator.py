import sqlite3
import tempfile
import unittest
from pathlib import Path

import kpi_calculator


def create_legacy_schema(db_path):
    conn = sqlite3.connect(db_path)
    conn.executescript(
        '''
        CREATE TABLE users (
            ID INTEGER PRIMARY KEY,
            login TEXT,
            first_name TEXT,
            second_name TEXT,
            nick_name TEXT,
            status INTEGER
        );
        CREATE TABLE shifts (
            shift_second_name TEXT,
            shift_first_name TEXT,
            dt_shift TEXT,
            club TEXT,
            dur REAL,
            source TEXT,
            shift_login TEXT
        );
        CREATE TABLE anketi (
            ID INTEGER PRIMARY KEY,
            id_ank INTEGER,
            dt_ank TEXT,
            club_ank TEXT
        );
        CREATE TABLE afterparty (
            ID INTEGER PRIMARY KEY,
            dt_rep TEXT,
            who TEXT,
            club TEXT,
            desc TEXT,
            status TEXT
        );
        CREATE TABLE birthday (
            ID INTEGER PRIMARY KEY,
            dt_rep TEXT,
            who TEXT,
            club TEXT,
            desc TEXT,
            status TEXT
        );
        CREATE TABLE initiative (
            ID INTEGER PRIMARY KEY,
            dt_rep TEXT,
            who TEXT,
            club TEXT,
            desc TEXT,
            status TEXT
        );
        CREATE TABLE sert (
            ID INTEGER PRIMARY KEY,
            num TEXT,
            d_rep TEXT,
            who TEXT,
            bonus REAL
        );
        CREATE TABLE abik (
            ID INTEGER PRIMARY KEY,
            num TEXT,
            d_rep TEXT,
            who TEXT,
            bonus REAL
        );
        CREATE TABLE bs (
            ID INTEGER PRIMARY KEY,
            id_bs INTEGER,
            dt_bs TEXT,
            name_bs TEXT
        );
        CREATE TABLE penalty (
            ID INTEGER PRIMARY KEY,
            dt TEXT,
            name TEXT,
            desc TEXT
        );
        CREATE TABLE reviews (
            ID INTEGER PRIMARY KEY,
            who TEXT,
            d_rep TEXT,
            amount REAL,
            desc TEXT
        );
        '''
    )
    conn.commit()
    conn.close()


class KpiCalculatorTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / 'kpi.sqlite')
        create_legacy_schema(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_calculates_sheet_formula_and_weighted_shifts(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO users VALUES (1, '@Alice', 'Алиса', 'Иванова', 'Алиса', 0)"
        )
        conn.executemany(
            "INSERT INTO shifts VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ('Иванова', 'Алиса', '2026-07-06', 'Дмитровка', 6.0, 'test', '@Alice'),
                ('Иванова', 'Алиса', '2026-07-06', 'Дмитровка', 3.0, 'test', '@Alice'),
                ('Иванова', 'Алиса', '2026-07-11', 'Дмитровка', 6.0, 'test', '@Alice'),
            ],
        )
        conn.executemany(
            "INSERT INTO anketi VALUES (?, ?, ?, ?)",
            [
                (1, 101, '2026-07-06', 'Дмитровка'),
                (2, 102, '2026-07-06', 'Дмитровка'),
            ],
        )
        conn.execute(
            "INSERT INTO afterparty VALUES (1, '2026-07-06', '@Alice', 'Дмитровка', '', 'Одобрено')"
        )
        conn.execute(
            "INSERT INTO initiative VALUES (1, '2026-07-06', '@Alice', 'Дмитровка', '', 'Одобрено')"
        )
        conn.execute(
            "INSERT INTO birthday VALUES (1, '2026-07-06', '@Alice', 'Дмитровка', '', 'Одобрено')"
        )
        conn.execute(
            "INSERT INTO sert VALUES (1, '3000', '2026-07-06', '@Alice', 1000)"
        )
        conn.execute(
            "INSERT INTO abik VALUES (1, '100', '2026-07-06', '@Alice', 5000)"
        )
        conn.execute("INSERT INTO bs VALUES (1, 1, '2026-07-06', '@Alice')")
        conn.execute(
            "INSERT INTO reviews VALUES (1, '@Alice', '2026-07-06', 1, '')"
        )
        conn.execute(
            "INSERT INTO penalty VALUES (1, '2026-07-06', '@Alice', 'Опоздание')"
        )
        conn.commit()
        conn.close()

        kpi_calculator.initialize_kpi_calculation_schema(self.db_path)
        kpi_calculator.set_monthly_stream(
            '@Alice',
            '2026-07-01',
            True,
            '@manager',
            db_path=self.db_path,
        )
        row = kpi_calculator.calculate_monthly_kpi(
            '2026-07-15',
            db_path=self.db_path,
            employee_logins=['@Alice'],
        )[0]

        self.assertAlmostEqual(row['shifts'], 2.5)
        self.assertAlmostEqual(row['weighted_shifts'], 3.5)
        self.assertEqual(row['forms'], 2)
        self.assertEqual(row['penalties'], 1)
        self.assertTrue(row['stream'])
        self.assertEqual(row['birthdays'], 1)
        self.assertAlmostEqual(row['initiatives_pct'], 0.1)
        self.assertAlmostEqual(row['total_pct'], 2.4133333333)
        self.assertAlmostEqual(row['weighted_pct'], row['total_pct'] * 2.5 / 3.5)
        self.assertAlmostEqual(row['amount'], 1345)
        self.assertEqual(row['rank'], 1)
        self.assertEqual(row['zone'], '🟢')

    def test_zones_are_relative_to_average_for_employees_with_shifts(self):
        conn = sqlite3.connect(self.db_path)
        users = [
            (1, '@green', 'Green', 'User', 'Green', 0),
            (2, '@yellow', 'Yellow', 'User', 'Yellow', 0),
            (3, '@red', 'Red', 'User', 'Red', 0),
            (4, '@idle', 'Idle', 'User', 'Idle', 0),
        ]
        conn.executemany("INSERT INTO users VALUES (?, ?, ?, ?, ?, ?)", users)
        for user_id, login, first_name, second_name, _nickname, _status in users[:3]:
            conn.execute(
                "INSERT INTO shifts VALUES (?, ?, '2026-07-06', 'Дмитровка', 6, 'test', ?)",
                (second_name, first_name, login),
            )
        conn.executemany(
            "INSERT INTO reviews VALUES (?, ?, '2026-07-06', ?, '')",
            [
                (1, '@green', 0.9),
                (2, '@yellow', 0.5625),
                (3, '@red', 0.375),
            ],
        )
        conn.commit()
        conn.close()

        rows = kpi_calculator.calculate_monthly_kpi(
            '2026-07',
            db_path=self.db_path,
        )
        by_login = {row['login']: row for row in rows}

        self.assertEqual(by_login['@green']['zone'], '🟢')
        self.assertEqual(by_login['@yellow']['zone'], '🟡')
        self.assertEqual(by_login['@red']['zone'], '🔴')
        self.assertEqual(by_login['@idle']['zone'], '⚪')
        self.assertEqual(by_login['@green']['rank'], 1)
        self.assertEqual(by_login['@yellow']['rank'], 2)
        self.assertEqual(by_login['@red']['rank'], 3)
        self.assertIsNone(by_login['@idle']['rank'])

    def test_comparison_ignores_legacy_rank_for_employee_without_shifts(self):
        differences = kpi_calculator.compare_with_sheet(
            [{'login': '@idle', 'shifts': 0, 'rank': None}],
            [{'login': '@idle', 'shifts': 0, 'rank': 19}],
        )

        self.assertEqual(differences, [])

    def test_month_can_be_closed_and_reopened_without_locking_history(self):
        kpi_calculator.initialize_kpi_calculation_schema(self.db_path)

        closed = kpi_calculator.set_month_status(
            '2026-07',
            True,
            '@manager',
            db_path=self.db_path,
        )
        reopened = kpi_calculator.set_month_status(
            '2026-07',
            False,
            '@manager',
            db_path=self.db_path,
        )

        self.assertTrue(closed['is_closed'])
        self.assertFalse(reopened['is_closed'])
        self.assertEqual(reopened['updated_by_login'], '@manager')

    def test_penalty_cancellation_keeps_audit_and_removes_impact(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO users VALUES (1, '@employee', 'Test', 'User', 'Tester', 0)"
        )
        conn.commit()
        conn.close()

        penalty_id = kpi_calculator.add_penalty(
            '@employee',
            '2026-07',
            'Опоздание',
            '@manager',
            db_path=self.db_path,
        )
        self.assertTrue(kpi_calculator.cancel_penalty(
            penalty_id,
            '@owner',
            'Ошибочное начисление',
            db_path=self.db_path,
        ))
        row = kpi_calculator.calculate_monthly_kpi(
            '2026-07',
            db_path=self.db_path,
            employee_logins=['@employee'],
        )[0]
        self.assertEqual(row['penalties'], 0)
        self.assertEqual(row['penalty_impact'], 0)

        conn = sqlite3.connect(self.db_path)
        audit = conn.execute(
            '''
            SELECT status, created_by_login, cancelled_by_login, cancel_reason
            FROM kpi_penalties WHERE id=?
            ''',
            (penalty_id,),
        ).fetchone()
        conn.close()
        self.assertEqual(
            audit,
            ('cancelled', '@manager', '@owner', 'Ошибочное начисление'),
        )

    def test_legacy_penalty_migration_is_idempotent(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO penalty VALUES (7, '2026-06-15', '@employee', 'Старый штраф')"
        )
        conn.commit()
        conn.close()

        kpi_calculator.initialize_kpi_calculation_schema(self.db_path)
        kpi_calculator.initialize_kpi_calculation_schema(self.db_path)

        conn = sqlite3.connect(self.db_path)
        rows = conn.execute(
            "SELECT employee_login, period_month, reason, source_key FROM kpi_penalties"
        ).fetchall()
        conn.close()
        self.assertEqual(
            rows,
            [('@employee', '2026-06-01', 'Старый штраф', 'legacy-db:7')],
        )


if __name__ == '__main__':
    unittest.main()
