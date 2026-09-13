import types
import unittest
from unittest.mock import Mock, patch

import openclose


class OpenCloseFlowChoiceTest(unittest.TestCase):
    def test_employee_can_only_open_mini_app_flow(self):
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
        self.assertEqual(buttons[1].callback_data, 'shift_flow:cancel')
        self.assertEqual(len(buttons), 2)

    def test_old_bot_steps_redirect_without_updating_records(self):
        bot = Mock()
        message = types.SimpleNamespace(chat=types.SimpleNamespace(id=123))
        with patch.object(openclose, 'choose_shift_flow') as redirect:
            openclose.confirm_enter(message, '✅ Открыть смену', 'Test', False, False, bot)
            redirect.assert_called_once_with(message, '✅ Открыть смену', bot)


if __name__ == '__main__':
    unittest.main()
