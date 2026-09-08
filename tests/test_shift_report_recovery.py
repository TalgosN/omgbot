import json
import sqlite3
import tempfile
import unittest
from contextlib import ExitStack, closing
from pathlib import Path
from unittest.mock import Mock, patch

import kpi_web
from test_kpi_web import BOT_TOKEN, signed_init_data, user


class ShiftReportRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.db_path = str(Path(directory) / 'reports.sqlite3')
        self.bot = Mock()
        kpi_web.app.config['TESTING'] = True
        self.client = kpi_web.app.test_client()
        self.headers = {'X-Telegram-Init-Data': signed_init_data()}
        self.run_id = 'recovery-close-001'
        self.scenario = {
            'action': 'close', 'club': 'Test club', 'version': 'v1',
            'variant_index': 0, 'questions': [], 'cleanliness_questions': [],
            'shift': {'date': kpi_web._moscow_today().isoformat()},
        }
        for name, value in {
            'DB_PATH': self.db_path, 'TELEGRAM_API_KEY': BOT_TOKEN,
        }.items():
            self.stack.enter_context(patch.object(kpi_web, name, value))
        for name, value in {
            'get_user': user(0), '_notification_bot': self.bot,
            'is_main_group_member': True,
            '_shift_report_test_club': ('Test club', {'shift_name': 'Test club'}),
            'update_table_open': None,
        }.items():
            self.stack.enter_context(patch.object(kpi_web, name, return_value=value))
        self.stack.enter_context(patch.dict(kpi_web.CHATS, {
            'reports': 'reports', 'main_group': 'main',
        }))
        kpi_web._initialize_shift_report_schema(self.db_path)
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute('CREATE TABLE activity (ID INTEGER PRIMARY KEY, dtrep, login, club, action)')
            conn.execute(
                '''INSERT INTO shift_webapp_runs
                   (id, login, chatid, club, action, shift_date, scenario_version,
                    variant_index, started_at, scenario_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (self.run_id, '@tester', '1001', 'Test club', 'close',
                 self.scenario['shift']['date'], 'v1', 0,
                 kpi_web._effective_now().strftime('%Y-%m-%d %H:%M:%S'),
                 json.dumps(self.scenario)),
            )

    def submit(self):
        return self.client.post('/api/shift-test/submit', headers=self.headers, data={
            'report': json.dumps({
                'run_id': self.run_id, 'action': 'close', 'variant_index': 0,
                'version': 'v1', 'answers': {}, 'photo_ids': [],
            }),
        })

    def status(self):
        return self.client.get(
            f'/api/shift-test/scenario?action=close&run_id={self.run_id}',
            headers=self.headers,
        ).get_json()

    def test_failed_delivery_remains_resumable_and_retries_only_pending_steps(self):
        with patch.object(kpi_web, '_send_shift_report_test') as send_report:
            self.bot.send_message.side_effect = RuntimeError('connection lost')
            self.assertEqual(self.submit().status_code, 502)
            self.assertFalse(self.status()['completed'])
            resumed = self.client.get(
                '/api/shift-test/scenario?action=close', headers=self.headers,
            ).get_json()
            self.assertEqual(resumed['run_id'], self.run_id)
            self.bot.send_message.side_effect = None
            self.assertTrue(self.submit().get_json()['completed'])
            send_report.assert_called_once()
        self.assertTrue(self.status()['completed'])
        with closing(sqlite3.connect(self.db_path)) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM activity').fetchone()[0], 1)

    def test_completed_retry_needs_no_photos_and_sends_nothing(self):
        with patch.object(kpi_web, '_send_shift_report_test') as send_report:
            self.assertEqual(self.submit().status_code, 200)
            self.bot.reset_mock()
            response = self.client.post('/api/shift-test/submit', headers=self.headers, data={
                'report': json.dumps({
                    'run_id': self.run_id, 'action': 'close', 'variant_index': 0,
                }),
            })
            self.assertTrue(response.get_json()['already_completed'])
            send_report.assert_called_once()
            self.bot.send_message.assert_not_called()

    def test_overlapping_submission_is_rejected_and_lock_is_released(self):
        nested = []

        def send(*args, **kwargs):
            nested.append(self.submit())

        with patch.object(kpi_web, '_send_shift_report_test', side_effect=send):
            self.assertEqual(self.submit().status_code, 200)
        self.assertEqual(nested[0].status_code, 409)
        self.assertEqual(nested[0].get_json()['code'], 'report_in_progress')
        self.assertNotIn(f'@tester:{self.run_id}', kpi_web._shift_report_submitting)

    def test_other_employee_cannot_confirm_completed_report(self):
        with patch.object(kpi_web, '_send_shift_report_test'):
            self.assertEqual(self.submit().status_code, 200)
        with patch.object(kpi_web, 'get_user', return_value={**user(0), 'login': '@other'}):
            self.assertEqual(self.submit().status_code, 400)

    def test_activity_and_report_link_roll_back_together(self):
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute('''CREATE TRIGGER fail_activity_link
                            BEFORE UPDATE OF activity_id ON shift_webapp_runs
                            BEGIN SELECT RAISE(ABORT, 'simulated write failure'); END''')
        with patch.object(kpi_web, '_send_shift_report_test') as send_report:
            self.assertEqual(self.submit().status_code, 502)
            with closing(sqlite3.connect(self.db_path)) as conn, conn:
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM activity').fetchone()[0], 0)
                conn.execute('DROP TRIGGER fail_activity_link')
            self.assertEqual(self.submit().status_code, 200)
            send_report.assert_called_once()
        with closing(sqlite3.connect(self.db_path)) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM activity').fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
