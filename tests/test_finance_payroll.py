import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from openpyxl import load_workbook

import finance


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class PayrollReportTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / 'payroll.sqlite')
        conn = sqlite3.connect(self.db_path)
        conn.executescript('''
            CREATE TABLE users (
                login TEXT, second_name TEXT, first_name TEXT
            );
            CREATE TABLE shifts (
                shift_second_name TEXT, shift_first_name TEXT, dt_shift DATE,
                club TEXT, dur REAL, source TEXT, shift_login TEXT
            );
            CREATE TABLE payroll_rates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                login TEXT NOT NULL,
                club TEXT NOT NULL,
                hourly_rate REAL NOT NULL,
                valid_from DATE NOT NULL,
                valid_to DATE,
                source TEXT,
                UNIQUE(login, club, valid_from)
            );
            CREATE TABLE double (ID INTEGER PRIMARY KEY, who TEXT, d_rep DATE, amount REAL, desc TEXT);
            CREATE TABLE birthday (ID INTEGER PRIMARY KEY, dt_rep DATE, who TEXT, club TEXT, desc TEXT, status TEXT);
            CREATE TABLE autosim (ID INTEGER PRIMARY KEY, who TEXT, d_rep DATE, amount REAL);
            CREATE TABLE activation (ID INTEGER PRIMARY KEY, who TEXT, d_rep DATE, amount REAL);
        ''')
        conn.executemany(
            'INSERT INTO users VALUES (?, ?, ?)',
            [('@one', 'Первый', 'Сотрудник'), ('@two', 'Второй', 'Сотрудник')],
        )
        conn.executemany(
            'INSERT INTO shifts VALUES (?, ?, ?, ?, ?, ?, ?)',
            [
                ('Первый', 'Сотрудник', '2026-07-20', 'Ленинский', 8, 'legacy_shifton', '@one'),
                ('Первый', 'Сотрудник', '2026-07-21', 'Коллцентр', 5.5, 'omg_shift', '@one'),
            ],
        )
        conn.executemany(
            'INSERT INTO payroll_rates (login, club, hourly_rate, valid_from, source) VALUES (?, ?, ?, ?, ?)',
            [
                ('@one', '*', 100, '2024-01-01', 'legacy_shifton'),
                ('@one', 'Коллцентр', 150, '2024-01-01', 'legacy_shifton'),
                ('@two', '*', 200, '2024-01-01', 'legacy_shifton'),
            ],
        )
        conn.executemany(
            'INSERT INTO double VALUES (?, ?, ?, ?, ?)',
            [(1, '@one', '2026-07-20', 1, ''), (2, '@two', '2026-07-20', 2, '')],
        )
        conn.execute(
            "INSERT INTO birthday VALUES (1, '2026-07-20', '@one', 'Ленинский', '', 'Одобрено')"
        )
        conn.execute("INSERT INTO autosim VALUES (1, '@one', '2026-07-20', 50)")
        conn.execute("INSERT INTO activation VALUES (1, '@one', '2026-07-21', 25.5)")
        conn.commit()
        conn.close()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_calculates_all_local_payroll_sources(self):
        with patch.object(finance, 'PAYROLL_DB_PATH', self.db_path):
            data = finance.get_data_pay_report(
                '2026-07-20 00:00:00', '2026-07-22 00:00:00'
            )

        self.assertEqual(data['@one']['Клубы']['Ленинский'], 1400)
        self.assertEqual(data['@one']['Клубы']['Коллцентр'], 825)
        self.assertEqual(data['@one']['Автосим'], 50)
        self.assertEqual(data['@one']['Активации'], 25.5)
        self.assertEqual(data['@two']['Двойные без клуба'], 400)

    def test_expands_template_before_manual_rows(self):
        workbook = load_workbook('Reports/Шаблон_ЗП.xlsx')
        worksheet = workbook['ЗП']

        finance._prepare_payroll_rows(worksheet, 9)
        finance._write_payroll_formulas(worksheet)

        self.assertEqual(finance._find_excel_row(worksheet, 'Возмещение / Доп'), 11)
        self.assertEqual(worksheet['AF2'].value, '=SUM(B2:AE2)')
        self.assertEqual(worksheet['AH2'].value, '=SUM(B2:AE2)-AG2')
        self.assertEqual(worksheet['AF11'].value, '=SUM(B11:AE11)')
        self.assertEqual(worksheet['AF15'].value, '=SUM(B15:AE15)')
        self.assertEqual(worksheet['AH15'].value, '=AF15-AG16')

    def test_fetches_and_saves_only_missing_day(self):
        empty_db = str(Path(self.temp_dir.name) / 'fallback.sqlite')
        conn = sqlite3.connect(empty_db)
        conn.execute('''
            CREATE TABLE shifts (
                shift_second_name TEXT, shift_first_name TEXT, dt_shift DATE,
                club TEXT, dur REAL, source TEXT, shift_login TEXT
            )
        ''')
        response = FakeResponse({
            'ok': True,
            'locations': [{
                'title': 'Ленинский',
                'shifts': [
                    {'employee': 'Первый Сотрудник', 'telegram': '@one', 'start': '09:00', 'end': '12:00'},
                    {'employee': 'Первый Сотрудник', 'telegram': '@one', 'start': '13:00', 'end': '16:00'},
                ],
            }],
        })

        with patch.object(finance.requests, 'get', return_value=response):
            finance._fetch_missing_payroll_shifts(
                conn, datetime(2026, 7, 20), datetime(2026, 7, 21)
            )

        rows = conn.execute('SELECT dur, source FROM shifts ORDER BY rowid').fetchall()
        conn.close()
        self.assertEqual(rows, [(3, 'omg_shift'), (3, 'omg_shift')])


if __name__ == '__main__':
    unittest.main()
