import hashlib
import hmac
import json
import sqlite3
import tempfile
import time
import unittest
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

    def test_init_data_signature_and_age_are_validated(self):
        self.assertEqual(
            kpi_web._validate_init_data(
                signed_init_data(777),
                BOT_TOKEN,
            )['telegram_id'],
            777,
        )
        self.assertIsNone(kpi_web._validate_init_data('hash=wrong', BOT_TOKEN))

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
