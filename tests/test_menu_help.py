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


class Markup:
    def __init__(self, **kwargs):
        self.rows = []

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
    )
    telebot.telebot = telebot

    constants = types.ModuleType('constants')
    constants.funclist = {}
    constants.admin_funclist = ()
    constants.owner_admin_funclist = ()

    admin_panel = types.ModuleType('admin_panel')
    admin_panel.sync_config = Mock()

    permissions = types.ModuleType('permissions')
    permissions.ROLE_EMPLOYEE = 0
    permissions.ROLE_MANAGER = 2
    permissions.ROLE_OWNER = 3
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
        self.assertFalse(any(
            '1VYcdmS5B6-cGpawVZZpc8qpiwDnjlSNaNyLM43eBJKI' in button.url
            for button in buttons
        ))
        self.assertFalse(any('Расписание' in button.text for button in buttons))


if __name__ == '__main__':
    unittest.main()
