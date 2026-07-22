import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
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
            [
                ('@one', 'Первый', 'Сотрудник'),
                ('@two', 'Второй', 'Сотрудник'),
                ('@three', 'Третий', 'Без Ставки'),
            ],
        )
        conn.executemany(
            'INSERT INTO shifts VALUES (?, ?, ?, ?, ?, ?, ?)',
            [
                ('Первый', 'Сотрудник', '2026-07-20', 'Ленинский', 8, 'legacy_shifton', '@one'),
                ('Первый', 'Сотрудник', '2026-07-21', 'Коллцентр', 5.5, 'omg_shift', '@one'),
                ('Третий', 'Без Ставки', '2026-07-20', 'Ленинский', 8, 'omg_shift', '@three'),
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
        self.assertNotIn('@three', data)

    def test_reports_employee_skipped_because_rate_is_missing(self):
        with patch.object(finance, 'PAYROLL_DB_PATH', self.db_path):
            data, skipped = finance._collect_payroll_report_data(
                '2026-07-20 00:00:00', '2026-07-22 00:00:00'
            )

        self.assertNotIn('@three', data)
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0]['Логин'], '@three')
        self.assertEqual(skipped[0]['Проблемы'], [('Ленинский', '2026-07-20')])

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

    def test_pay_report_sends_separate_missing_rate_warning(self):
        class Bot:
            def __init__(self):
                self.documents = 0
                self.messages = []

            def send_document(self, _chat_id, _document):
                self.documents += 1

            def send_message(self, _chat_id, text):
                self.messages.append(text)

        skipped = [{
            'Логин': '@three',
            'Имя': 'Третий Без Ставки',
            'Проблемы': [('Ленинский', '2099-01-01')],
        }]
        bot = Bot()
        message = SimpleNamespace(chat=SimpleNamespace(id=1))

        with patch.object(finance, '_collect_payroll_report_data', return_value=({}, skipped)), \
                patch.object(finance, 'finance', return_value=None):
            finance.pay_report(
                '2099-01-01T00:00:00', '2099-01-08T00:00:00', message, bot
            )

        self.assertEqual(bot.documents, 1)
        self.assertEqual(len(bot.messages), 1)
        self.assertIn('@three', bot.messages[0])
        self.assertIn('Ленинский', bot.messages[0])

    def test_salary_menu_has_previous_week_button(self):
        class Bot:
            def __init__(self):
                self.markup = None

            def send_message(self, _chat_id, _text, reply_markup=None):
                if reply_markup is not None:
                    self.markup = reply_markup

            def register_next_step_handler(self, *_args):
                return None

        bot = Bot()
        message = SimpleNamespace(
            text='👨🏻‍💻 ЗП за период',
            chat=SimpleNamespace(id=1),
        )

        with patch.object(finance, 'require_role', return_value=True):
            finance.func_fin(message, bot)

        buttons = [button['text'] for row in bot.markup.keyboard for button in row]
        self.assertIn('Текущая неделя', buttons)
        self.assertIn('Прошлая неделя', buttons)


if __name__ == '__main__':
    unittest.main()
