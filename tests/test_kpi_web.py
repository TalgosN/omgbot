import hashlib
import hmac
import json
import time
import unittest
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
                side_effect=[[row], []],
            ),
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
        self.assertEqual(
            payload['freshness']['latest_metric_date'],
            '2026-07-14',
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


if __name__ == '__main__':
    unittest.main()
