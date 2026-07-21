import importlib.util
import sys
import types
import unittest
from datetime import timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch


def load_kpi_module():
    telebot = types.ModuleType("telebot")
    telebot.__all__ = []
    constants = types.ModuleType("constants")
    constants.__all__ = ["SHIFTON_API_URL", "SHIFTON_API_TOKEN", "TEXTS"]
    constants.SHIFTON_API_URL = "http://shifton.test"
    constants.SHIFTON_API_TOKEN = "test-token"
    constants.TEXTS = {"aff": ["Готово"], "penalty_phrases": ["Штраф записан"]}
    sheets = types.ModuleType("sheets")
    sheets.__all__ = []
    pytz = types.ModuleType("pytz")
    pytz.timezone = lambda _name: timezone(timedelta(hours=3))

    modules = {
        "telebot": telebot,
        "constants": constants,
        "pygsheets": types.ModuleType("pygsheets"),
        "pandas": types.ModuleType("pandas"),
        "pytz": pytz,
        "sql_scripts": types.ModuleType("sql_scripts"),
        "sheets": sheets,
    }
    with patch.dict(sys.modules, modules):
        spec = importlib.util.spec_from_file_location("kpi_under_test", "kpi.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


class KpiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kpi = load_kpi_module()

    def message(self, text):
        return SimpleNamespace(text=text, from_user=SimpleNamespace(username="employee"))

    def test_supported_hashtags(self):
        self.assertEqual(set(self.kpi.kpi_dict), {
            "#серт", "#абик", "#штраф", "#двойная", "#продление",
            "#др", "#инициатива", "#отзывы", "#автосим", "#активация",
        })

    def test_router_is_case_insensitive_and_preserves_arguments(self):
        received = []

        def handler(message, args):
            received.append((message.text, args))
            return True, "ok", ""

        with patch.object(self.kpi, "kpi_dict", {"#продление": handler}):
            result = self.kpi.hash_handle(self.message("#ПРОДЛЕНИЕ Татьяна 15:00-16:00"))

        self.assertEqual(result, (True, "ok", ""))
        self.assertEqual(received, [("#ПРОДЛЕНИЕ Татьяна 15:00-16:00", "Татьяна 15:00-16:00")])

    def test_bonus_number_boundaries(self):
        invalid_cert = self.kpi.do_bonus("#серт", self.message(""), "2999 5000")
        invalid_subscription = self.kpi.do_bonus("#абик", self.message(""), "1000 5000")
        self.assertFalse(invalid_cert[0])
        self.assertFalse(invalid_subscription[0])

        self.kpi.Insert_bonus = lambda *args: None
        self.kpi.update_table = lambda *args: None
        valid_cert = self.kpi.do_bonus("#серт", self.message(""), "3000 5000")
        valid_subscription = self.kpi.do_bonus("#абик", self.message(""), "999 5000")
        self.assertTrue(valid_cert[0])
        self.assertTrue(valid_subscription[0])

    def test_double_accepts_decimal_comma(self):
        message = self.message("#двойная 1,5")
        self.kpi.requests.post = lambda *args, **kwargs: SimpleNamespace(json=lambda: {"ok": True})
        self.kpi.get_user_club_today = lambda username: "Марьино"

        connection = unittest.mock.Mock()
        connection.cursor.return_value = unittest.mock.Mock()
        with patch.object(self.kpi.sqlite3, "connect", return_value=connection):
            result = self.kpi.do_double(message, "1,5 описание")

        self.assertTrue(result[0])
        connection.cursor.return_value.execute.assert_called_once_with(
            "INSERT INTO double (who, d_rep, amount, desc) VALUES (?, ?, ?, ?)",
            ("@employee", unittest.mock.ANY, 1.5, "описание"),
        )


if __name__ == "__main__":
    unittest.main()
