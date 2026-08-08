import unittest
import sqlite3
import tempfile
from datetime import date, datetime
from pathlib import Path
from unittest.mock import Mock, patch

import bukza


def bukza_row(
    order_id,
    start,
    *,
    end=None,
    paid=0,
    participants=5,
    resource='Клуб',
    status='Подтверждено',
    number=None,
):
    values = {
        'bukza_start_date': (start, start),
        'bukza_end_date': (end or start, end or start),
        'bukza_paid': (paid, str(paid)),
        'bukza_shares': (participants, str(participants)),
        'bukza_resource_system_name': (resource, resource),
        'bukza_reservation_status': (status, status),
        'bukza_order_number': (number or order_id, number or order_id),
    }
    return {
        'orderId': order_id,
        'cells': [
            {'type': key, 'value': value, 'formatted': formatted}
            for key, (value, formatted) in values.items()
        ],
    }


class BukzaTest(unittest.TestCase):
    def test_period_covers_current_sunday_and_two_following_weekends(self):
        self.assertEqual(
            bukza.notification_period(date(2026, 8, 7)),
            (date(2026, 8, 7), date(2026, 8, 23)),
        )

    def test_filter_keeps_only_unpaid_large_or_event_weekend_orders(self):
        rows = [
            bukza_row('large', '2026-08-08', participants=5),
            bukza_row('event', '2026-08-09', participants=1, resource='Мероприятие'),
            bukza_row('weekday-event', '2026-08-10', participants=10, resource='Мероприятие'),
            bukza_row('small', '2026-08-15', participants=4),
            bukza_row('paid', '2026-08-15', paid=1),
            bukza_row('cancelled', '2026-08-16', status='Отменено'),
            bukza_row('outside', '2026-08-29', participants=10),
        ]

        result = bukza.unpaid_weekend_orders(
            rows,
            date(2026, 8, 7),
            date(2026, 8, 23),
        )

        self.assertEqual([item['id'] for item in result], ['large', 'event'])

    def test_fetch_reservations_uses_login_server_and_inclusive_period(self):
        login_response = Mock()
        login_response.json.return_value = {
            'token': 'bukza-token',
            'serverUrl': 'https://tenant.bukza.test/',
        }
        table_response = Mock()
        table_response.json.return_value = {'rows': []}

        with patch.object(bukza.requests, 'post', side_effect=[
            login_response,
            table_response,
        ]) as post, patch.object(bukza, 'BUKZA_EMAIL', 'user@example.com'), \
                patch.object(bukza, 'BUKZA_PASSWORD', 'secret'):
            rows = bukza.fetch_reservations(
                date(2026, 8, 7),
                date(2026, 8, 23),
            )

        self.assertEqual(rows, [])
        self.assertEqual(
            post.call_args_list[1].args[0],
            'https://tenant.bukza.test/api/reservation-tables/data',
        )
        self.assertEqual(
            post.call_args_list[1].kwargs['json']['till'],
            '2026-08-24T00:00:00.000Z',
        )
        self.assertEqual(
            post.call_args_list[1].kwargs['headers']['Authorization'],
            'Bearer bukza-token',
        )

    def test_empty_result_does_not_send_message(self):
        bot = Mock()
        with patch.object(bukza, 'BUKZA_EMAIL', 'user@example.com'), \
                patch.object(bukza, 'BUKZA_PASSWORD', 'secret'), \
                patch.object(
                    bukza,
                    'booking_freshness',
                    return_value={'stale': False, 'age_minutes': 1},
                ), \
                patch.object(bukza, 'load_orders', return_value=[]):
            result = bukza.send_daily_notification(
                bot,
                today=date(2026, 8, 7),
            )

        self.assertEqual(result, 0)
        bot.send_message.assert_not_called()

    def test_notification_is_sent_to_callcenter_as_html(self):
        bot = Mock()
        rows = [bukza_row('42', '2026-08-08', number='A&B')]
        orders = [bukza._canonical_order(rows[0])]
        with patch.object(bukza, 'BUKZA_EMAIL', 'user@example.com'), \
                patch.object(bukza, 'BUKZA_PASSWORD', 'secret'), \
                patch.object(
                    bukza,
                    'booking_freshness',
                    return_value={'stale': False, 'age_minutes': 1},
                ), \
                patch.object(bukza, 'load_orders', return_value=orders), \
                patch.dict(bukza.CHATS, {'callcenter': '-851937975'}):
            result = bukza.send_daily_notification(
                bot,
                today=date(2026, 8, 7),
            )

        self.assertEqual(result, 1)
        args, kwargs = bot.send_message.call_args
        self.assertEqual(args[0], '-851937975')
        self.assertIn('A&amp;B', args[1])
        self.assertEqual(kwargs['parse_mode'], 'HTML')

    def test_notification_reads_database_without_bukza_credentials(self):
        bot = Mock()
        with patch.object(bukza, 'BUKZA_EMAIL', ''), \
                patch.object(bukza, 'BUKZA_PASSWORD', ''), \
                patch.object(
                    bukza,
                    'booking_freshness',
                    return_value={'stale': False, 'age_minutes': 1},
                ), \
                patch.object(bukza, 'load_orders', return_value=[]), \
                patch.dict(bukza.CHATS, {'callcenter': '-851937975'}):
            result = bukza.send_daily_notification(
                bot,
                today=date(2026, 8, 8),
            )

        self.assertEqual(result, 0)

    def test_range_fetch_logs_in_once_and_splits_period(self):
        calls = []

        def fetch(_server, _token, day_from, day_to):
            calls.append((day_from, day_to))
            return [bukza_row(day_from.isoformat(), day_from.isoformat())]

        with patch.object(bukza, '_login', return_value=('server', 'token')) as login, \
                patch.object(bukza, '_fetch_reservations', side_effect=fetch):
            rows = bukza.fetch_reservations_range(
                date(2026, 1, 1),
                date(2026, 7, 31),
            )

        login.assert_called_once_with()
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(rows), 3)
        self.assertEqual(calls[0], (date(2026, 1, 1), date(2026, 4, 3)))
        self.assertEqual(calls[-1][1], date(2026, 7, 31))

    def test_order_storage_updates_current_state_and_writes_history(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / 'bukza.sqlite')
            original = {
                'id': '42',
                'number': 'A-42',
                'reservation_at': '2026-08-08T12:00:00',
                'reservation_end_at': '2026-08-08T13:00:00',
                'date': date(2026, 8, 8),
                'status': 'Подтверждено',
                'resource': 'МАР > Классический VR',
                'club_code': 'МАР',
                'club': 'Марьино',
                'booking_format': 'Классический VR',
                'participants': 5.0,
                'paid': 0.0,
                'source_present': 1,
                'url': 'https://example.test/42',
            }
            first = bukza._store_orders(
                [original],
                date(2019, 1, 1),
                date(2027, 8, 8),
                True,
                db_path,
            )
            changed = {**original, 'status': 'Оплачено', 'paid': 1500.0}
            second = bukza._store_orders(
                [changed],
                date(2026, 7, 9),
                date(2027, 8, 8),
                False,
                db_path,
            )

            conn = sqlite3.connect(db_path)
            current = conn.execute(
                'SELECT status, paid FROM bukza_orders WHERE order_id=?',
                ('42',),
            ).fetchone()
            history = conn.execute(
                '''SELECT field, old_value, new_value
                   FROM bukza_order_history
                   WHERE order_id=? ORDER BY id''',
                ('42',),
            ).fetchall()
            state = conn.execute(
                "SELECT value FROM bukza_sync_state WHERE key='initial_backfill_complete'"
            ).fetchone()
            conn.close()

        self.assertEqual(first['inserted'], 1)
        self.assertEqual(second['updated'], 1)
        self.assertEqual(current, ('Оплачено', 1500.0))
        self.assertEqual(history, [
            ('created', None, 'A-42'),
            ('status', 'Подтверждено', 'Оплачено'),
            ('paid', '0', '1500'),
        ])
        self.assertEqual(state, ('1',))

    def test_canonical_order_contains_end_time_club_and_format(self):
        order = bukza._canonical_order(bukza_row(
            '42',
            '08.08.2026 11:30',
            end='08.08.2026 12:30',
            resource='ЛЕН > VR Квесты UBISOFT',
        ))

        self.assertEqual(order['reservation_at'], '2026-08-08T11:30:00')
        self.assertEqual(order['reservation_end_at'], '2026-08-08T12:30:00')
        self.assertEqual(order['club'], 'Ленинский')
        self.assertEqual(order['booking_format'], 'VR Квесты UBISOFT')

    def test_missing_order_is_marked_without_deletion(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / 'bukza.sqlite')
            order = bukza._canonical_order(bukza_row(
                '42',
                '08.08.2026 11:30',
                end='08.08.2026 12:30',
                resource='ЛЕН > Классический VR',
            ))
            bukza._store_orders(
                [order],
                date(2026, 8, 8),
                date(2026, 8, 8),
                True,
                db_path,
            )
            result = bukza._store_orders(
                [],
                date(2026, 8, 8),
                date(2026, 8, 8),
                False,
                db_path,
                sync_kind='live',
            )
            conn = sqlite3.connect(db_path)
            present = conn.execute(
                'SELECT source_present FROM bukza_orders WHERE order_id=?',
                ('42',),
            ).fetchone()[0]
            history = conn.execute(
                '''SELECT old_value, new_value FROM bukza_order_history
                   WHERE order_id=? AND field='source_present' ''',
                ('42',),
            ).fetchone()
            conn.close()

        self.assertEqual(result['missing'], 1)
        self.assertEqual(present, 0)
        self.assertEqual(history, ('1', '0'))

    def test_suspiciously_incomplete_snapshot_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / 'bukza.sqlite')
            orders = [
                bukza._canonical_order(bukza_row(
                    str(index),
                    '08.08.2026 11:30',
                    resource='ЛЕН > Классический VR',
                ))
                for index in range(10)
            ]
            bukza._store_orders(
                orders,
                date(2026, 8, 8),
                date(2026, 8, 8),
                True,
                db_path,
            )
            with self.assertRaisesRegex(RuntimeError, 'неполный снимок'):
                bukza._store_orders(
                    [],
                    date(2026, 8, 8),
                    date(2026, 8, 8),
                    False,
                    db_path,
                    sync_kind='live',
                )
            conn = sqlite3.connect(db_path)
            present = conn.execute(
                'SELECT SUM(source_present) FROM bukza_orders'
            ).fetchone()[0]
            conn.close()

        self.assertEqual(present, 10)

    def test_stale_data_prevents_callcenter_notification(self):
        bot = Mock()
        with patch.object(bukza, 'BUKZA_EMAIL', 'user@example.com'), \
                patch.object(bukza, 'BUKZA_PASSWORD', 'secret'), \
                patch.object(
                    bukza,
                    'booking_freshness',
                    return_value={'stale': True, 'age_minutes': 30},
                ), patch.dict(
                    bukza.CHATS,
                    {'callcenter': '-851937975', 'me': 'owner'},
                ):
            result = bukza.send_daily_notification(
                bot,
                today=date(2026, 8, 8),
            )

        self.assertIsNone(result)
        bot.send_message.assert_called_once()
        self.assertEqual(bot.send_message.call_args.args[0], 'owner')

    def test_upcoming_unpaid_orders_excludes_past_paid_and_cancelled(self):
        now = bukza.MOSCOW.localize(datetime(2026, 8, 8, 12, 0))
        orders = [
            bukza._canonical_order(bukza_row(
                'future', '08.08.2026 13:00', end='08.08.2026 14:00',
                resource='КАШ > Классический VR',
            )),
            bukza._canonical_order(bukza_row(
                'past', '08.08.2026 11:00', paid=0,
                resource='КАШ > Классический VR',
            )),
            bukza._canonical_order(bukza_row(
                'paid', '08.08.2026 15:00', paid=100,
                resource='КАШ > Классический VR',
            )),
            bukza._canonical_order(bukza_row(
                'cancelled', '08.08.2026 16:00', status='Отменено',
                resource='КАШ > Классический VR',
            )),
        ]
        with patch.object(bukza, 'load_orders', return_value=orders):
            result = bukza.upcoming_unpaid_orders(now=now)

        self.assertEqual([order['id'] for order in result], ['future'])

    def test_empty_club_scope_does_not_expose_other_clubs(self):
        order = bukza._canonical_order(bukza_row(
            '42',
            '08.08.2026 13:00',
            resource='МАР > Классический VR',
        ))
        with patch.object(bukza, 'load_orders', return_value=[order]):
            result = bukza.active_orders_for_day(
                date(2026, 8, 8),
                clubs=[],
            )

        self.assertEqual(result, [])


if __name__ == '__main__':
    unittest.main()
