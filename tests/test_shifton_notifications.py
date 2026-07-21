import importlib.util
import locale
import sys
import threading
import time
import types
import unittest
from datetime import timedelta, timezone
from unittest.mock import Mock, patch


def load_rasp_module():
    telebot = types.ModuleType("telebot")
    telebot.__all__ = []
    sheets = types.ModuleType("sheets")
    sheets.__all__ = []
    constants = types.ModuleType("constants")
    constants.__all__ = ["SHIFTON_API_URL", "SHIFTON_API_TOKEN"]
    constants.SHIFTON_API_URL = "http://shifton.test"
    constants.SHIFTON_API_TOKEN = "test-token"
    weather = types.ModuleType("weather")
    weather.get_weather = lambda: ""
    pytz = types.ModuleType("pytz")
    pytz.timezone = lambda _name: timezone(timedelta(hours=3))

    modules = {
        "telebot": telebot,
        "sheets": sheets,
        "constants": constants,
        "weather": weather,
        "pytz": pytz,
    }
    with patch.dict(sys.modules, modules), patch.object(locale, "setlocale"):
        spec = importlib.util.spec_from_file_location("rasp_under_test", "rasp.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


class ShiftonNotificationsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rasp = load_rasp_module()

    def test_register_chat_payload(self):
        response = Mock()
        response.json.return_value = {"ok": True}

        with patch.object(self.rasp.requests, "post", return_value=response) as post:
            result = self.rasp.register_shifton_chat("@employee", 12345)

        self.assertEqual(result, {"ok": True})
        post.assert_called_once_with(
            "http://shifton.test/api/bot/register-chat",
            json={"telegram": "@employee", "chatId": 12345},
            headers={
                "Authorization": "Bearer test-token",
                "Content-Type": "application/json",
            },
            timeout=10,
        )

    def test_successful_notification_is_completed(self):
        bot = Mock()
        queue = [
            {"ok": True, "notification": {"id": 17, "chatId": 12345, "text": "Смена изменена"}},
            {"ok": True, "notification": None},
        ]

        with patch.object(self.rasp, "claim_shifton_notification", side_effect=queue), \
                patch.object(self.rasp, "complete_shifton_notification") as complete:
            self.rasp.send_pending_shifton_notifications(bot)

        bot.send_message.assert_called_once_with(12345, "Смена изменена")
        complete.assert_called_once_with(17, True)

    def test_telegram_error_is_reported_to_shifton(self):
        bot = Mock()
        bot.send_message.side_effect = RuntimeError("telegram unavailable")
        queue = [
            {"ok": True, "notification": {"id": 18, "chatId": 67890, "text": "Смена удалена"}},
            {"ok": True, "notification": None},
        ]

        with patch.object(self.rasp, "claim_shifton_notification", side_effect=queue), \
                patch.object(self.rasp, "complete_shifton_notification") as complete:
            self.rasp.send_pending_shifton_notifications(bot)

        complete.assert_any_call(18, False, "telegram unavailable")

    def test_parallel_workers_are_not_started(self):
        started = threading.Event()
        release = threading.Event()

        def blocking_check(_bot):
            started.set()
            release.wait(2)

        with patch.object(self.rasp, "send_pending_shifton_notifications", side_effect=blocking_check) as check:
            self.rasp.start_shifton_notifications_check(Mock())
            self.assertTrue(started.wait(1))
            self.rasp.start_shifton_notifications_check(Mock())
            time.sleep(0.05)
            self.assertEqual(check.call_count, 1)
            release.set()

            for _ in range(20):
                if not self.rasp.shifton_notifications_lock.locked():
                    break
                time.sleep(0.05)
            self.assertFalse(self.rasp.shifton_notifications_lock.locked())


if __name__ == "__main__":
    unittest.main()
