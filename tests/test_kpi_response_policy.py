import ast
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


def load_hashtag_handler(namespace):
    source = Path("main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    handler = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "HashTags")
    module = ast.Module(body=[handler], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, "main.py", "exec"), namespace)
    return namespace["HashTags"]


class KpiResponsePolicyTest(unittest.TestCase):
    def run_status(self, status):
        bot = Mock()
        bot.message_handler.side_effect = lambda **_kwargs: (lambda function: function)
        send_react = Mock()
        send_react.return_value = True
        update_kpi = Mock()

        kpi = types.ModuleType("kpi")
        kpi.KPI_SUCCESS = "success"
        kpi.KPI_INVALID = "invalid"
        kpi.KPI_ERROR = "error"
        kpi.KPI_SAVED_ERROR = "saved_error"
        kpi.hash_handle = lambda _message: (status, "Ошибка обработки", "")
        kpi.update_kpi = update_kpi

        namespace = {
            "bot": bot,
            "is_spam": lambda _message: True,
            "require_role": lambda _message, _bot, _role: True,
            "ROLE_EMPLOYEE": 0,
            "kpi": kpi,
            "send_react": send_react,
            "random": SimpleNamespace(choice=lambda _items: "✅"),
            "emojis": {"confirm": ("✅",)},
            "CHATS": {"me": 1},
        }
        with patch.dict(sys.modules, {"kpi": kpi}):
            handler = load_hashtag_handler(namespace)
            handler(SimpleNamespace(text="#тест"))
        return bot, send_react, update_kpi

    def test_success_uses_only_confirm_reaction(self):
        bot, send_react, update_kpi = self.run_status("success")
        update_kpi.assert_called_once_with()
        send_react.assert_called_once()
        bot.reply_to.assert_not_called()

    def test_invalid_input_uses_only_dislike(self):
        bot, send_react, update_kpi = self.run_status("invalid")
        update_kpi.assert_not_called()
        self.assertEqual(send_react.call_args.args[1], "👎")
        bot.reply_to.assert_not_called()

    def test_error_sends_message_without_reaction(self):
        bot, send_react, update_kpi = self.run_status("error")
        update_kpi.assert_not_called()
        send_react.assert_not_called()
        bot.reply_to.assert_called_once()

    def test_saved_error_is_synchronized_and_reported(self):
        bot, send_react, update_kpi = self.run_status("saved_error")
        update_kpi.assert_called_once_with()
        send_react.assert_not_called()
        bot.reply_to.assert_called_once()


if __name__ == "__main__":
    unittest.main()
