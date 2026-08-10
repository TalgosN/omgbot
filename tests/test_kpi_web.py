import hashlib
import hmac
import json
import sqlite3
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path
from urllib.parse import urlencode
from unittest.mock import patch

import kpi_web


BOT_TOKEN = '123456:test-token'


def signed_init_data(telegram_id=1001):
    values = {
        'auth_date': str(int(time.time())),
        'query_id': 'test-query',
        'user': json.dumps(
            {'id': telegram_id, 'first_name': 'Test'},
            separators=(',', ':'),
        ),
    }
    data_check_string = '\n'.join(
        f'{key}={values[key]}'
        for key in sorted(values)
    )
    secret = hmac.new(
        b'WebAppData',
        BOT_TOKEN.encode(),
        hashlib.sha256,
    ).digest()
    values['hash'] = hmac.new(
        secret,
        data_check_string.encode(),
        hashlib.sha256,
    ).hexdigest()
    return urlencode(values)


def user(role):
    return {
        'chatid': '1001',
        'login': '@tester',
        'nick_name': 'Тестер',
        'first_name': 'Test',
        'status': role,
    }


class KpiWebTest(unittest.TestCase):
    def setUp(self):
        kpi_web.app.config['TESTING'] = True
        self.client = kpi_web.app.test_client()
        self.headers = {'X-Telegram-Init-Data': signed_init_data()}
        self.membership_patch = patch.object(
            kpi_web,
            'is_main_group_member',
            return_value=True,
        )
        self.membership_patch.start()

    def tearDown(self):
        self.membership_patch.stop()

    def test_init_data_signature_and_age_are_validated(self):
        self.assertEqual(
            kpi_web._validate_init_data(
                signed_init_data(777),
                BOT_TOKEN,
            )['telegram_id'],
            777,
        )
        self.assertIsNone(kpi_web._validate_init_data('hash=wrong', BOT_TOKEN))

    def test_active_user_outside_main_group_cannot_use_api(self):
        self.membership_patch.stop()
        self.membership_patch = patch.object(
            kpi_web,
            'is_main_group_member',
            return_value=False,
        )
        self.membership_patch.start()
        with (
            patch.object(kpi_web, 'TELEGRAM_API_KEY', BOT_TOKEN),
            patch.object(kpi_web, 'get_user', return_value=user(3)),
        ):
            response = self.client.get('/api/me', headers=self.headers)

        self.assertEqual(response.status_code, 401)

    def test_module_pages_load_shared_swipe_navigation(self):
        for path in ('/kpi', '/problems', '/shift', '/shift-config'):
            response = self.client.get(path)
            try:
                self.assertEqual(response.status_code, 200)
                self.assertIn(
                    b'/static/swipe_navigation.js', response.data,
                )
            finally:
                response.close()

        script = self.client.get('/static/swipe_navigation.js')
        try:
            self.assertEqual(script.status_code, 200)
            self.assertIn(b"navigate('/')", script.data)
        finally:
            script.close()

    def test_kpi_and_taskboard_mobile_controls_use_full_width_layouts(self):
        response = self.client.get('/kpi')
        try:
            kpi_html = response.get_data(as_text=True)
            self.assertNotIn('class="home-link"', kpi_html)
            self.assertIn('id="kpiUserName"', kpi_html)
        finally:
            response.close()

        response = self.client.get('/problems')
        try:
            taskboard_html = response.get_data(as_text=True)
            self.assertLess(
                taskboard_html.index('id="newProblem"'),
                taskboard_html.index('id="repairCatalog"'),
            )
        finally:
            response.close()

        response = self.client.get('/static/app.js')
        try:
            app_script = response.get_data(as_text=True)
            self.assertIn('owner-settings-button', app_script)
            self.assertIn("state.me.role_name", app_script)
        finally:
            response.close()

    def test_home_places_compact_shift_module_before_dashboard(self):
        response = self.client.get('/')
        try:
            html = response.get_data(as_text=True)
            self.assertLess(
                html.index('class="modules-section"'),
                html.index('id="personalSection"'),
            )
            self.assertIn('class="module-card shift-module" href="/shift"', html)
            self.assertIn('<span class="module-icon">↗</span>', html)
            self.assertIn('<span class="module-icon">?</span>', html)
            self.assertIn('<span class="module-icon">✓</span>', html)
            self.assertNotIn('id="shiftConfigModule"', html)
            self.assertIn('Сегодня в клубах', html)
        finally:
            response.close()

        script = self.client.get('/static/home.js')
        try:
            self.assertNotIn(b'club.red_zone', script.data)
            self.assertNotIn('Ближайшая'.encode(), script.data)
            self.assertIn('Идёт бронь'.encode(), script.data)
            self.assertIn('Броней сейчас нет'.encode(), script.data)
            self.assertIn(b'class="club-shift-summary"', script.data)
            self.assertLess(
                script.data.index(b'class="club-shift-summary"'),
                script.data.index(b'class="club-meta"'),
            )
            self.assertIn(
                b'class="summary-tile clickable-card" href="/kpi"',
                script.data,
            )
            self.assertIn(
                b'class="summary-tile clickable-card" href="/problems"',
                script.data,
            )
            self.assertIn(
                b'class="shift-card clickable-card shift-link" href="/shift"',
                script.data,
            )
        finally:
            script.close()

    def test_shift_module_is_visible_to_employee_and_unlocks_manager_tools(self):
        with (
            patch.object(kpi_web, 'TELEGRAM_API_KEY', BOT_TOKEN),
            patch.object(kpi_web, 'OMG_SHIFT_URL', 'http://shift.test/'),
            patch.object(kpi_web, 'get_user', return_value=user(0)),
            patch.object(kpi_web, '_upcoming_shifts', return_value=[{
                'date': '2026-08-10', 'club': 'Дмитровка', 'duration': 12.5,
                'start': '10:00', 'end': '22:30',
            }]),
            patch.object(kpi_web, '_shift_month_summary', return_value={
                'shifts': 8, 'hours': 86.0,
            }),
        ):
            employee_response = self.client.get(
                '/api/shift', headers=self.headers,
            )

        with (
            patch.object(kpi_web, 'TELEGRAM_API_KEY', BOT_TOKEN),
            patch.object(kpi_web, 'OMG_SHIFT_URL', 'http://shift.test/'),
            patch.object(kpi_web, 'get_user', return_value=user(2)),
            patch.object(kpi_web, '_upcoming_shifts', return_value=[]),
            patch.object(kpi_web, '_shift_month_summary', return_value={
                'shifts': 0, 'hours': 0.0,
            }),
        ):
            manager_response = self.client.get(
                '/api/shift', headers=self.headers,
            )

        self.assertEqual(employee_response.status_code, 200)
        employee_payload = employee_response.get_json()
        self.assertEqual(employee_payload['external_url'], 'http://shift.test/')
        self.assertFalse(employee_payload['can_manage'])
        self.assertEqual(employee_payload['employee_dashboard']['month_summary'], {
            'shifts': 8, 'hours': 86.0,
        })
        self.assertEqual(
            employee_payload['employee_dashboard']['upcoming_shifts'][0]['club'],
            'Дмитровка',
        )
        self.assertEqual(
            employee_payload['employee_dashboard']['upcoming_shifts'][0]['start'],
            '10:00',
        )
        self.assertEqual(manager_response.status_code, 200)
        self.assertTrue(manager_response.get_json()['can_manage'])

        shift_css = self.client.get('/static/shift.css')
        try:
            self.assertIn(b'.shift-actions[hidden]', shift_css.data)
            self.assertIn(b'.shift-action[hidden]', shift_css.data)
            self.assertIn(b'display:none !important', shift_css.data)
        finally:
            shift_css.close()

    def test_owner_is_excluded_from_active_kpi_employees(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'roles.db'
            conn = sqlite3.connect(db_path)
            conn.executescript(
                '''
                CREATE TABLE users (
                    ID INTEGER PRIMARY KEY,
                    login TEXT,
                    status INTEGER
                );
                INSERT INTO users VALUES (1, '@employee', 0);
                INSERT INTO users VALUES (2, '@manager', 2);
                INSERT INTO users VALUES (3, '@owner', 3);
                '''
            )
            conn.commit()
            conn.close()

            with patch.object(kpi_web, 'DB_PATH', str(db_path)):
                logins = kpi_web._active_employee_logins()

        self.assertEqual(logins, ['@employee', '@manager'])

    def test_owner_home_contains_club_dashboard_without_personal_kpi(self):
        team_rows = [{'login': '@employee', 'shifts': 2, 'zone': '🟢'}]
        management = {
            'participants': 1,
            'average_pct': 0.8,
            'red_zone': 0,
            'active_penalties': 0,
        }
        clubs = [{
            'club': 'Марьино',
            'status': 'Открыт',
            'on_shift': ['Сотрудник'],
            'problems': {'work': 1, 'review': 0},
        }]
        with (
            patch.object(kpi_web, 'TELEGRAM_API_KEY', BOT_TOKEN),
            patch.object(kpi_web, 'get_user', return_value=user(3)),
            patch.object(
                kpi_web,
                '_team_snapshot',
                return_value=(team_rows, management),
            ),
            patch.object(
                kpi_web,
                '_task_counts',
                return_value={'work': 1, 'review': 0},
            ),
            patch.object(kpi_web, '_problem_counts_by_club', return_value={}),
            patch.object(kpi_web, '_club_dashboard', return_value=clubs),
            patch.object(kpi_web, '_upcoming_shifts') as upcoming,
        ):
            response = self.client.get('/api/home', headers=self.headers)

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(payload['personal_kpi'])
        self.assertEqual(payload['upcoming_shifts'], [])
        self.assertEqual(payload['clubs'], clubs)
        upcoming.assert_not_called()

    def test_manager_home_also_contains_operational_club_dashboard(self):
        clubs = [{
            'club': 'Ленинский',
            'status': 'Открыт',
            'on_shift': ['Менеджер'],
            'problems': {'work': 0, 'review': 1},
        }]
        management = {
            'participants': 1,
            'average_pct': 0.9,
            'red_zone': 0,
            'active_penalties': 0,
        }
        with (
            patch.object(kpi_web, 'TELEGRAM_API_KEY', BOT_TOKEN),
            patch.object(kpi_web, 'get_user', return_value=user(2)),
            patch.object(kpi_web, '_upcoming_shifts', return_value=[]),
            patch.object(kpi_web, 'calculate_monthly_kpi', return_value=[]),
            patch.object(
                kpi_web, '_team_snapshot', return_value=([], management),
            ),
            patch.object(
                kpi_web, '_task_counts', return_value={'work': 0, 'review': 1},
            ),
            patch.object(kpi_web, '_problem_counts_by_club', return_value={}),
            patch.object(kpi_web, '_club_dashboard', return_value=clubs),
        ):
            response = self.client.get('/api/home', headers=self.headers)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['clubs'], clubs)

    def test_club_employee_sees_only_today_shift_club_bookings(self):
        groups = [{
            'club': 'Марьино',
            'count': 1,
            'participants': 5,
            'bookings': [{
                'start': '2026-08-08T12:00:00',
                'end': '2026-08-08T13:00:00',
                'format': 'Классический VR',
                'participants': 5,
            }],
        }]
        with (
            patch.object(kpi_web, 'TELEGRAM_API_KEY', BOT_TOKEN),
            patch.object(kpi_web, 'get_user', return_value=user(0)),
            patch.object(kpi_web, '_today_shift_clubs', return_value=['Марьино']),
            patch.object(kpi_web, '_club_booking_groups', return_value=groups) as grouped,
            patch.object(kpi_web, 'booking_freshness', return_value={
                'last_synced_at': '2026-08-08T12:00:00+03:00',
                'age_minutes': 1,
                'stale': False,
            }),
        ):
            response = self.client.get('/api/bookings/today', headers=self.headers)

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload['mode'], 'clubs')
        self.assertEqual(payload['groups'], groups)
        self.assertNotIn('number', payload['groups'][0]['bookings'][0])
        grouped.assert_called_once_with(['Марьино'], unittest.mock.ANY)

    def test_callcenter_employee_sees_upcoming_unpaid_orders(self):
        order = {
            'reservation_at': '2026-08-09T12:00:00',
            'reservation_end_at': '2026-08-09T13:00:00',
            'date': date(2026, 8, 9),
            'club': 'Каширка',
            'booking_format': 'Мероприятие',
            'participants': 12,
            'number': '12345',
            'url': 'https://my.bukza.com/order/12345',
        }
        with (
            patch.object(kpi_web, 'TELEGRAM_API_KEY', BOT_TOKEN),
            patch.object(kpi_web, 'get_user', return_value=user(0)),
            patch.object(
                kpi_web,
                '_today_shift_clubs',
                return_value=['Коллцентр'],
            ),
            patch.object(kpi_web, 'upcoming_unpaid_orders', return_value=[order]),
            patch.object(kpi_web, 'booking_freshness', return_value={
                'last_synced_at': '2026-08-08T12:00:00+03:00',
                'age_minutes': 1,
                'stale': False,
            }),
        ):
            response = self.client.get('/api/bookings/today', headers=self.headers)

        payload = response.get_json()
        self.assertEqual(payload['mode'], 'callcenter')
        self.assertEqual(payload['bookings'][0]['number'], '12345')
        self.assertEqual(payload['bookings'][0]['club'], 'Каширка')

    def test_manager_sees_brief_booking_groups_for_all_clubs(self):
        groups = [{'club': 'Ленинский', 'count': 2, 'participants': 7, 'bookings': []}]
        with (
            patch.object(kpi_web, 'TELEGRAM_API_KEY', BOT_TOKEN),
            patch.object(kpi_web, 'get_user', return_value=user(2)),
            patch.object(kpi_web, '_club_booking_groups', return_value=groups) as grouped,
            patch.object(kpi_web, '_today_shift_clubs') as shifts,
            patch.object(kpi_web, 'booking_freshness', return_value={
                'last_synced_at': '2026-08-08T12:00:00+03:00',
                'age_minutes': 1,
                'stale': False,
            }),
        ):
            response = self.client.get('/api/bookings/today', headers=self.headers)

        payload = response.get_json()
        self.assertEqual(payload['mode'], 'management')
        self.assertEqual(payload['groups'], groups)
        grouped.assert_called_once_with(
            list(kpi_web.BUKZA_CLUB_CODES.values()),
            unittest.mock.ANY,
        )
        shifts.assert_not_called()

    def test_manager_and_owner_can_open_shift_config(self):
        config = {'version': 'abc', 'clubs': []}
        for role in (2, 3):
            with (
                patch.object(kpi_web, 'TELEGRAM_API_KEY', BOT_TOKEN),
                patch.object(kpi_web, 'get_user', return_value=user(role)),
                patch.object(kpi_web, 'get_editor_config', return_value=config),
            ):
                response = self.client.get('/api/shift-config', headers=self.headers)

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json(), config)

    def test_employee_cannot_open_shift_config(self):
        with (
            patch.object(kpi_web, 'TELEGRAM_API_KEY', BOT_TOKEN),
            patch.object(kpi_web, 'get_user', return_value=user(0)),
            patch.object(kpi_web, 'get_editor_config') as editor,
        ):
            response = self.client.get('/api/shift-config', headers=self.headers)

        self.assertEqual(response.status_code, 403)
        editor.assert_not_called()

    def test_problem_form_uses_general_request_type(self):
        with (
            patch.object(kpi_web, 'TELEGRAM_API_KEY', BOT_TOKEN),
            patch.object(kpi_web, 'get_user', return_value=user(0)),
            patch.object(kpi_web, 'get_clubs', return_value={'Марьино': {}}),
        ):
            response = self.client.get('/api/problems-meta', headers=self.headers)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['types'], [
            'Общее обращение',
            'Ремонт',
            'Улучшение бота',
        ])

    def test_mini_app_creates_anonymous_problem_in_shared_tasks_table(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'tasks.db'
            conn = sqlite3.connect(db_path)
            conn.execute(
                '''CREATE TABLE tasks (
                       ID INTEGER PRIMARY KEY AUTOINCREMENT,
                       dtrep TEXT, type TEXT, club TEXT, title TEXT,
                       photo BLOB, desc TEXT, status TEXT, dtfb TEXT,
                       feedback TEXT
                   )'''
            )
            conn.commit()
            conn.close()

            kpi_web.initialize_repair_schema(str(db_path))
            conn = sqlite3.connect(db_path)
            item_id = conn.execute(
                "SELECT id FROM repair_item_types WHERE name='VR-шлем'"
            ).fetchone()[0]
            location_id = conn.execute(
                "SELECT id FROM repair_locations WHERE club='Марьино' AND name='2 зона'"
            ).fetchone()[0]
            conn.close()

            with (
                patch.object(kpi_web, 'TELEGRAM_API_KEY', BOT_TOKEN),
                patch.object(kpi_web, 'get_user', return_value=user(0)),
                patch.object(kpi_web, 'DB_PATH', str(db_path)),
                patch.object(kpi_web, 'get_clubs', return_value={'Марьино': {}}),
                patch.object(kpi_web, '_send_problem_notification') as notify,
            ):
                response = self.client.post(
                    '/api/problems',
                    headers=self.headers,
                    data={
                        'type': 'Ремонт',
                        'club': 'Марьино',
                        'title': 'Не работает шлем',
                        'description': 'Не включается второй шлем',
                        'repair_item_id': str(item_id),
                        'repair_location_ids': json.dumps([location_id]),
                    },
                )
                detail_response = self.client.get(
                    '/api/problems/1', headers=self.headers,
                )

            conn = sqlite3.connect(db_path)
            row = conn.execute(
                'SELECT type, club, title, desc, status FROM tasks'
            ).fetchone()
            columns = [item[1] for item in conn.execute('PRAGMA table_info(tasks)')]
            repair_link = conn.execute(
                '''SELECT cases.task_id, items.name, locations.name
                   FROM repair_cases cases
                   JOIN repair_item_types items ON items.id=cases.item_type_id
                   JOIN repair_case_locations links ON links.task_id=cases.task_id
                   JOIN repair_locations locations ON locations.id=links.location_id'''
            ).fetchone()
            repair_event = conn.execute(
                'SELECT event_type FROM repair_events WHERE task_id=1'
            ).fetchone()
            conn.close()

        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            row,
            (
                'Ремонт', 'Марьино', 'VR-шлем — 2 зона',
                'Не включается второй шлем', 'В работе',
            ),
        )
        self.assertNotIn('author', columns)
        self.assertEqual(repair_link, (1, 'VR-шлем', '2 зона'))
        self.assertEqual(repair_event, ('created',))
        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(
            detail_response.get_json()['repair']['history'][0]['task_id'], 1,
        )
        notify.assert_called_once()

    def test_problem_follows_existing_solution_and_return_lifecycle(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'tasks.db'
            conn = sqlite3.connect(db_path)
            conn.executescript(
                '''CREATE TABLE tasks (
                       ID INTEGER PRIMARY KEY AUTOINCREMENT,
                       dtrep TEXT, type TEXT, club TEXT, title TEXT,
                       photo BLOB, desc TEXT, status TEXT, dtfb TEXT,
                       feedback TEXT
                   );
                   INSERT INTO tasks (
                       dtrep, type, club, title, desc, status
                   ) VALUES (
                       '2026-08-07', 'Ремонт', 'Марьино',
                       'Шлем', 'Не включается', 'В работе'
                   );'''
            )
            conn.commit()
            conn.close()

            with (
                patch.object(kpi_web, 'TELEGRAM_API_KEY', BOT_TOKEN),
                patch.object(kpi_web, 'get_user', return_value=user(1)),
                patch.object(kpi_web, 'DB_PATH', str(db_path)),
                patch.object(kpi_web, '_send_problem_notification'),
            ):
                solution = self.client.post(
                    '/api/problems/1/solution',
                    headers=self.headers,
                    json={'message': 'Переподключил питание'},
                )

            with (
                patch.object(kpi_web, 'TELEGRAM_API_KEY', BOT_TOKEN),
                patch.object(kpi_web, 'get_user', return_value=user(0)),
                patch.object(kpi_web, 'DB_PATH', str(db_path)),
                patch.object(kpi_web, '_send_problem_notification'),
            ):
                returned = self.client.post(
                    '/api/problems/1/return',
                    headers=self.headers,
                    json={'message': 'Снова выключился'},
                )

            conn = sqlite3.connect(db_path)
            row = conn.execute(
                'SELECT status, dtfb, feedback FROM tasks WHERE ID=1'
            ).fetchone()
            conn.close()

        self.assertEqual(solution.status_code, 200)
        self.assertEqual(returned.status_code, 200)
        self.assertEqual(row[0], 'В работе')
        self.assertIsNone(row[1])
        self.assertIn('Переподключил питание', row[2])
        self.assertIn('Снова выключился', row[2])

    def test_all_active_users_can_read_every_employee_kpi(self):
        with (
            patch.object(kpi_web, 'TELEGRAM_API_KEY', BOT_TOKEN),
            patch.object(kpi_web, 'get_user', return_value=user(0)),
            patch.object(kpi_web, '_active_employee_logins', return_value=['@one']),
            patch.object(
                kpi_web,
                '_employee_logins_with_month_shifts',
                return_value=['@one'],
            ),
            patch.object(kpi_web, '_employee_metadata', return_value={
                '@one': {
                    'role': 0,
                    'role_name': 'Сотрудник',
                    'clubs': ['Дмитровка'],
                },
            }),
            patch.object(kpi_web, 'calculate_monthly_kpi', return_value=[{
                'login': '@one',
                'nickname': 'Первый',
                'shifts': 2,
                'rank': 1,
                'total_pct': 0.8,
            }]) as calculate,
            patch.object(kpi_web, 'list_penalties', return_value=[]),
            patch.object(kpi_web, 'get_kpi_freshness', return_value={
                'latest_metric_date': '2026-07-14',
                'latest_shift_date': '2026-07-15',
            }),
            patch.object(kpi_web, 'get_month_status', return_value={
                'period_month': '2026-07-01',
                'is_closed': False,
            }),
        ):
            response = self.client.get(
                '/api/kpi?month=2026-07&date=2026-07-15',
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['employees'][0]['login'], '@one')
        self.assertEqual(response.get_json()['date'], '2026-07-15')
        self.assertTrue(all(
            call.kwargs['employee_logins'] == ['@one']
            for call in calculate.call_args_list
        ))

    def test_personal_kpi_contains_explanation_and_shift_pace(self):
        row = {
            'login': '@tester',
            'nickname': 'Тестер',
            'shifts': 2,
            'weighted_shifts': 3,
            'rank': 1,
            'zone': '🟢',
            'average_pct': 0.9,
            'total_pct': 0.8,
            'weighted_pct': 0.6,
            'reviews': 1,
            'reviews_plan_per_shift': 0.25,
            'reviews_target': 0.5,
            'reviews_pct': 2,
            'forms': 1,
            'forms_plan_per_shift': 1,
            'forms_target': 2,
            'forms_pct': 0.5,
            'extensions': 0,
            'extensions_plan_per_shift': 0.25,
            'extensions_target': 0.5,
            'extensions_pct': 0,
            'certificates': 0,
            'certificates_plan_per_shift': 125,
            'certificates_target': 250,
            'certificates_pct': 0,
            'subscriptions': 0,
            'subscriptions_plan_per_shift': 250,
            'subscriptions_target': 500,
            'subscriptions_pct': 0,
            'initiatives': 1,
            'initiatives_pct': 0.1,
            'penalty_impact': 0.1,
            'stream_bonus': 0.05,
        }
        with (
            patch.object(kpi_web, 'TELEGRAM_API_KEY', BOT_TOKEN),
            patch.object(kpi_web, 'get_user', return_value=user(0)),
            patch.object(
                kpi_web,
                '_active_employee_logins',
                return_value=['@tester'],
            ),
            patch.object(
                kpi_web,
                '_employee_logins_with_month_shifts',
                return_value=['@tester'],
            ),
            patch.object(
                kpi_web,
                'calculate_monthly_kpi',
                side_effect=[[row], [], []],
            ),
            patch.object(kpi_web, '_employee_metadata', return_value={
                '@tester': {
                    'role': 0,
                    'role_name': 'Сотрудник',
                    'clubs': ['Дмитровка', 'Марьино'],
                },
            }),
            patch.object(kpi_web, 'list_penalties', return_value=[]),
            patch.object(kpi_web, 'get_month_status', return_value={
                'period_month': '2026-07-01',
                'is_closed': False,
            }),
            patch.object(kpi_web, 'get_kpi_freshness', return_value={
                'latest_metric_date': '2026-07-14',
                'latest_shift_date': '2026-07-15',
            }),
        ):
            response = self.client.get(
                '/api/kpi?month=2026-07&date=2026-07-15',
                headers=self.headers,
            )

        payload = response.get_json()
        explanation = payload['my_kpi']['explanation']
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload['my_kpi']['login'], '@tester')
        self.assertEqual(explanation['pace']['shifts'], 2)
        self.assertEqual(explanation['pace']['projected_pct'], 0.8)
        self.assertAlmostEqual(explanation['pace']['gap_to_green_pct'], 0.1)
        self.assertEqual(explanation['metrics'][1]['needed'], 1)
        initiative = next(
            item
            for item in explanation['metrics']
            if item['key'] == 'initiatives'
        )
        self.assertEqual(initiative['bonus_per_item'], 0.1)
        self.assertEqual(initiative['contribution_pct'], 0.1)
        self.assertEqual(
            payload['freshness']['latest_metric_date'],
            '2026-07-14',
        )
        self.assertEqual(payload['my_kpi']['clubs'], ['Дмитровка', 'Марьино'])

    def test_attention_reasons_follow_confirmed_manager_rules(self):
        reasons = kpi_web._attention_reasons({
            'zone': '🔴',
            'penalties': 1,
            'kpi_change_7d': -0.10,
            'shifts': 3,
            'reviews': 0,
            'forms': 0,
            'extensions': 0,
            'certificates': 0,
            'subscriptions': 0,
            'initiatives': 0,
        })

        self.assertEqual(
            [reason['key'] for reason in reasons],
            ['red_zone', 'penalty', 'drop', 'no_kpi'],
        )

    def test_kpi_payload_marks_seven_day_drop_for_manager(self):
        current = {
            'login': '@one',
            'nickname': 'Первый',
            'shifts': 2,
            'weighted_shifts': 2,
            'rank': 1,
            'zone': '🟡',
            'average_pct': 0.8,
            'total_pct': 0.7,
            'weighted_pct': 0.7,
            'reviews': 1,
            'reviews_pct': 1,
            'forms': 1,
            'forms_pct': 0.5,
            'extensions': 1,
            'extensions_pct': 1,
            'certificates': 0,
            'certificates_pct': 0,
            'subscriptions': 0,
            'subscriptions_pct': 0,
            'initiatives': 1,
            'initiatives_pct': 0.1,
            'penalties': 0,
            'penalty_impact': 0,
            'stream_bonus': 0,
        }
        previous_day = dict(current, total_pct=0.72)
        previous_week = dict(current, total_pct=0.85)
        with (
            patch.object(kpi_web, 'TELEGRAM_API_KEY', BOT_TOKEN),
            patch.object(kpi_web, 'get_user', return_value=user(2)),
            patch.object(kpi_web, '_active_employee_logins', return_value=['@one']),
            patch.object(
                kpi_web,
                'calculate_monthly_kpi',
                side_effect=[[current], [previous_day], [previous_week]],
            ),
            patch.object(kpi_web, '_employee_metadata', return_value={
                '@one': {
                    'role': 0,
                    'role_name': 'Сотрудник',
                    'clubs': ['Дмитровка'],
                },
            }),
            patch.object(kpi_web, 'list_penalties', return_value=[]),
            patch.object(kpi_web, 'get_month_status', return_value={
                'period_month': '2026-07-01',
                'is_closed': False,
            }),
            patch.object(kpi_web, 'get_kpi_freshness', return_value={
                'latest_metric_date': '2026-07-15',
                'latest_shift_date': '2026-07-15',
            }),
        ):
            response = self.client.get(
                '/api/kpi?month=2026-07&date=2026-07-15',
                headers=self.headers,
            )

        employee = response.get_json()['employees'][0]
        self.assertAlmostEqual(employee['kpi_change_7d'], -0.15)
        self.assertTrue(employee['needs_attention'])
        self.assertEqual(
            [reason['key'] for reason in employee['attention_reasons']],
            ['drop'],
        )
        self.assertEqual(employee['clubs'], ['Дмитровка'])

    def test_employee_metadata_contains_every_club_in_selected_month(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / 'metadata.db'
            conn = sqlite3.connect(db_path)
            conn.executescript(
                '''
                CREATE TABLE users (
                    login TEXT,
                    first_name TEXT,
                    second_name TEXT,
                    status INTEGER
                );
                CREATE TABLE shifts (
                    shift_login TEXT,
                    shift_first_name TEXT,
                    shift_second_name TEXT,
                    dt_shift TEXT,
                    club TEXT
                );
                INSERT INTO users VALUES ('@one', 'One', 'User', 0);
                INSERT INTO shifts VALUES (
                    '@one', 'One', 'User', '2026-07-05', 'Дмитровка'
                );
                INSERT INTO shifts VALUES (
                    '@one', 'One', 'User', '2026-07-12', 'Марьино'
                );
                INSERT INTO shifts VALUES (
                    '@one', 'One', 'User', '2026-08-01', 'Каширка'
                );
                '''
            )
            conn.commit()
            conn.close()

            with patch.object(kpi_web, 'DB_PATH', str(db_path)):
                metadata = kpi_web._employee_metadata(
                    ['@one'],
                    '2026-07-01',
                )

        self.assertEqual(metadata['@one']['role'], 0)
        self.assertEqual(
            metadata['@one']['clubs'],
            ['Дмитровка', 'Марьино'],
        )

    def test_metric_details_are_available_to_employee(self):
        with (
            patch.object(kpi_web, 'TELEGRAM_API_KEY', BOT_TOKEN),
            patch.object(kpi_web, 'get_user', return_value=user(0)),
            patch.object(kpi_web, '_active_employee_logins', return_value=['@one']),
            patch.object(kpi_web, 'get_metric_entries', return_value=[{
                'id': 1,
                'date': '2026-07-05',
                'value': 1,
                'description': 'Отзыв',
                'club': None,
                'status': None,
            }]) as get_entries,
        ):
            response = self.client.get(
                '/api/kpi/details?month=2026-07&date=2026-07-15'
                '&employee_login=@one&metric=reviews',
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['entries'][0]['date'], '2026-07-05')
        get_entries.assert_called_once_with(
            '@one',
            '2026-07-01',
            'reviews',
            period_end='2026-07-15',
        )

    def test_dynamic_hashtag_details_are_available_to_employee(self):
        with (
            patch.object(kpi_web, 'TELEGRAM_API_KEY', BOT_TOKEN),
            patch.object(kpi_web, 'get_user', return_value=user(0)),
            patch.object(kpi_web, '_active_employee_logins', return_value=['@one']),
            patch.object(kpi_web, 'get_hashtag_entries', return_value=[{
                'id': 1,
                'date': '2026-07-05',
                'value': None,
                'value_unit': None,
                'description': 'Автосим',
                'club': 'Марьино',
                'status': None,
            }]) as get_entries,
        ):
            response = self.client.get(
                '/api/kpi/details?month=2026-07&date=2026-07-15'
                '&employee_login=@one&metric=hashtag:%23автосим',
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['entries'][0]['date'], '2026-07-05')
        get_entries.assert_called_once_with(
            '@one',
            '2026-07-01',
            '#автосим',
            period_end='2026-07-15',
            db_path=kpi_web.DB_PATH,
        )

    def test_daily_analytics_are_available_to_employee(self):
        kpi_web._analytics_cache.clear()
        analytics_row = {
            'login': '@one',
            'nickname': 'Первый',
            'total_pct': 0.8,
            'weighted_pct': 0.6,
            'rank': 1,
            'zone': '🟢',
            'shifts': 2,
            'weighted_shifts': 3,
            'reviews': 1,
            'forms': 2,
            'extensions': 1,
            'certificates': 0,
            'subscriptions': 0,
            'initiatives': 1,
            'penalties': 0,
            'stream': False,
        }
        with (
            patch.object(kpi_web, 'TELEGRAM_API_KEY', BOT_TOKEN),
            patch.object(kpi_web, 'get_user', return_value=user(0)),
            patch.object(kpi_web, '_active_employee_logins', return_value=['@one']),
            patch.object(
                kpi_web,
                '_employee_logins_with_month_shifts',
                return_value=['@one'],
            ),
            patch.object(kpi_web, 'initialize_kpi_calculation_schema'),
            patch.object(
                kpi_web,
                'calculate_daily_kpi_series',
                return_value=[
                    {'date': '2026-07-01', 'employees': [analytics_row]},
                    {'date': '2026-07-02', 'employees': [analytics_row]},
                ],
            ) as calculate,
        ):
            response = self.client.get(
                '/api/kpi/analytics?mode=daily&month=2026-07&date=2026-07-02',
                headers=self.headers,
            )
            cached_response = self.client.get(
                '/api/kpi/analytics?mode=daily&month=2026-07&date=2026-07-02',
                headers=self.headers,
            )

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(cached_response.status_code, 200)
        self.assertEqual(len(payload['points']), 2)
        self.assertEqual(payload['points'][-1]['team']['kpi'], 0.8)
        self.assertEqual(payload['employees'][0]['login'], '@one')
        calculate.assert_called_once_with(
            '2026-07-01',
            '2026-07-02',
            employee_logins=['@one'],
            ensure_schema=False,
        )

    def test_employee_cannot_add_penalty(self):
        with (
            patch.object(kpi_web, 'TELEGRAM_API_KEY', BOT_TOKEN),
            patch.object(kpi_web, 'get_user', return_value=user(0)),
        ):
            response = self.client.post(
                '/api/penalties',
                headers=self.headers,
                json={
                    'month': '2026-07',
                    'employee_login': '@one',
                    'reason': 'Причина',
                },
            )

        self.assertEqual(response.status_code, 403)

    def test_manager_adds_fixed_ten_percent_penalty(self):
        with (
            patch.object(kpi_web, 'TELEGRAM_API_KEY', BOT_TOKEN),
            patch.object(kpi_web, 'get_user', return_value=user(2)),
            patch.object(kpi_web, '_active_employee_logins', return_value=['@one']),
            patch.object(kpi_web, 'add_penalty', return_value=42) as add_penalty,
        ):
            response = self.client.post(
                '/api/penalties',
                headers=self.headers,
                json={
                    'month': '2026-07',
                    'employee_login': '@one',
                    'reason': 'Опоздание',
                },
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()['impact_pct'], 0.10)
        add_penalty.assert_called_once_with(
            '@one',
            '2026-07-01',
            'Опоздание',
            '@tester',
            source='telegram_mini_app',
        )

    def test_settings_are_forbidden_to_manager(self):
        with (
            patch.object(kpi_web, 'TELEGRAM_API_KEY', BOT_TOKEN),
            patch.object(kpi_web, 'get_user', return_value=user(2)),
        ):
            response = self.client.get(
                '/api/kpi/settings?month=2026-07',
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 403)

    def test_owner_can_save_kpi_settings(self):
        saved = {
            'period_month': '2026-08-01',
            'metrics': [],
            'clubs': [],
        }
        with (
            patch.object(kpi_web, 'TELEGRAM_API_KEY', BOT_TOKEN),
            patch.object(kpi_web, 'get_user', return_value=user(3)),
            patch.object(
                kpi_web,
                'save_kpi_settings',
                return_value=saved,
            ) as save_settings,
        ):
            response = self.client.put(
                '/api/kpi/settings',
                headers=self.headers,
                json={
                    'month': '2026-08',
                    'metrics': {
                        'Отзывы': 0.25,
                        'Инициативы': 0.10,
                    },
                    'clubs': {
                        'Дмитровка': {
                            'weekday_weight': 0.75,
                            'weekend_weight': 2,
                        },
                    },
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['period_month'], '2026-08-01')
        save_settings.assert_called_once_with(
            '2026-08-01',
            {
                'Отзывы': 0.25,
                'Инициативы': 0.10,
            },
            {
                'Дмитровка': (0.75, 2),
            },
            '@tester',
        )

    def test_owner_can_save_custom_goal(self):
        settings = {
            'period_month': '2026-08-01', 'metrics': [], 'clubs': [],
            'custom_goals': [],
        }
        payload = {
            'month': '2026-08', 'name': 'Повторы', 'hashtag': '#повторы',
            'calculation_type': 'per_unit', 'audience': 'physical',
            'unit_label': 'шт.', 'value': 1, 'contribution_pct': 0.1,
            'min_profile_shifts': None,
        }
        with (
            patch.object(kpi_web, 'TELEGRAM_API_KEY', BOT_TOKEN),
            patch.object(kpi_web, 'get_user', return_value=user(3)),
            patch.object(
                kpi_web, 'save_custom_goal', return_value='goal-1',
            ) as save_goal,
            patch.object(kpi_web, 'get_kpi_settings', return_value=settings),
        ):
            response = self.client.post(
                '/api/kpi/goals', headers=self.headers, json=payload,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['saved_goal_key'], 'goal-1')
        expected = dict(payload)
        expected.pop('month')
        save_goal.assert_called_once_with('2026-08-01', expected, '@tester')

    def test_manager_cannot_save_custom_goal(self):
        with (
            patch.object(kpi_web, 'TELEGRAM_API_KEY', BOT_TOKEN),
            patch.object(kpi_web, 'get_user', return_value=user(2)),
        ):
            response = self.client.post(
                '/api/kpi/goals', headers=self.headers,
                json={'month': '2026-08'},
            )
        self.assertEqual(response.status_code, 403)

    def test_month_close_preview_only_warns_about_detected_problems(self):
        rows = [
            {
                'login': '@red',
                'nickname': 'Red',
                'shifts': 2,
                'total_pct': 0.4,
                'zone': '🔴',
                'penalties': 1,
                'reviews': 0,
                'forms': 0,
                'extensions': 0,
                'certificates': 0,
                'subscriptions': 0,
                'initiatives': 0,
            },
            {
                'login': '@idle',
                'nickname': 'Idle',
                'shifts': 0,
                'total_pct': 0,
                'zone': '⚪',
                'penalties': 0,
            },
        ]
        with (
            patch.object(kpi_web, '_active_employee_logins', return_value=[
                '@red',
                '@idle',
            ]),
            patch.object(
                kpi_web,
                'calculate_monthly_kpi',
                return_value=rows,
            ),
            patch.object(kpi_web, '_employee_metadata', return_value={}),
        ):
            preview = kpi_web._month_close_preview('2026-07-01')

        self.assertEqual(preview['summary']['active_employees'], 2)
        self.assertEqual(preview['summary']['participants'], 1)
        self.assertEqual(
            [warning['key'] for warning in preview['warnings']],
            ['no_shifts', 'no_kpi', 'red_zone', 'penalties'],
        )

    def test_manager_closes_month_with_server_snapshot(self):
        snapshot = {
            'period_month': '2026-07-01',
            'date': '2026-07-31',
            'generated_at': '2026-07-31T20:00:00+00:00',
            'summary': {'participants': 1},
            'warnings': [],
            'employees': [],
        }
        saved_status = {
            'period_month': '2026-07-01',
            'is_closed': True,
            'updated_by_login': '@tester',
            'updated_at': '2026-07-31 23:00:00',
            'snapshot': snapshot,
            'snapshot_created_at': '2026-07-31 23:00:00',
        }
        with (
            patch.object(kpi_web, 'TELEGRAM_API_KEY', BOT_TOKEN),
            patch.object(kpi_web, 'get_user', return_value=user(2)),
            patch.object(
                kpi_web,
                '_month_close_preview',
                return_value=snapshot,
            ),
            patch.object(
                kpi_web,
                'set_month_status',
                return_value=saved_status,
            ) as set_status,
        ):
            response = self.client.post(
                '/api/month-status',
                headers=self.headers,
                json={'month': '2026-07', 'is_closed': True},
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['is_closed'])
        set_status.assert_called_once_with(
            '2026-07-01',
            True,
            '@tester',
            snapshot=snapshot,
        )

    def test_manager_can_reopen_month_without_rebuilding_snapshot(self):
        with (
            patch.object(kpi_web, 'TELEGRAM_API_KEY', BOT_TOKEN),
            patch.object(kpi_web, 'get_user', return_value=user(2)),
            patch.object(kpi_web, '_month_close_preview') as preview,
            patch.object(
                kpi_web,
                'set_month_status',
                return_value={
                    'period_month': '2026-07-01',
                    'is_closed': False,
                },
            ) as set_status,
        ):
            response = self.client.post(
                '/api/month-status',
                headers=self.headers,
                json={'month': '2026-07', 'is_closed': False},
            )

        self.assertEqual(response.status_code, 200)
        preview.assert_not_called()
        set_status.assert_called_once_with(
            '2026-07-01',
            False,
            '@tester',
            snapshot=None,
        )


if __name__ == '__main__':
    unittest.main()
