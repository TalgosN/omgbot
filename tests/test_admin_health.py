import importlib.util
import sys
import types
import unittest
from datetime import timedelta, timezone
from unittest.mock import Mock, patch


def load_admin_module():
    telebot = types.ModuleType("telebot")
    telebot.types = types.SimpleNamespace()
    telebot.__all__ = ["types"]

    pygsheets = types.ModuleType("pygsheets")
    constants = types.ModuleType("constants")
    constants.CHATS = {}
    constants.clublist_task = ()
    constants.SHIFTON_API_URL = "http://shifton.test"
    constants.SHIFTON_API_TOKEN = "test-token"
    constants.validate_config = lambda: None
    sender = types.ModuleType("sender")
    sender.safe_send = Mock()
    pytz = types.ModuleType("pytz")
    pytz.timezone = lambda _name: timezone(timedelta(hours=3))

    modules = {
        "telebot": telebot,
        "pygsheets": pygsheets,
        "constants": constants,
        "sender": sender,
        "pytz": pytz,
    }
    with patch.dict(sys.modules, modules):
        spec = importlib.util.spec_from_file_location("admin_under_test", "admin_panel.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


class AdminHealthTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin = load_admin_module()

    def test_health_report_checks_all_core_systems(self):
        bot = Mock()
        bot.get_me.return_value = types.SimpleNamespace(username="omgbot")

        connection = Mock()
        connection.execute.return_value.fetchone.return_value = (7,)
        response = Mock()
        response.json.return_value = {"ok": True}
        google = Mock()
        self.admin.pygsheets.authorize = Mock(return_value=google)
        scheduler = Mock(name="omgbot-scheduler")
        scheduler.name = "omgbot-scheduler"
        scheduler.is_alive.return_value = True

        rasp = types.ModuleType("rasp")
        rasp.get_shifton_runtime_status = lambda: {
            "last_notification_check": "2026-07-21 12:00:00",
            "last_chat_sync": "2026-07-21 11:00:00",
            "last_notification_error": None,
        }

        with patch.object(self.admin.sqlite3, "connect", return_value=connection), \
                patch.object(self.admin.requests, "get", return_value=response) as request, \
                patch.object(self.admin.threading, "enumerate", return_value=[scheduler]), \
                patch.dict(sys.modules, {"rasp": rasp}):
            report = self.admin.collect_system_health(bot)

        self.assertIn("✅ Telegram", report)
        self.assertIn("✅ SQLite", report)
        self.assertIn("✅ Конфигурация", report)
        self.assertIn("✅ OMG Shift API", report)
        self.assertIn("✅ Google Sheets", report)
        self.assertIn("✅ Планировщик", report)
        self.assertIn("последняя проверка 2026-07-21 12:00:00", report)
        self.assertEqual(request.call_args.kwargs["timeout"], 5)
        google.open.assert_called_once_with("KPI OMG VR")


if __name__ == "__main__":
    unittest.main()
