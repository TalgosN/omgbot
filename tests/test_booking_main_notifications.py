import tempfile
import types
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

import bronix
from test_bronix import booking_row


class BookingMainNotificationsTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db_path = str(Path(self.directory.name) / 'bookings.sqlite')
        bronix.initialize_booking_schema(self.db_path)
        self.now = bronix._moscow_datetime('2026-09-12T09:00:00+03:00')
        self.bot = Mock()
        self.schedule = {'ok': True, 'days': [{
            'date': '2026-09-12', 'locations': [{
                'title': 'Марьино', 'shifts': [{
                    'employee': 'Иван', 'telegram': '@ivan',
                    'start': '10:00', 'end': '22:00',
                }],
            }],
        }]}

    def store(self, *rows):
        bronix._store_bookings(
            [bronix._canonical_booking(row) for row in rows],
            date(2026, 9, 12), date(2026, 9, 18), True, self.db_path, sync_kind='live',
        )

    def send(self, kind):
        rasp = types.SimpleNamespace(refresh_shift_schedule=Mock(return_value=self.schedule))
        with patch.dict('sys.modules', {'rasp': rasp}), \
                patch.object(bronix, 'booking_freshness', return_value={'stale': False}), \
                patch('sender.safe_send', side_effect=lambda bot, *args, **kwargs: bot.send_message(*args, **kwargs)), \
                patch.dict(bronix.CHATS, {'main_group': 'main'}):
            return bronix.send_main_booking_notifications(self.bot, kind, self.now, self.db_path)

    def test_quests_group_by_club_and_deduplicate_source_rows_and_restarts(self):
        self.store(
            booking_row('quest1', booking_format='VR-квест', participants=4),
            booking_row('quest2', booking_format='VR-квест', participants=4),
            booking_row('cancelled', number='cancelled', booking_format='VR-квест', active=False),
        )
        self.assertEqual(self.send('quests'), 1)
        self.assertEqual(self.send('quests'), 0)
        text = self.bot.send_message.call_args.args[1]
        self.assertTrue(text.startswith('@omgvr_mar\n'))
        self.assertEqual(text.count('12:00–13:00'), 1)
        self.assertNotIn('#', text)

    def test_failed_delivery_can_be_retried(self):
        self.store(booking_row(booking_format='VR-квест'))
        self.bot.send_message.side_effect = RuntimeError('telegram unavailable')
        with self.assertRaises(RuntimeError):
            self.send('quests')
        self.bot.send_message.side_effect = None
        self.assertEqual(self.send('quests'), 1)

    def test_unconfirmed_delivery_is_not_marked_as_sent(self):
        self.store(booking_row(booking_format='VR-квест'))
        self.bot.send_message.return_value = None
        with self.assertRaisesRegex(RuntimeError, 'не подтвердил'):
            self.send('quests')
        self.bot.send_message.return_value = Mock(message_id=1)
        self.assertEqual(self.send('quests'), 1)

    def test_unpublished_club_day_and_two_simultaneous_employees_do_not_warn(self):
        self.store(booking_row(is_event=True, participants=8))
        self.schedule['days'][0]['locations'][0]['shifts'] = []
        self.assertEqual(self.send('staffing'), 0)
        self.schedule['days'][0]['locations'][0]['shifts'] = [
            {'employee': 'Иван', 'telegram': '@ivan', 'start': '10:00', 'end': '22:00'},
            {'employee': 'Пётр', 'telegram': '@petr', 'start': '10:00', 'end': '22:00'},
        ]
        self.assertEqual(self.send('staffing'), 0)
        self.bot.send_message.assert_not_called()

    def test_staffing_counts_overlaps_not_daily_totals_and_rechecks_later_schedule(self):
        self.store(
            booking_row('first', number='1', participants=3),
            booking_row('second', number='2', participants=3,
                        start='2026-09-12T14:00:00+03:00', end='2026-09-12T15:00:00+03:00'),
        )
        self.assertEqual(self.send('staffing'), 0)
        self.store(
            booking_row('first', number='1', participants=3),
            booking_row('second', number='2', participants=3,
                        start='2026-09-12T12:30:00+03:00', end='2026-09-12T13:30:00+03:00'),
        )
        shifts = self.schedule['days'][0]['locations'][0]['shifts']
        self.schedule['days'][0]['locations'][0]['shifts'] = []
        self.assertEqual(self.send('staffing'), 0)
        self.schedule['days'][0]['locations'][0]['shifts'] = shifts
        self.assertEqual(self.send('staffing'), 1)
        self.assertEqual(self.send('staffing'), 0)
        text = self.bot.send_message.call_args.args[1]
        self.assertIn('<b>Нужен второй сотрудник</b>', text)
        self.assertIn('12:30–13:00', text)
        self.assertIn('на 6 гостей', text)
        self.assertIn('работает 1 сотрудник.', text)

    def test_staff_boundary_and_free_slots_are_not_counted_as_second_employee(self):
        bookings = [bronix._canonical_booking(booking_row(is_event=True, participants=3))]
        shifts = self.schedule['days'][0]['locations'][0]['shifts']
        shifts.append({'employee': 'Пётр', 'telegram': '@petr', 'start': '12:30', 'end': '20:00'})
        alerts = bronix._staffing_alerts(bookings, self.schedule, self.now)
        self.assertEqual([(a['start'].strftime('%H:%M'), a['end'].strftime('%H:%M'))
                          for a in alerts], [('12:00', '12:30')])
        shifts[1] = {'employee': 'СВОБОДНАЯ СМЕНА', 'start': '10:00', 'end': '22:00'}
        alerts = bronix._staffing_alerts(bookings, self.schedule, self.now)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]['staff'], 1)
        shifts[:] = [shifts[1]]
        self.assertEqual(bronix._staffing_alerts(bookings, self.schedule, self.now), [])

    def test_stale_bookings_do_not_generate_notifications(self):
        with patch.object(bronix, 'booking_freshness', return_value={'stale': True}), \
                patch.dict(bronix.CHATS, {'main_group': 'main'}):
            with self.assertRaisesRegex(RuntimeError, 'устарели'):
                bronix.send_main_booking_notifications(self.bot, 'quests', self.now, self.db_path)
        self.bot.send_message.assert_not_called()


if __name__ == '__main__':
    unittest.main()
