import types
import unittest
from unittest.mock import Mock, patch

import telebot

from bot_routing import CommandAwareTeleBot, CommandCooldown


class CommandAwareTeleBotTest(unittest.TestCase):
    def test_command_clears_pending_step_before_handler_dispatch(self):
        bot = object.__new__(CommandAwareTeleBot)
        bot.clear_step_handler_by_chat_id = Mock()
        message = types.SimpleNamespace(
            text=' /weather',
            chat=types.SimpleNamespace(id=123),
        )

        with patch.object(telebot.TeleBot, '_notify_next_handlers', return_value=None) as base:
            bot._notify_next_handlers([message])

        bot.clear_step_handler_by_chat_id.assert_called_once_with(123)
        base.assert_called_once_with([message])

    def test_regular_message_keeps_pending_step(self):
        bot = object.__new__(CommandAwareTeleBot)
        bot.clear_step_handler_by_chat_id = Mock()
        message = types.SimpleNamespace(
            text='Открыть смену',
            chat=types.SimpleNamespace(id=123),
        )

        with patch.object(telebot.TeleBot, '_notify_next_handlers', return_value=None):
            bot._notify_next_handlers([message])

        bot.clear_step_handler_by_chat_id.assert_not_called()


class CommandCooldownTest(unittest.TestCase):
    def test_only_repeated_same_command_is_blocked(self):
        cooldown = CommandCooldown(seconds=10)

        self.assertTrue(cooldown.allow(123, 'start'))
        self.assertFalse(cooldown.allow(123, 'start'))
        self.assertTrue(cooldown.allow(123, 'weather'))
        self.assertTrue(cooldown.allow(456, 'start'))


if __name__ == '__main__':
    unittest.main()
