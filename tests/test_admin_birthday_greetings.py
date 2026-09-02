import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import admin_panel
import birthday_greetings
import constants


class AdminBirthdayGreetingsTest(unittest.TestCase):
    def test_test_button_is_available_to_managers_and_owners(self):
        self.assertIn('🎂 Тест поздравления', constants.admin_extra_funclist)
        self.assertIn('🎂 Тест поздравления', constants.owner_admin_extra_funclist)

    def test_preview_is_sent_only_to_requesting_admin_chat(self):
        message = SimpleNamespace(
            text='@tester',
            chat=SimpleNamespace(id=123),
            from_user=SimpleNamespace(id=456),
        )
        bot = Mock()
        bot.send_message.side_effect = [
            SimpleNamespace(message_id=10),
            SimpleNamespace(message_id=11),
            SimpleNamespace(message_id=12),
        ]
        user = {'login': '@tester'}
        preview = {
            'source': 'openrouter',
            'text': '🎂 Тестовое поздравление\n\n— Виарыч 🤖💜',
        }

        with (
            patch.object(
                admin_panel,
                'require_role',
                return_value={'ID': 9, 'status': 2},
            ),
            patch.object(
                birthday_greetings,
                'build_birthday_preview',
                return_value=(user, preview),
            ) as build,
            patch.object(
                admin_panel.types,
                'ReplyKeyboardMarkup',
                return_value=Mock(),
            ),
        ):
            admin_panel.birthday_test_generate(message, bot)

        build.assert_called_once_with('@tester')
        target_chats = [call.args[0] for call in bot.send_message.call_args_list]
        self.assertTrue(target_chats)
        self.assertEqual(set(target_chats), {123})
        preview_call = next(
            call for call in bot.send_message.call_args_list
            if '🧪 Тест для @tester' in call.args[1]
        )
        self.assertIn('— Виарыч 🤖💜', preview_call.args[1])

    def test_fallback_preview_contains_openrouter_error(self):
        message = SimpleNamespace(
            text='@tester',
            chat=SimpleNamespace(id=123),
            from_user=SimpleNamespace(id=456),
        )
        bot = Mock()
        bot.send_message.return_value = SimpleNamespace(message_id=10)
        preview = {
            'source': 'fallback',
            'generation_error': 'HTTPError: 402 Payment Required',
            'text': 'Резервное поздравление',
        }

        with (
            patch.object(
                admin_panel,
                'require_role',
                return_value={'ID': 9, 'status': 2},
            ),
            patch.object(
                birthday_greetings,
                'build_birthday_preview',
                return_value=({'login': '@tester'}, preview),
            ),
            patch.object(
                admin_panel.types,
                'ReplyKeyboardMarkup',
                return_value=Mock(),
            ),
        ):
            admin_panel.birthday_test_generate(message, bot)

        texts = [call.args[1] for call in bot.send_message.call_args_list]
        self.assertTrue(any('402 Payment Required' in text for text in texts))


if __name__ == '__main__':
    unittest.main()
