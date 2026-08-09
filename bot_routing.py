import threading
import time

import telebot


class CommandAwareTeleBot(telebot.TeleBot):
    """Lets slash commands interrupt an unfinished next-step dialog."""

    def _notify_next_handlers(self, new_messages):
        for message in new_messages:
            text = (getattr(message, 'text', None) or '').lstrip()
            if text.startswith('/'):
                self.clear_step_handler_by_chat_id(message.chat.id)
        return super()._notify_next_handlers(new_messages)


class CommandCooldown:
    """Thread-safe cooldown that does not block unrelated commands."""

    def __init__(self, seconds):
        self.seconds = seconds
        self._last_calls = {}
        self._lock = threading.Lock()

    def allow(self, user_id, command):
        key = (str(user_id), command)
        now = time.monotonic()
        with self._lock:
            previous = self._last_calls.get(key)
            if previous is not None and now - previous < self.seconds:
                return False
            self._last_calls[key] = now
            return True
