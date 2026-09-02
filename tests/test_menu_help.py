import importlib.util
import os
import sys
import types
import unittest
from unittest.mock import Mock, patch


class Button:
    def __init__(self, text, **kwargs):
        self.text = text
        self.url = kwargs.get('url')
        self.web_app = kwargs.get('web_app')
        self.callback_data = kwargs.get('callback_data')


class WebAppInfo:
    def __init__(self, url):
        self.url = url


class Markup:
    def __init__(self, **kwargs):
        self.rows = []
        self.kwargs = kwargs

    def add(self, *buttons):
        self.rows.append(list(buttons))

    def row(self, *buttons):
        self.rows.append(list(buttons))


def load_menu_module():
    telebot = types.ModuleType('telebot')
    telebot.types = types.SimpleNamespace(
        ReplyKeyboardMarkup=Markup,
        InlineKeyboardMarkup=Markup,
        InlineKeyboardButton=Button,
        KeyboardButton=Button,
        WebAppInfo=WebAppInfo,
    )
    telebot.telebot = telebot

    constants = types.ModuleType('constants')
    constants.funclist = {}
    constants.admin_funclist = ()
    constants.owner_admin_funclist = ()
    constants.OWNER_EMPLOYEE_MODE_BUTTON = '🧑🏻 Режим сотрудника'
    constants.OWNER_MODE_BUTTON = '👑 Вернуться в режим владельца'
    constants.KPI_WEBAPP_URL = ''

    admin_panel = types.ModuleType('admin_panel')
    admin_panel.sync_config = Mock()

    permissions = types.ModuleType('permissions')
    permissions.ROLE_EMPLOYEE = 0
    permissions.ROLE_MANAGER = 2
    permissions.ROLE_OWNER = 3
    permissions.disable_owner_employee_mode = Mock(return_value=True)
    permissions.enable_owner_employee_mode = Mock(return_value=True)
    permissions.get_user = Mock()
    permissions.is_owner_employee_mode = Mock(return_value=False)
    permissions.require_role = Mock(return_value={'status': 0})

    with patch.dict(sys.modules, {
        'telebot': telebot,
        'constants': constants,
        'admin_panel': admin_panel,
        'permissions': permissions,
    }):
        spec = importlib.util.spec_from_file_location('menu_under_test', 'menu.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


class HelpMenuTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.menu = load_menu_module()

    def test_help_opens_section_menu(self):
        bot = Mock()
        bot.send_message.return_value = object()
        message = types.SimpleNamespace(chat=types.SimpleNamespace(id=123))

        self.menu.help(bot, message)

        markup = bot.send_message.call_args.kwargs['reply_markup']
        self.assertEqual(
            [button for row in markup.rows for button in row],
            list(self.menu.HELP_MENU_BUTTONS),
        )
        bot.register_next_step_handler.assert_called_once()

    def test_main_menu_does_not_install_persistent_next_step_handler(self):
        bot = Mock()
        self.menu.funclist = {0: ('Обычная кнопка',)}
        self.menu.get_user.return_value = {'status': 0, 'nick_name': 'Тест'}

        self.menu.hello(123, bot)

        bot.register_next_step_handler.assert_not_called()
        markup = bot.send_message.call_args.kwargs['reply_markup']
        self.assertTrue(markup.kwargs['is_persistent'])

    def test_private_text_restores_main_menu_for_authorized_employee(self):
        bot = Mock()
        message = types.SimpleNamespace(chat=types.SimpleNamespace(id=123))

        with patch.object(
            self.menu,
            'require_role',
            return_value={'status': 0},
        ) as require_role, patch.object(self.menu, 'hello') as hello:
            restored = self.menu.restore_main_menu_for_private_text(message, bot)

        self.assertTrue(restored)
        require_role.assert_called_once_with(
            message,
            bot,
            self.menu.ROLE_EMPLOYEE,
            notify=False,
        )
        hello.assert_called_once_with(123, bot)

    def test_group_text_never_restores_private_main_menu(self):
        bot = Mock()
        message = types.SimpleNamespace(chat=types.SimpleNamespace(id=-100123))

        with patch.object(self.menu, 'hello') as hello:
            restored = self.menu.restore_main_menu_for_private_text(message, bot)

        self.assertFalse(restored)
        hello.assert_not_called()

    def test_problem_reply_button_opens_authorized_inline_app(self):
        bot = Mock()
        self.menu.funclist = {0: ('🚩 Доска проблем', 'Обычная кнопка')}
        self.menu.get_user.return_value = {'status': 0, 'nick_name': 'Тест'}

        with patch.object(self.menu.app_constants, 'KPI_WEBAPP_URL', 'https://bot.omg-vr.ru/'):
            self.menu.hello(123, bot)

        markup = bot.send_message.call_args.kwargs['reply_markup']
        problem_button = markup.rows[0][0]
        self.assertEqual(problem_button, '🚩 Доска проблем')

        message = types.SimpleNamespace(chat=types.SimpleNamespace(id=123))
        with patch.object(self.menu.app_constants, 'KPI_WEBAPP_URL', 'https://bot.omg-vr.ru/'):
            self.menu.open_problems_app(message, bot)

        inline_markup = bot.send_message.call_args.kwargs['reply_markup']
        open_button = inline_markup.rows[0][0]
        self.assertEqual(open_button.text, '🚩 Открыть Доску проблем')
        self.assertEqual(
            open_button.web_app.url,
            'https://bot.omg-vr.ru/problems',
        )

    def test_problem_menu_offers_manager_readonly_view(self):
        bot = Mock()
        message = types.SimpleNamespace(chat=types.SimpleNamespace(id=123))
        self.menu.get_user.return_value = {'status': self.menu.ROLE_MANAGER}

        with patch.object(self.menu.app_constants, 'KPI_WEBAPP_URL', 'https://bot.omg-vr.ru/'):
            self.menu.open_problems_app(message, bot)

        markup = bot.send_message.call_args.kwargs['reply_markup']
        readonly_button = markup.rows[1][0]
        self.assertEqual(readonly_button.text, '📋 Быстрый просмотр в боте')
        self.assertEqual(readonly_button.callback_data, 'readonly_tasks:work')

    def test_resource_links_include_shift_and_all_sheets(self):
        with patch.dict(os.environ, {
            'STEAMTRACKER_SPREADSHEET_ID': 'current-steam-sheet',
        }):
            markup = self.menu.resource_links_markup()

        buttons = [button for row in markup.rows for button in row]
        self.assertEqual(len(buttons), 8)
        self.assertEqual(buttons[0].url, self.menu.OMG_SHIFT_URL)
        self.assertTrue(any(
            button.url.endswith('/current-steam-sheet/edit')
            for button in buttons
        ))
        self.assertFalse(any('Расписание' in button.text for button in buttons))

    def test_main_menu_opens_steamtracker_for_employee(self):
        tracker_admin = types.ModuleType('steamtracker.admin')
        tracker_admin.promotion_admin_menu = Mock()
        bot = Mock()
        message = types.SimpleNamespace(
            text='🎮 Steam Tracker',
            chat=types.SimpleNamespace(id=123),
        )

        with patch.dict(sys.modules, {
            'steamtracker.admin': tracker_admin,
        }):
            self.menu.func(message, bot)

        tracker_admin.promotion_admin_menu.assert_called_once_with(message, bot)

    def test_owner_main_menu_contains_employee_mode_button(self):
        bot = Mock()
        bot.send_message.return_value = object()
        self.menu.funclist = {3: ('Обычная кнопка',)}
        self.menu.get_user.return_value = {'status': 3, 'nick_name': 'Владелец'}
        self.menu.is_owner_employee_mode.return_value = False

        self.menu.hello(30, bot)

        markup = bot.send_message.call_args.kwargs['reply_markup']
        buttons = [button for row in markup.rows for button in row]
        self.assertEqual(
            buttons,
            ['Обычная кнопка', self.menu.OWNER_EMPLOYEE_MODE_BUTTON],
        )

    def test_employee_mode_main_menu_contains_owner_return_button(self):
        bot = Mock()
        bot.send_message.return_value = object()
        self.menu.funclist = {0: ('Кнопка сотрудника',)}
        self.menu.get_user.return_value = {'status': 0, 'nick_name': 'Владелец'}
        self.menu.is_owner_employee_mode.return_value = True

        self.menu.hello(30, bot)

        markup = bot.send_message.call_args.kwargs['reply_markup']
        buttons = [button for row in markup.rows for button in row]
        self.assertEqual(
            buttons,
            ['Кнопка сотрудника', self.menu.OWNER_MODE_BUTTON],
        )

    def test_steamtracker_follows_schedule_in_every_main_menu(self):
        import constants

        for buttons in constants.funclist.values():
            self.assertEqual(
                buttons.index('🎮 Steam Tracker'),
                buttons.index('🗓 Расписание') + 1,
            )


if __name__ == '__main__':
    unittest.main()
