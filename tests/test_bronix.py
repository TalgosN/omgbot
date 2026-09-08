import sqlite3
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import Mock, patch

import bronix


def booking_row(
    booking_id='booking_42',
    *,
    number='12345',
    start='2026-09-12T12:00:00+03:00',
    end='2026-09-12T13:00:00+03:00',
    status='confirmed',
    active=True,
    club='Марьино',
    booking_format='Классический VR',
    is_event=False,
    participants=5,
    paid='0.00',
):
    return {
        'id': booking_id,
        'number': number,
        'startAt': start,
        'endAt': end,
        'status': status,
        'active': active,
        'club': club,
        'format': booking_format,
        'isEvent': is_event,
        'participants': participants,
        'paidAmount': paid,
        'adminUrl': f'https://bronix.test/bookings/{booking_id}',
    }


class BronixTest(unittest.TestCase):
    def test_period_covers_current_sunday_and_two_following_weekends(self):
        self.assertEqual(
            bronix.notification_period(date(2026, 9, 8)),
            (date(2026, 9, 8), date(2026, 9, 27)),
        )

    def test_canonical_booking_contains_every_operational_field(self):
        result = bronix._canonical_booking(booking_row(
            booking_id='booking_31923',
            paid='2400.00',
            is_event=True,
            booking_format='Мероприятие',
        ))

        self.assertEqual(result['id'], 'booking_31923')
        self.assertEqual(result['reservation_at'], '2026-09-12T12:00:00+03:00')
        self.assertEqual(result['reservation_end_at'], '2026-09-12T13:00:00+03:00')
        self.assertEqual(result['club'], 'Марьино')
        self.assertEqual(result['paid'], 2400.0)
        self.assertEqual(result['is_event'], 1)
        self.assertEqual(result['url'], result['admin_url'])

    def test_canonical_booking_rejects_missing_or_naive_dates(self):
        row = booking_row()
        del row['paidAmount']
        with self.assertRaisesRegex(RuntimeError, 'paidAmount'):
            bronix._canonical_booking(row)
        with self.assertRaisesRegex(RuntimeError, 'часовой пояс'):
            bronix._canonical_booking(booking_row(
                start='2026-09-12T12:00:00',
            ))
        with self.assertRaisesRegex(RuntimeError, 'paidAmount'):
            bronix._canonical_booking(booking_row(paid='not-a-number'))
        with self.assertRaisesRegex(RuntimeError, 'дробное количество'):
            bronix._canonical_booking(booking_row(participants=1.5))

    def test_fetch_page_uses_bearer_token_and_cursor(self):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            'ok': True,
            'bookings': [booking_row()],
            'nextCursor': 'next-page',
        }
        with patch.object(bronix.requests, 'get', return_value=response) as get, \
                patch.object(bronix, 'BRONIX_BOT_API_TOKEN', 'secret'), \
                patch.object(bronix, 'BRONIX_BOT_API_URL', 'https://bronix.test/api'):
            rows, cursor = bronix._fetch_page(
                date(2026, 9, 1),
                date(2026, 9, 8),
                'current-page',
            )

        self.assertEqual(len(rows), 1)
        self.assertEqual(cursor, 'next-page')
        self.assertEqual(get.call_args.kwargs['params']['cursor'], 'current-page')
        self.assertEqual(
            get.call_args.kwargs['headers']['Authorization'],
            'Bearer secret',
        )

    def test_range_fetch_follows_pagination_and_splits_large_period(self):
        calls = []

        def fetch(day_from, day_to, cursor=None):
            calls.append((day_from, day_to, cursor))
            if cursor is None:
                return [booking_row(f'{day_from}-one')], 'next'
            return [booking_row(f'{day_from}-two')], None

        with patch.object(bronix, '_fetch_page', side_effect=fetch), \
                patch.object(bronix, 'BRONIX_BOT_API_TOKEN', 'secret'):
            rows = bronix.fetch_bookings_range(
                date(2025, 1, 1),
                date(2026, 2, 6),
            )

        self.assertEqual(len(rows), 4)
        self.assertEqual(calls[0][0], date(2025, 1, 1))
        self.assertEqual(calls[0][1], date(2026, 2, 5))
        self.assertEqual(calls[2][0], date(2026, 2, 6))

    def test_repeated_cursor_is_rejected(self):
        with patch.object(
            bronix,
            '_fetch_page',
            return_value=([booking_row()], 'same'),
        ), patch.object(bronix, 'BRONIX_BOT_API_TOKEN', 'secret'):
            with self.assertRaisesRegex(RuntimeError, 'зациклила'):
                bronix.fetch_bookings_range(
                    date(2026, 9, 1),
                    date(2026, 9, 1),
                )

    def test_legacy_tables_are_migrated_and_removed_without_data_loss(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / 'bookings.sqlite')
            conn = sqlite3.connect(db_path)
            conn.executescript(
                '''CREATE TABLE bukza_orders (
                       order_id TEXT PRIMARY KEY, order_number TEXT,
                       reservation_at TEXT, reservation_end_at TEXT,
                       status TEXT, resource TEXT, club_code TEXT, club TEXT,
                       booking_format TEXT, participants REAL, paid REAL,
                       source_present INTEGER, first_seen_at TEXT,
                       last_seen_at TEXT, last_changed_at TEXT
                   );
                   CREATE TABLE bukza_order_history (
                       id INTEGER PRIMARY KEY, order_id TEXT, changed_at TEXT,
                       field TEXT, old_value TEXT, new_value TEXT
                   );
                   CREATE TABLE bukza_sync_state (
                       key TEXT PRIMARY KEY, value TEXT, updated_at TEXT
                   );'''
            )
            conn.execute(
                '''INSERT INTO bukza_orders VALUES (
                       '42', 'A-42', '2026-09-12T12:00:00',
                       '2026-09-12T13:00:00', 'Ожидается',
                       'МАР > Классический VR', 'МАР', 'Марьино',
                       'Классический VR', 5, 0, 1,
                       '2026-09-01', '2026-09-01', '2026-09-01')'''
            )
            conn.execute(
                "INSERT INTO bukza_order_history VALUES (1, '42', '2026-09-01', 'created', NULL, 'A-42')"
            )
            conn.commit()
            conn.close()

            bronix.initialize_booking_schema(db_path)
            conn = sqlite3.connect(db_path)
            migrated = conn.execute(
                '''SELECT booking_id, source, source_present
                   FROM booking_orders'''
            ).fetchone()
            history = conn.execute(
                'SELECT booking_id FROM booking_order_history'
            ).fetchone()
            old_tables = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name LIKE 'bukza_%'"
            ).fetchone()[0]
            conn.close()

        self.assertEqual(migrated, ('legacy:42', 'legacy', 1))
        self.assertEqual(history, ('legacy:42',))
        self.assertEqual(old_tables, 0)

    def test_store_updates_booking_and_records_history(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / 'bookings.sqlite')
            original = bronix._canonical_booking(booking_row())
            changed = bronix._canonical_booking(booking_row(
                paid='2400.00',
            ))
            first = bronix._store_bookings(
                [original], date(2026, 9, 12), date(2026, 9, 12),
                True, db_path, sync_kind='full',
            )
            second = bronix._store_bookings(
                [changed], date(2026, 9, 12), date(2026, 9, 12),
                False, db_path, sync_kind='live',
            )
            conn = sqlite3.connect(db_path)
            paid = conn.execute(
                'SELECT paid FROM booking_orders WHERE booking_id=?',
                ('booking_42',),
            ).fetchone()[0]
            history = conn.execute(
                '''SELECT old_value, new_value FROM booking_order_history
                   WHERE booking_id=? AND field='paid' ''',
                ('booking_42',),
            ).fetchone()
            conn.close()

        self.assertEqual(first['inserted'], 1)
        self.assertEqual(second['updated'], 1)
        self.assertEqual(paid, 2400.0)
        self.assertEqual(history, ('0', '2400'))

    def test_initial_bronix_backfill_deactivates_legacy_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / 'bookings.sqlite')
            bronix.initialize_booking_schema(db_path)
            conn = sqlite3.connect(db_path)
            conn.execute(
                '''INSERT INTO booking_orders (
                       booking_id, booking_number, reservation_at,
                       reservation_end_at, status, active, club,
                       booking_format, is_event, participants, paid,
                       admin_url, source, source_present
                   ) VALUES ('legacy:1', '1', '2026-09-12T10:00:00',
                       '2026-09-12T11:00:00', 'Ожидается', 1, 'Марьино',
                       'Классический VR', 0, 5, 0, 'https://old.test',
                       'legacy', 1)'''
            )
            conn.commit()
            conn.close()
            bronix._store_bookings(
                [bronix._canonical_booking(booking_row())],
                date(2026, 9, 12), date(2026, 9, 12), True, db_path,
                sync_kind='full',
            )
            conn = sqlite3.connect(db_path)
            legacy = conn.execute(
                "SELECT active, source_present FROM booking_orders WHERE source='legacy'"
            ).fetchone()
            conn.close()
        self.assertEqual(legacy, (0, 0))

    def test_missing_booking_is_marked_without_deletion(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / 'bookings.sqlite')
            booking = bronix._canonical_booking(booking_row())
            bronix._store_bookings(
                [booking], date(2026, 9, 12), date(2026, 9, 12),
                True, db_path, sync_kind='full',
            )
            result = bronix._store_bookings(
                [], date(2026, 9, 12), date(2026, 9, 12),
                False, db_path, sync_kind='live',
            )
            conn = sqlite3.connect(db_path)
            state = conn.execute(
                'SELECT active, source_present FROM booking_orders'
            ).fetchone()
            conn.close()
        self.assertEqual(result['missing'], 1)
        self.assertEqual(state, (0, 0))

    def test_suspiciously_incomplete_snapshot_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / 'bookings.sqlite')
            bookings = [
                bronix._canonical_booking(booking_row(f'booking_{index}'))
                for index in range(10)
            ]
            bronix._store_bookings(
                bookings, date(2026, 9, 12), date(2026, 9, 12),
                True, db_path, sync_kind='full',
            )
            with self.assertRaisesRegex(RuntimeError, 'неполный снимок'):
                bronix._store_bookings(
                    [], date(2026, 9, 12), date(2026, 9, 12),
                    False, db_path, sync_kind='live',
                )

    def test_unpaid_weekend_filter_uses_active_event_and_participants(self):
        rows = [
            bronix._canonical_booking(booking_row('large', participants=5)),
            bronix._canonical_booking(booking_row(
                'event', participants=1, is_event=True,
                booking_format='Мероприятие',
            )),
            bronix._canonical_booking(booking_row(
                'small', participants=4,
            )),
            bronix._canonical_booking(booking_row('paid', paid='1.00')),
            bronix._canonical_booking(booking_row(
                'cancelled', status='cancelled', active=False,
            )),
        ]
        result = bronix.unpaid_weekend_bookings(
            rows, date(2026, 9, 12), date(2026, 9, 13),
        )
        self.assertEqual([row['id'] for row in result], ['event', 'large'])

    def test_empty_club_scope_does_not_expose_other_clubs(self):
        booking = bronix._canonical_booking(booking_row())
        with patch.object(bronix, 'load_bookings', return_value=[booking]):
            result = bronix.active_bookings_for_day(
                date(2026, 9, 12), clubs=[],
            )
        self.assertEqual(result, [])

    def test_upcoming_unpaid_excludes_past_paid_and_cancelled(self):
        now = bronix.MOSCOW.localize(datetime(2026, 9, 12, 12, 0))
        rows = [
            bronix._canonical_booking(booking_row(
                'future', start='2026-09-12T13:00:00+03:00',
                end='2026-09-12T14:00:00+03:00',
            )),
            bronix._canonical_booking(booking_row(
                'past', start='2026-09-12T10:00:00+03:00',
                end='2026-09-12T11:00:00+03:00',
            )),
            bronix._canonical_booking(booking_row('paid', paid='10.00')),
            bronix._canonical_booking(booking_row(
                'cancelled', active=False, status='cancelled',
            )),
        ]
        with patch.object(bronix, 'load_bookings', return_value=rows):
            result = bronix.upcoming_unpaid_bookings(now=now)
        self.assertEqual([row['id'] for row in result], ['future'])

    def test_notification_is_sent_to_callcenter_as_html(self):
        bot = Mock()
        booking = bronix._canonical_booking(booking_row(number='A&B'))
        with patch.object(bronix, 'booking_freshness', return_value={
            'stale': False, 'age_minutes': 1,
        }), patch.object(
            bronix, 'load_bookings', return_value=[booking],
        ), patch.dict(bronix.CHATS, {'callcenter': '-851937975'}):
            result = bronix.send_daily_notification(
                bot, today=date(2026, 9, 8),
            )
        self.assertEqual(result, 1)
        args, kwargs = bot.send_message.call_args
        self.assertEqual(args[0], '-851937975')
        self.assertIn('A&amp;B', args[1])
        self.assertEqual(kwargs['parse_mode'], 'HTML')

    def test_stale_data_prevents_callcenter_notification(self):
        bot = Mock()
        with patch.object(bronix, 'booking_freshness', return_value={
            'stale': True, 'age_minutes': 30,
        }), patch.dict(
            bronix.CHATS, {'callcenter': '-851937975', 'me': 'owner'},
        ):
            result = bronix.send_daily_notification(bot)
        self.assertIsNone(result)
        bot.send_message.assert_called_once()
        self.assertEqual(bot.send_message.call_args.args[0], 'owner')

    def test_test_command_uses_daily_notification(self):
        bot = Mock()
        message = Mock()
        message.chat.id = 123
        with patch.object(
            bronix, 'send_daily_notification', return_value=2,
        ) as notification, patch.dict(
            bronix.CHATS, {'callcenter': '-851937975'},
        ):
            result = bronix.send_test_notification(message, bot)
        self.assertEqual(result, 2)
        notification.assert_called_once_with(bot)
        bot.send_message.assert_called_once_with(
            123,
            '✅ Отчёт отправлен в чат Коллцентра. Броней: 2.',
        )


if __name__ == '__main__':
    unittest.main()
