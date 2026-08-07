import importlib.util
import locale
import sqlite3
import sys
import tempfile
import threading
import time
import types
import unittest
from datetime import timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch


def load_rasp_module():
    telebot = types.ModuleType("telebot")
    telebot.__all__ = []
    sheets = types.ModuleType("sheets")
    sheets.__all__ = []
    constants = types.ModuleType("constants")
    constants.__all__ = ["SHIFTON_API_URL", "SHIFTON_API_TOKEN"]
    constants.SHIFTON_API_URL = "http://shifton.test"
    constants.SHIFTON_API_TOKEN = "test-token"
    weather = types.ModuleType("weather")
    weather.get_weather = lambda: ""
    pytz = types.ModuleType("pytz")
    pytz.timezone = lambda _name: timezone(timedelta(hours=3))

    modules = {
        "telebot": telebot,
        "sheets": sheets,
        "constants": constants,
        "weather": weather,
        "pytz": pytz,
    }
    with patch.dict(sys.modules, modules), patch.object(locale, "setlocale"):
        spec = importlib.util.spec_from_file_location("rasp_under_test", "rasp.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


class ShiftonNotificationsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rasp = load_rasp_module()

    def test_register_chat_payload(self):
        response = Mock()
        response.json.return_value = {"ok": True}

        with patch.object(self.rasp.requests, "post", return_value=response) as post:
            result = self.rasp.register_shifton_chat("@employee", 12345)

        self.assertEqual(result, {"ok": True})
        post.assert_called_once_with(
            "http://shifton.test/api/bot/register-chat",
            json={"telegram": "@employee", "chatId": 12345},
            headers={
                "Authorization": "Bearer test-token",
                "Content-Type": "application/json",
            },
            timeout=10,
        )

    def test_employee_catalog_request_includes_archived_records(self):
        response = Mock()
        response.json.return_value = {
            'ok': True,
            'employees': [{
                'id': 'employee-1',
                'name': 'Иванов Иван',
                'telegram': 'ivan',
                'startDate': '2026-01-01',
                'endDate': '',
                'archived': False,
            }],
        }
        with patch.object(self.rasp.requests, 'get', return_value=response) as get:
            employees = self.rasp.fetch_shifton_employees(include_archived=True)

        self.assertEqual(employees[0]['telegram'], '@ivan')
        get.assert_called_once_with(
            'http://shifton.test/api/bot/employees',
            params={'includeArchived': 'true'},
            headers={'Authorization': 'Bearer test-token'},
            timeout=15,
        )

    def test_employee_sync_links_users_and_preserves_legacy_rates(self):
        employees = [
            {
                'id': 'employee-1',
                'name': 'Иванов Иван',
                'phone': '+70000000001',
                'telegram': '@ivan',
                'startDate': '2024-01-01',
                'endDate': '',
                'archived': False,
                'position': {
                    'id': 'position-1',
                    'title': 'Администратор',
                    'baseRate': 10,
                    'rateHistory': [
                        {'startDate': '0001-01-01', 'rate': 10},
                        {'startDate': '2026-07-01', 'rate': 250},
                    ],
                },
                'rate': {
                    'current': 250,
                    'source': 'position',
                    'manualOverride': '',
                },
                'bookingPercent': {'enabled': False, 'percent': 0},
            },
            {
                'id': 'employee-2',
                'name': 'Новый Сотрудник',
                'phone': '+70000000002',
                'telegram': '@new_employee',
                'startDate': '2026-08-01',
                'endDate': '',
                'archived': False,
                'position': {'id': 'position-1', 'title': 'Администратор'},
                'rate': {
                    'current': 300,
                    'source': 'employee',
                    'manualOverride': 300,
                },
                'bookingPercent': {'enabled': True, 'percent': 5},
            },
            {
                'id': 'employee-3',
                'name': 'Архивный Сотрудник',
                'phone': '',
                'telegram': None,
                'startDate': '2024-01-01',
                'endDate': '2025-01-01',
                'archived': True,
                'position': {},
                'rate': {},
                'bookingPercent': {},
            },
            {
                'id': 'employee-4',
                'name': 'Заблокированный Сотрудник',
                'phone': '',
                'telegram': '@blocked',
                'startDate': '2026-08-01',
                'endDate': '',
                'archived': False,
                'position': {'id': 'position-1', 'title': 'Администратор'},
                'rate': {
                    'current': 200,
                    'source': 'employee',
                    'manualOverride': 200,
                },
                'bookingPercent': {},
            },
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / 'employees.sqlite')
            conn = sqlite3.connect(db_path)
            conn.executescript('''
                CREATE TABLE users (
                    ID INTEGER PRIMARY KEY, login TEXT, first_name TEXT,
                    second_name TEXT, nick_name TEXT, bday TEXT, phone TEXT,
                    email TEXT, status INTEGER, chatid TEXT
                );
                CREATE TABLE shifts (
                    shift_second_name TEXT, shift_first_name TEXT, dt_shift DATE,
                    club TEXT, dur REAL, source TEXT, shift_login TEXT
                );
                CREATE TABLE payroll_rates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    login TEXT NOT NULL, club TEXT NOT NULL DEFAULT '*',
                    hourly_rate REAL NOT NULL, valid_from DATE NOT NULL,
                    valid_to DATE, source TEXT NOT NULL DEFAULT 'manual',
                    UNIQUE(login, club, valid_from)
                );
                INSERT INTO users VALUES (
                    1, '@ivan', 'Старое', 'Имя', 'Ник', NULL, NULL, NULL, 0, '100'
                );
                INSERT INTO users VALUES (
                    2, '@blocked', 'Заблокированный', 'Сотрудник', 'Блок',
                    NULL, NULL, NULL, -1, '200'
                );
                INSERT INTO shifts VALUES (
                    'Иванов', 'Иван', '2026-07-20', 'Марьино', 8,
                    'omg_shift', '@ivan'
                );
                INSERT INTO payroll_rates
                    (login, club, hourly_rate, valid_from, source)
                VALUES ('@ivan', '*', 150, '2024-08-21', 'legacy_shifton');
            ''')
            conn.commit()
            conn.close()

            account = types.ModuleType('account')

            def apply_identity(connection, employee):
                row = connection.execute(
                    'SELECT * FROM users WHERE lower(login)=lower(?)',
                    (employee.get('telegram'),),
                ).fetchone()
                if not row:
                    return {'status': 'unlinked'}
                connection.execute(
                    '''UPDATE users
                       SET first_name=?, second_name=?, omg_shift_employee_id=?
                       WHERE ID=?''',
                    (
                        employee['name'].split(maxsplit=1)[1],
                        employee['name'].split(maxsplit=1)[0],
                        employee['id'], row['ID'],
                    ),
                )
                return {'status': 'linked', 'changed': True}

            account.apply_omg_employee_identity = apply_identity
            account.sync_google_dependencies = Mock(return_value=[])

            with patch.object(self.rasp, 'SHIFTON_DB_PATH', db_path), \
                    patch.object(self.rasp, 'fetch_shifton_employees', return_value=employees), \
                    patch.dict(sys.modules, {'account': account}):
                first = self.rasp.sync_shifton_employees()
                second = self.rasp.sync_shifton_employees()

            conn = sqlite3.connect(db_path)
            user = conn.execute(
                '''SELECT first_name, second_name, status, omg_shift_employee_id
                   FROM users WHERE ID=1'''
            ).fetchone()
            rates = conn.execute(
                '''SELECT hourly_rate, valid_from, source
                   FROM payroll_rates WHERE login='@ivan'
                   ORDER BY date(valid_from)'''
            ).fetchall()
            snapshot_count = conn.execute(
                'SELECT COUNT(*) FROM omg_shift_employees'
            ).fetchone()[0]
            conn.close()

        self.assertEqual(first['linked'], 2)
        self.assertEqual(first['archived'], 1)
        self.assertEqual(len(first['unlinked']), 1)
        self.assertEqual(len(first['access_mismatches']), 1)
        self.assertEqual(second['rate_rows'], 3)
        self.assertEqual(user, ('Иван', 'Иванов', 0, 'employee-1'))
        self.assertEqual(
            rates,
            [
                (150.0, '2024-08-21', 'legacy_shifton'),
                (250.0, '2026-07-20', 'omg_shift:position'),
            ],
        )
        self.assertEqual(snapshot_count, 4)
        account.sync_google_dependencies.assert_called()

    def test_successful_notification_is_completed(self):
        bot = Mock()
        queue = [
            {"ok": True, "notification": {"id": 17, "chatId": 12345, "text": "Смена изменена"}},
            {"ok": True, "notification": None},
        ]

        with patch.object(self.rasp, "claim_shifton_notification", side_effect=queue), \
                patch.object(self.rasp, "complete_shifton_notification") as complete:
            self.rasp.send_pending_shifton_notifications(bot)

        bot.send_message.assert_called_once_with(12345, "Смена изменена")
        complete.assert_called_once_with(17, True)

    def test_telegram_error_is_reported_to_shifton(self):
        bot = Mock()
        bot.send_message.side_effect = RuntimeError("telegram unavailable")
        queue = [
            {"ok": True, "notification": {"id": 18, "chatId": 67890, "text": "Смена удалена"}},
            {"ok": True, "notification": None},
        ]

        with patch.object(self.rasp, "claim_shifton_notification", side_effect=queue), \
                patch.object(self.rasp, "complete_shifton_notification") as complete:
            self.rasp.send_pending_shifton_notifications(bot)

        complete.assert_any_call(18, False, "telegram unavailable")

    def test_parallel_workers_are_not_started(self):
        started = threading.Event()
        release = threading.Event()

        def blocking_check(_bot):
            started.set()
            release.wait(2)

        with patch.object(self.rasp, "send_pending_shifton_notifications", side_effect=blocking_check) as check:
            self.rasp.start_shifton_notifications_check(Mock())
            self.assertTrue(started.wait(1))
            self.rasp.start_shifton_notifications_check(Mock())
            time.sleep(0.05)
            self.assertEqual(check.call_count, 1)
            release.set()

            for _ in range(20):
                if not self.rasp.shifton_notifications_lock.locked():
                    break
                time.sleep(0.05)
            self.assertFalse(self.rasp.shifton_notifications_lock.locked())


if __name__ == "__main__":
    unittest.main()
