import types
import unittest
from unittest.mock import Mock, patch

import auth


class AuthNavigationTest(unittest.TestCase):
    def test_registration_can_be_cancelled_from_any_text_step(self):
        bot = Mock()
        message = types.SimpleNamespace(
            text=auth.CANCEL_REGISTRATION,
            chat=types.SimpleNamespace(id=123),
        )

        with patch.object(
            auth.types,
            'ReplyKeyboardRemove',
            return_value='remove-keyboard',
        ):
            cancelled = auth._registration_cancelled(message, bot)

        self.assertTrue(cancelled)
        bot.clear_step_handler_by_chat_id.assert_called_once_with(123)
        self.assertEqual(
            bot.send_message.call_args.kwargs['reply_markup'],
            'remove-keyboard',
        )


if __name__ == '__main__':
    unittest.main()
