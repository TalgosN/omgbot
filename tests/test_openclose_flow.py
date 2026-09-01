import types
import unittest
from datetime import datetime
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import openclose


class OpenCloseFlowChoiceTest(unittest.TestCase):
    def test_employee_can_choose_legacy_or_mini_app_flow(self):
        bot = Mock()
        message = types.SimpleNamespace(
            chat=types.SimpleNamespace(id=123),
            from_user=types.SimpleNamespace(id=123, username='tester'),
        )

        with (
            patch.object(openclose, 'require_role', return_value={'status': 0}),
            patch('menu._webapp_url', return_value=(
                'https://bot.omg-vr.ru/shift-report?action=open'
            )),
        ):
            openclose.choose_shift_flow(
                message, '✅ Открыть смену', bot,
            )

        markup = bot.send_message.call_args.kwargs['reply_markup']
        buttons = [button for row in markup.keyboard for button in row]
        self.assertEqual(
            buttons[0].web_app.url,
            'https://bot.omg-vr.ru/shift-report?action=open',
        )
        self.assertEqual(
            buttons[1].callback_data,
            'shift_flow:open:legacy',
        )
        self.assertEqual(buttons[2].callback_data, 'shift_flow:cancel')

    def test_legacy_flow_warning_keeps_bot_and_app_choices(self):
        bot = Mock()
        message = types.SimpleNamespace(
            chat=types.SimpleNamespace(id=123),
            from_user=types.SimpleNamespace(id=123, username='tester'),
        )

        with patch('menu._webapp_url', return_value=(
            'https://bot.omg-vr.ru/shift-report?action=close'
        )):
            openclose.warn_legacy_shift_flow(
                message, '🚫 Закрыть смену', bot,
            )

        sent = bot.send_message.call_args
        self.assertIn('С 1 сентября', sent.args[1])
        self.assertIn('Попробуй сейчас', sent.args[1])
        self.assertEqual(sent.kwargs['parse_mode'], 'HTML')
        buttons = [
            button
            for row in sent.kwargs['reply_markup'].keyboard
            for button in row
        ]
        self.assertEqual(
            buttons[0].web_app.url,
            'https://bot.omg-vr.ru/shift-report?action=close',
        )
        self.assertEqual(
            buttons[1].callback_data,
            'shift_flow:close:legacy_confirmed',
        )

    def test_previous_shift_is_used_only_for_closing_before_six(self):
        bot = Mock()
        message = types.SimpleNamespace(
            chat=types.SimpleNamespace(id=123),
            from_user=types.SimpleNamespace(id=123, username='tester'),
        )
        current = datetime(
            2026, 8, 11, 1, 0, tzinfo=ZoneInfo('Europe/Moscow'),
        )
        with (
            patch.object(openclose, 'require_role', return_value={'status': 0}),
            patch.object(openclose, 'datetime', wraps=datetime) as date_mock,
            patch('kpi.get_user_club_today', side_effect=[None, 'Ленинский']) as lookup,
        ):
            date_mock.now.return_value = current
            allowed = openclose._can_manage_club(
                message, 'Ленинский', bot, action='🚫 Закрыть смену',
            )

        self.assertTrue(allowed)
        self.assertEqual(lookup.call_count, 2)

        with (
            patch.object(openclose, 'require_role', return_value={'status': 0}),
            patch.object(openclose, 'datetime', wraps=datetime) as date_mock,
            patch('kpi.get_user_club_today', return_value=None) as lookup,
        ):
            date_mock.now.return_value = current
            allowed = openclose._can_manage_club(
                message, 'Ленинский', bot, action='✅ Открыть смену',
            )

        self.assertFalse(allowed)
        lookup.assert_called_once_with('tester')


if __name__ == '__main__':
    unittest.main()
