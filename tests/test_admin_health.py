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
                patch.object(
                    self.admin,
                    "collect_steamtracker_health",
                    return_value=["", "🎮 Steam Tracker", "✅ База: тест"],
                ), \
                patch.dict(sys.modules, {"rasp": rasp}):
            report = self.admin.collect_system_health(bot)

        self.assertIn("✅ Telegram", report)
        self.assertIn("✅ SQLite", report)
        self.assertIn("✅ Конфигурация", report)
        self.assertIn("✅ OMG Shift API", report)
        self.assertIn("✅ Google Sheets", report)
        self.assertIn("✅ Планировщик", report)
        self.assertIn("🎮 Steam Tracker", report)
        self.assertIn("последняя проверка 2026-07-21 12:00:00", report)
        self.assertEqual(request.call_args.kwargs["timeout"], 5)
        google.open.assert_not_called()
        google.open_by_key.assert_called_once_with(self.admin.CONFIG_SPREADSHEET_ID)

    def test_monthly_kpi_report_filters_zero_shifts_and_marks_weakest_three(self):
        def employee(name, shifts, weighted_shifts, total, weighted):
            return {
                'nickname': name,
                'shifts': shifts,
                'weighted_shifts': weighted_shifts,
                'total_pct': total,
                'weighted_pct': weighted,
            }

        rows = [
            employee('Без смен', 0, 5, 0.01, 0.01),
            employee('Без взвешенных смен', 10, 0, 0.01, 0.01),
            employee('Нулевой KPI', 6, 7, 0, 0),
            employee('Четвёртый', 10, 12, 0.50, 0.40),
            employee('Первый', 8, 9, 0.10, 0.08),
            employee('Третий', 9, 11, 0.30, 0.25),
            employee('Второй', 7, 8, 0.20, 0.15),
        ]

        reports = self.admin.build_monthly_kpi_report(rows, '21.07.2026')
        report = '\n'.join(reports)

        self.assertNotIn('Без смен', report)
        self.assertNotIn('Без взвешенных смен', report)
        self.assertEqual(report.count('🔴'), 3)
        self.assertIn('Четвёртый', report)
        self.assertIn('Третий', report)
        self.assertIn('📈 Средний KPI: <b>27.5%</b>', report)
        self.assertIn('📐 Медианный KPI: <b>25%</b>', report)
        self.assertIn('📆 Смен: <b>6</b>', report)
        self.assertNotIn('🥇', report)
        self.assertNotIn('🕒', report)
        self.assertLess(report.index('Нулевой KPI'), report.index('Первый'))
        self.assertLess(report.index('Первый'), report.index('Второй'))
        self.assertNotIn('—', report)

    def test_monthly_kpi_report_handler_does_not_read_google_sheet(self):
        bot = Mock()
        bot.send_message.return_value = types.SimpleNamespace(message_id=77)
        message = types.SimpleNamespace(chat=types.SimpleNamespace(id=123))
        menu = types.ModuleType('menu')
        menu.admin_menu = Mock()
        rows = [{
            'login': '@employee',
            'nickname': 'Сотрудник',
            'shifts': 5.0,
            'weighted_shifts': 6.0,
            'total_pct': 0.42,
            'weighted_pct': 0.35,
        }]
        self.admin.pygsheets.authorize = Mock(
            side_effect=AssertionError('Google Sheets must not be used'),
        )

        with patch.object(
            self.admin, 'require_role', return_value={'status': 2},
        ), patch.object(
            self.admin,
            'active_kpi_employee_logins',
            return_value=['@employee'],
        ), patch.object(
            self.admin,
            'calculate_monthly_kpi',
            return_value=rows,
        ) as calculate, patch.dict(sys.modules, {'menu': menu}):
            self.admin.handle_monthly_kpi_report(message, bot)

        calculate.assert_called_once()
        self.admin.pygsheets.authorize.assert_not_called()
        sent_texts = [call.args[1] for call in bot.send_message.call_args_list]
        self.assertTrue(any('KPI сотрудников за месяц' in text for text in sent_texts))

    def test_extra_menu_keeps_owner_only_report_out_of_manager_menu(self):
        manager_buttons = [
            '⚙️ Обновить настройки',
            '🩺 Статус систем',
            '📦 Тест отчета по расходникам',
            '⬅️ Назад в админку',
        ]
        owner_buttons = [
            '⚙️ Обновить настройки',
            '🩺 Статус систем',
            '📊 Тест недельного отчета',
            '📦 Тест отчета по расходникам',
            '⬅️ Назад в админку',
        ]
        constants = types.ModuleType("constants")
        constants.admin_extra_funclist = manager_buttons
        constants.owner_admin_extra_funclist = owner_buttons
        markup = Mock()
        self.admin.types.ReplyKeyboardMarkup = Mock(return_value=markup)
        bot = Mock()
        bot.send_message.return_value = Mock()
        message = types.SimpleNamespace(
            chat=types.SimpleNamespace(id=10),
        )

        with patch.object(
            self.admin,
            "require_role",
            return_value={"status": self.admin.ROLE_MANAGER},
        ), patch.dict(sys.modules, {"constants": constants}):
            self.admin.admin_extra_menu(message, bot)

        markup.add.assert_called_once_with(*manager_buttons)
        bot.register_next_step_handler.assert_called_once_with(
            bot.send_message.return_value,
            self.admin.admin_extra_menu_handler,
            bot,
        )

    def test_extra_menu_routes_service_action_to_existing_handler(self):
        message = types.SimpleNamespace(text='🩺 Статус систем')
        bot = Mock()

        with patch.object(
            self.admin,
            "require_role",
            return_value={"status": self.admin.ROLE_MANAGER},
        ), patch.object(self.admin, "admin_func_handler") as handler:
            self.admin.admin_extra_menu_handler(message, bot)

        handler.assert_called_once_with(message, bot)

    def test_kpi_shadow_report_groups_differences_and_import_status(self):
        report = self.admin.build_kpi_shadow_report(
            {
                'period_month': '2026-07-01',
                'employees': 20,
                'differences': [
                    {'login': '@first', 'field': 'forms'},
                    {'login': '@first', 'field': 'forms'},
                    {'login': '@second', 'field': 'rank'},
                ],
                'sheet_health': {
                    'data_rows': 548,
                    'data_unique_rows': 300,
                    'data_duplicate_rows': 248,
                },
            },
            {
                'penalty_sources': {
                    'legacy_google_sheet': 14,
                    'legacy_db': 10,
                },
                'current_penalties': 2,
                'current_streams': 7,
            },
        )

        self.assertIn('Расхождений: <b>3</b>', report)
        self.assertIn('Проверяется только месяц: <b>2026-07-01</b>', report)
        self.assertIn('Затронуто сотрудников: <b>2</b>', report)
        self.assertIn('Анкеты: <b>2</b>', report)
        self.assertIn('Рейтинг: <b>1</b>', report)
        self.assertIn('лишних дублей: <b>248</b>', report)
        self.assertIn('548', report)
        self.assertIn('300', report)
        self.assertIn('@first', report)
        self.assertIn('сервер → Google', report)
        self.assertIn('Google Sheets: <b>14</b>', report)
        self.assertIn('старой БД: <b>10</b>', report)
        self.assertIn('Трансляции месяца: <b>7</b>', report)


if __name__ == "__main__":
    unittest.main()
