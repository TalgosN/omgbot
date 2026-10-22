import copy
import sqlite3
import tempfile
import threading
import types
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from test_shifton_notifications import load_rasp_module


class ScheduleRefreshTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db_path = str(Path(self.directory.name) / 'schedule.sqlite')
        self.rasp = load_rasp_module()
        self.payload = {'ok': True, 'days': [{
            'date': '2026-10-22', 'locations': [{
                'title': 'Марьино', 'shifts': [{
                    'employee': 'Иванов Иван', 'telegram': 'first',
                    'start': '10:00', 'end': '02:00',
                }],
            }],
        }]}

    def refresh(self):
        return self.rasp.refresh_shift_schedule(
            '2026-10-22', '2026-10-22', db_path=self.db_path,
        )

    def logins(self):
        conn = sqlite3.connect(self.db_path)
        try:
            return conn.execute('SELECT shift_login, dur FROM shifts').fetchall()
        finally:
            conn.close()

    def test_replacement_is_atomic_and_shared_with_a_second_process_module(self):
        with patch.object(self.rasp, 'fetch_schedule_range_from_api', return_value=self.payload) as fetch:
            self.refresh()
            second = load_rasp_module()
            with patch.object(second, 'fetch_schedule_range_from_api') as second_fetch:
                second.refresh_shift_schedule('2026-10-22', '2026-10-22', self.db_path)
            second_fetch.assert_not_called()
            self.assertEqual(self.logins(), [('@first', 16.0)])
            changed = copy.deepcopy(self.payload)
            changed['days'][0]['locations'][0]['shifts'][0]['telegram'] = '@replacement'
            conn = sqlite3.connect(self.db_path)
            with conn:
                conn.execute("UPDATE shift_schedule_cache SET refreshed_at=0 WHERE day<>'lock'")
                conn.execute("INSERT INTO shifts (shift_login, source) VALUES ('@archive', 'legacy')")
            conn.close()
            fetch.return_value = changed
            self.refresh()
        self.assertEqual(self.logins(), [('@archive', None), ('@replacement', 16.0)])

    def test_empty_day_removes_cancelled_shifts_and_incomplete_response_preserves_them(self):
        with patch.object(self.rasp, 'fetch_schedule_range_from_api', return_value=self.payload) as fetch:
            self.refresh()
            conn = sqlite3.connect(self.db_path)
            with conn:
                conn.execute("UPDATE shift_schedule_cache SET refreshed_at=0 WHERE day<>'lock'")
            conn.close()
            fetch.return_value = {'ok': True, 'days': []}
            with self.assertRaisesRegex(RuntimeError, 'неполный'):
                self.refresh()
            self.assertEqual(self.logins(), [('@first', 16.0)])
            conn = sqlite3.connect(self.db_path)
            with conn:
                conn.execute("UPDATE shift_schedule_cache SET refreshed_at=0 WHERE day='lock'")
            conn.close()
            fetch.return_value = {'ok': True, 'days': [{'date': '2026-10-22', 'locations': []}]}
            self.refresh()
        self.assertEqual(self.logins(), [])

    def test_concurrent_entries_share_one_request_without_holding_database_write_lock(self):
        started, release = threading.Event(), threading.Event()
        errors = []

        def fetch(*_args):
            started.set()
            release.wait(3)
            return self.payload

        def run():
            try:
                self.refresh()
            except Exception as error:
                errors.append(error)

        with patch.object(self.rasp, 'fetch_schedule_range_from_api', side_effect=fetch) as request:
            first = threading.Thread(target=run)
            first.start()
            self.assertTrue(started.wait(2))
            conn = sqlite3.connect(self.db_path, timeout=0.2)
            conn.execute('BEGIN IMMEDIATE')
            conn.rollback()
            conn.close()
            second = threading.Thread(target=run)
            second.start()
            release.set()
            first.join(4)
            second.join(4)
        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertEqual(errors, [])
        request.assert_called_once()

    def test_failed_request_is_not_retried_by_every_page(self):
        with patch.object(self.rasp, 'fetch_schedule_range_from_api', return_value={'ok': False}) as fetch:
            for _ in range(3):
                with self.assertRaises(RuntimeError):
                    self.refresh()
        fetch.assert_called_once()

    def test_app_refresh_populates_home_shift_and_task_data_before_rendering(self):
        import kpi_web

        today = date(2026, 10, 22)
        conn = sqlite3.connect(self.db_path)
        with conn:
            conn.execute('CREATE TABLE users (ID INTEGER, chatid TEXT, login TEXT, '
                         'nick_name TEXT, first_name TEXT, second_name TEXT, status INTEGER)')
            conn.execute("INSERT INTO users VALUES (1, '101', '@first', 'Иван', 'Иван', 'Иванов', 0)")
        conn.close()
        payload = {'ok': True, 'days': [
            {'date': '2026-10-19', 'locations': []},
            {'date': '2026-10-20', 'locations': []},
            {'date': '2026-10-21', 'locations': []},
            *self.payload['days'],
            *[{'date': f'2026-10-{day}', 'locations': []} for day in range(23, 30)],
        ]}
        employee = {'chatid': '101', 'login': '@first', 'status': 0}
        real_refresh = self.rasp.refresh_shift_schedule
        module = types.SimpleNamespace(refresh_shift_schedule=real_refresh)
        now = datetime(2026, 10, 22, 12, tzinfo=ZoneInfo('Europe/Moscow'))
        with kpi_web.app.test_request_context(), \
                patch.dict(kpi_web.app.config, {'TESTING': False}), \
                patch.object(kpi_web, 'DB_PATH', self.db_path), \
                patch.object(kpi_web, '_moscow_today', return_value=today), \
                patch.dict('sys.modules', {'rasp': module}), \
                patch.object(self.rasp, 'fetch_schedule_range_from_api', return_value=payload) as fetch:
            kpi_web.g.kpi_preview = None
            kpi_web._refresh_app_schedule()
            self.assertEqual(kpi_web._upcoming_shifts('@first', date_from=today.isoformat())[0]['club'], 'Марьино')
            self.assertEqual(kpi_web._current_user_shifts('@first', today.isoformat(), today.isoformat())[0]['club'], 'Марьино')
            tasks = kpi_web.app_task_list(employee, db_path=self.db_path, now=now)
            self.assertTrue(any(task['club'] == 'Марьино' and task['can_execute'] for task in tasks))
            kpi_web._refresh_app_schedule()
        fetch.assert_called_once()

    def test_employee_preview_keeps_schedule_read_only(self):
        import kpi_web
        from unittest.mock import Mock

        fetch = Mock(return_value=self.payload)
        refresh = Mock()
        module = types.SimpleNamespace(
            fetch_schedule_range_from_api=fetch, refresh_shift_schedule=refresh,
        )
        with kpi_web.app.test_request_context(), \
                patch.dict(kpi_web.app.config, {'TESTING': False}), \
                patch.dict('sys.modules', {'rasp': module}):
            kpi_web.g.kpi_preview = {'login': '@first'}
            kpi_web._refresh_app_schedule()
            result = kpi_web._shift_schedule_range('2026-10-22', '2026-10-22')
        self.assertTrue(result['ok'])
        fetch.assert_called_once()
        refresh.assert_not_called()


if __name__ == '__main__':
    unittest.main()
