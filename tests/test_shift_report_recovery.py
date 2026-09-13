import json
import sqlite3
import tempfile
import unittest
from contextlib import ExitStack, closing
from pathlib import Path
from unittest.mock import Mock, patch

import kpi_web
from test_kpi_web import BOT_TOKEN, signed_init_data, user

SELECT_SHIFT = kpi_web._select_shift_report_test_shift


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
            '_shift_report_test_scenario': self.scenario,
            '_select_shift_report_test_shift': {**self.scenario['shift'], 'club': 'Test club'},
            '_shift_close_tasks': [],
            'refresh_club_status_dashboard': None,
        }.items():
            self.stack.enter_context(patch.object(kpi_web, name, return_value=value))
        self.stack.enter_context(patch.dict(kpi_web.CHATS, {
            'reports': 'reports', 'main_group': 'main',
        }))
        kpi_web._initialize_shift_report_schema(self.db_path)
        kpi_web.initialize_club_status_dashboard_schema(self.db_path)
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute('CREATE TABLE clubs (club TEXT PRIMARY KEY, status TEXT)')
            conn.execute("INSERT INTO clubs VALUES ('Test club', 'Закрывается')")
            conn.execute('INSERT INTO club_status_updates (club, changed_at, active_run_id) VALUES (?, ?, ?)',
                         ('Test club', '2026-09-13 10:00:00', self.run_id))
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

    def cancel(self):
        return self.client.post('/api/shift-test/cancel', headers=self.headers,
                                json={'run_id': self.run_id})

    def test_cancellation_restores_club_and_blocks_submission(self):
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute("UPDATE shift_webapp_runs SET previous_status='Открыт' WHERE id=?", (self.run_id,))
        self.assertEqual(self.cancel().status_code, 200)
        self.assertTrue(self.status()['cancelled'])
        self.assertEqual(self.submit().status_code, 400)
        with closing(sqlite3.connect(self.db_path)) as conn:
            self.assertEqual(conn.execute('SELECT status FROM clubs').fetchone()[0], 'Открыт')

    def test_cancel_does_not_overwrite_newer_club_state(self):
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute("UPDATE club_status_updates SET active_run_id='newer-run'")
            conn.execute("UPDATE clubs SET status='Закрыт'")
        self.assertEqual(self.cancel().status_code, 200)
        with closing(sqlite3.connect(self.db_path)) as conn:
            self.assertEqual(conn.execute('SELECT status FROM clubs').fetchone()[0], 'Закрыт')

    def test_partial_submission_cannot_be_cancelled(self):
        self.bot.send_message.side_effect = RuntimeError('connection lost')
        with patch.object(kpi_web, '_send_shift_report_test'):
            self.assertEqual(self.submit().status_code, 502)
        self.assertEqual(self.cancel().status_code, 409)
        self.assertFalse(self.status()['cancelled'])

    def test_each_unfinished_task_requires_a_reason(self):
        tasks = [{'id': 10, 'title': 'Полы'}, {'id': 11, 'title': 'Оборудование'}]
        with patch.object(kpi_web, '_shift_close_tasks', return_value=tasks), \
                patch.object(kpi_web, '_send_shift_report_test') as send:
            self.assertEqual(self.submit().status_code, 400)
            self.assertFalse(self.status()['submission_started'])
            payload = {'run_id': self.run_id, 'action': 'close', 'variant_index': 0,
                       'version': 'v1', 'answers': {}, 'photo_ids': [],
                       'task_reasons': {'10': 'Нет воды'}}
            response = self.client.post('/api/shift-test/submit', headers=self.headers,
                                        data={'report': json.dumps(payload)})
            self.assertEqual(response.status_code, 400)
            send.assert_not_called()
            payload['task_reasons']['11'] = 'Ожидаем запчасти'
            response = self.client.post('/api/shift-test/submit', headers=self.headers,
                                        data={'report': json.dumps(payload)})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(send.call_args.args[0]['task_reasons']), 2)
        with closing(sqlite3.connect(self.db_path)) as conn:
            self.assertEqual(conn.execute('SELECT status FROM clubs').fetchone()[0], 'Закрыт')
            reasons = json.loads(conn.execute('SELECT task_reasons_json FROM shift_webapp_runs').fetchone()[0])
            self.assertEqual(reasons[1]['reason'], 'Ожидаем запчасти')

    def test_stale_club_cannot_be_submitted_or_auto_resumed(self):
        current = {**self.scenario, 'club': 'Other club'}
        with patch.object(kpi_web, '_shift_report_test_scenario', return_value=current):
            self.assertEqual(self.submit().status_code, 400)
        with patch.object(kpi_web, '_shift_report_test_club', return_value=('Other club', {})):
            result = self.client.get('/api/shift-test/scenario?action=close', headers=self.headers).get_json()
            self.assertNotIn('run_id', result)

    def test_reset_opening_keeps_arrival_and_does_not_send_late_alert(self):
        import openclose
        self.scenario['action'] = 'open'
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute("UPDATE clubs SET status='Закрыт'")
        def start(run_id):
            return self.client.post('/api/shift-test/start', headers=self.headers, json={
                'run_id': run_id, 'action': 'open', 'variant_index': 0, 'version': 'v1',
            })
        first = start('opening-first-attempt')
        self.assertEqual(first.status_code, 200)
        self.run_id = 'opening-first-attempt'
        arrival = first.get_json()['arrival_at']
        self.assertEqual(self.cancel().status_code, 200)
        second = start('opening-second-attempt')
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.get_json()['arrival_at'], arrival)
        with closing(sqlite3.connect(self.db_path)) as conn:
            self.assertEqual(conn.execute('SELECT status FROM clubs').fetchone()[0], 'Подготовка к открытию')
        self.bot.reset_mock()
        with patch.object(openclose, 'DB_PATH', self.db_path):
            openclose.send_status_open('Test club', self.bot)
        self.bot.send_message.assert_not_called()

    def test_active_overnight_shift_is_kept_after_six(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        night = {'date': '2026-09-12', 'club': 'Night', 'start': '22:00', 'end': '08:00'}
        today = {'date': '2026-09-13', 'club': 'Day', 'start': '10:00', 'end': '22:00'}
        with patch.object(kpi_web, '_shift_report_candidate_shifts', return_value=[night, today]):
            for action in ('open', 'close'):
                selected = SELECT_SHIFT('@tester', action=action,
                                        now=datetime(2026, 9, 13, 7, tzinfo=ZoneInfo('Europe/Moscow')))
                self.assertEqual(selected['club'], 'Night')
            selected = SELECT_SHIFT('@tester', action='close',
                                    now=datetime(2026, 9, 13, 9, tzinfo=ZoneInfo('Europe/Moscow')))
            self.assertEqual(selected['club'], 'Day')

    def test_legacy_started_report_can_be_cancelled_after_upgrade(self):
        for action, before, after in [('open', 'Открыт', 'Подготовка к открытию'),
                                      ('close', 'Закрыт', 'Открыт')]:
            with self.subTest(action=action):
                with closing(sqlite3.connect(self.db_path)) as conn, conn:
                    conn.execute('UPDATE shift_webapp_runs SET action=?, cancelled_at=NULL WHERE id=?',
                                 (action, self.run_id))
                    conn.execute('UPDATE clubs SET status=?', (before,))
                    conn.execute('UPDATE club_status_updates SET active_run_id=NULL, changed_at=(SELECT started_at FROM shift_webapp_runs WHERE id=?)',
                                 (self.run_id,))
                self.assertEqual(self.cancel().status_code, 200)
                with closing(sqlite3.connect(self.db_path)) as conn:
                    self.assertEqual(conn.execute('SELECT status FROM clubs').fetchone()[0], after)


if __name__ == '__main__':
    unittest.main()
