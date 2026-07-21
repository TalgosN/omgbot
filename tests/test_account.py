import importlib.util
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


def load_account_module():
    telebot = types.ModuleType('telebot')
    telebot.types = types.SimpleNamespace()
    constants = types.ModuleType('constants')
    constants.TEXTS = {'hey': ['Привет']}
    constants.funclist_acc = ()
    sheets = types.ModuleType('sheets')
    sheets.tables = ['afterparty', 'birthday', 'initiative', 'abik', 'sert']
    sheets.update_status = lambda: None
    sheets.update_table = lambda _table: None
    sheets.update_table_open = lambda: None
    sheets.update_users = lambda: None
    pygsheets = types.ModuleType('pygsheets')
    pytz = types.ModuleType('pytz')
    pytz.timezone = lambda _name: None
    modules = {
        'telebot': telebot,
        'constants': constants,
        'sheets': sheets,
        'pygsheets': pygsheets,
        'pytz': pytz,
    }
    with patch.dict(sys.modules, modules):
        spec = importlib.util.spec_from_file_location('account_under_test', 'account.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


class AccountTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.account = load_account_module()

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / 'test.sql'
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            'CREATE TABLE users_new (ID INTEGER PRIMARY KEY, login TEXT, first_name TEXT, '
            'second_name TEXT, nick_name TEXT, bday TEXT, phone TEXT, email TEXT, status INTEGER, chatid TEXT)'
        )
        conn.execute(
            'INSERT INTO users_new VALUES (1, ?, ?, ?, ?, NULL, NULL, NULL, 0, ?)',
            ('@old_login', 'СтароеИмя', 'СтараяФамилия', 'Ник', '12345'),
        )
        for table, column in self.account.LOGIN_REFERENCES.items():
            if table == 'shifts':
                conn.execute(
                    'CREATE TABLE shifts (shift_second_name TEXT, shift_first_name TEXT, '
                    'dt_shift TEXT, club TEXT, dur REAL)'
                )
                conn.execute(
                    'INSERT INTO shifts VALUES (?, ?, ?, ?, ?)',
                    ('Бондаренко', 'Саша', '2026-07-20', 'Марьино', 6.0),
                )
            else:
                conn.execute(f'CREATE TABLE "{table}" ("{column}" TEXT)')
                conn.execute(f'INSERT INTO "{table}" ("{column}") VALUES (?)', ('@old_login',))
        conn.commit()
        conn.close()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_omg_identity_updates_names_and_every_login_reference(self):
        with patch.object(self.account, 'DB_PATH', str(self.db_path)):
            result = self.account.apply_omg_identity(
                '12345', '@new_login', 'Бондаренко Саша'
            )

        self.assertTrue(result['changed'])
        conn = sqlite3.connect(self.db_path)
        user = conn.execute(
            'SELECT login, first_name, second_name FROM users_new WHERE ID=1'
        ).fetchone()
        self.assertEqual(user, ('@new_login', 'Саша', 'Бондаренко'))
        for table, column in self.account.LOGIN_REFERENCES.items():
            value = conn.execute(f'SELECT "{column}" FROM "{table}"').fetchone()[0]
            if table == 'shifts':
                self.assertEqual(value, '@new_login')
            else:
                self.assertEqual(value, '@new_login', table)
        conn.close()

    def test_duplicate_login_rolls_back_everything(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            'INSERT INTO users_new VALUES (2, ?, ?, ?, ?, NULL, NULL, NULL, 0, ?)',
            ('@occupied', 'Другой', 'Сотрудник', 'Другой', '99999'),
        )
        conn.commit()
        conn.close()

        with patch.object(self.account, 'DB_PATH', str(self.db_path)):
            with self.assertRaisesRegex(ValueError, 'другому пользователю'):
                self.account.apply_omg_identity('12345', '@occupied', 'Бондаренко Саша')

        conn = sqlite3.connect(self.db_path)
        login = conn.execute('SELECT login FROM users_new WHERE ID=1').fetchone()[0]
        reference = conn.execute('SELECT who FROM afterparty').fetchone()[0]
        conn.close()
        self.assertEqual(login, '@old_login')
        self.assertEqual(reference, '@old_login')

    def test_legacy_shift_uses_omg_name_after_identity_sync(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "UPDATE users_new SET login=?, first_name=?, second_name=? WHERE ID=1",
            ('@maxim', 'Максим', 'Песков'),
        )
        conn.execute(
            "UPDATE shifts SET shift_second_name=?, shift_first_name=?",
            ('Песков', 'Максим'),
        )
        conn.commit()
        conn.close()

        with patch.object(self.account, 'DB_PATH', str(self.db_path)):
            self.account.apply_omg_identity(
                '12345', '@maxon', 'Песков Максон'
            )

        conn = sqlite3.connect(self.db_path)
        raw_shift = conn.execute(
            'SELECT shift_second_name, shift_first_name, shift_login FROM shifts'
        ).fetchone()
        displayed_shift = conn.execute(self.account.sql_scripts.shifts_ext).fetchone()
        conn.close()

        self.assertEqual(raw_shift, ('Песков', 'Максим', '@maxon'))
        self.assertEqual(displayed_shift[:2], ('Песков', 'Максон'))

    def test_profile_validation(self):
        self.assertEqual(
            self.account.validate_profile_value('bday', '21.07.2000'),
            '2000-07-21',
        )
        self.assertEqual(
            self.account.validate_profile_value('email', 'user@example.com'),
            'user@example.com',
        )
        with self.assertRaises(ValueError):
            self.account.validate_profile_value('phone', '89990000000')

    def test_other_employee_statistics_button_is_visible_to_managers(self):
        message = Mock(chat=Mock(id=123), from_user=Mock(id=123))
        bot = Mock()
        keyboard = Mock()
        bot.send_message.return_value = Mock()

        with patch.object(self.account, 'require_role', return_value={'status': 2}), \
                patch.object(
                    self.account.telebot.types,
                    'ReplyKeyboardMarkup',
                    return_value=keyboard,
                    create=True,
                ):
            self.account.account_settings(message, bot)

        self.assertIn(self.account.OTHER_STATS_BUTTON, keyboard.add.call_args.args)

    def test_other_employee_statistics_button_is_hidden_from_employees(self):
        message = Mock(chat=Mock(id=123), from_user=Mock(id=123))
        bot = Mock()
        keyboard = Mock()
        bot.send_message.return_value = Mock()

        with patch.object(self.account, 'require_role', return_value={'status': 0}), \
                patch.object(
                    self.account.telebot.types,
                    'ReplyKeyboardMarkup',
                    return_value=keyboard,
                    create=True,
                ):
            self.account.account_settings(message, bot)

        self.assertNotIn(self.account.OTHER_STATS_BUTTON, keyboard.add.call_args.args)

    def test_other_employee_monthly_statistics_uses_selected_login(self):
        message = Mock(text='📊 За месяц')
        bot = Mock()

        with patch.object(self.account, 'require_role', return_value={'status': 2}), \
                patch.object(self.account, 'send_monthly_stats') as send_stats, \
                patch.object(self.account, 'account_settings') as account_settings:
            self.account.other_stats_show(message, bot, '@employee', 'Сотрудник')

        send_stats.assert_called_once_with(
            message, bot, '@employee', 'Сотрудник'
        )
        account_settings.assert_called_once_with(message, bot)

    def test_main_sheet_kpi_is_mapped_by_telegram_login(self):
        employees_sheet = Mock()
        employees_sheet.get_values.return_value = [['Ник', '@employee']]
        main_sheet = Mock()
        main_row = [
            '1', 'Ник', '10', '12', '3', '30%', '8', '80%', '2', '20%',
            '1000', '10%', '2000', '20%', '1', '15%', '4', '40%', 'TRUE',
            '1', '55%', '46%', '1200 ₽', '●', '2', '5', '2',
        ]
        main_sheet.get_values.return_value = [
            ['', '', '21.07.2026'], [], [], [], [], [], [], main_row,
        ]
        spreadsheet = Mock()
        spreadsheet.worksheet_by_title.side_effect = lambda title: (
            employees_sheet if title == 'Сотрудники' else main_sheet
        )
        client = Mock()
        client.open.return_value = spreadsheet

        with patch.object(self.account.pygsheets, 'authorize', return_value=client, create=True):
            result = self.account.get_main_kpi('@employee')

        self.assertEqual(result['nickname'], 'Ник')
        self.assertEqual(result['total_pct'], '55%')
        self.assertEqual(result['weighted_pct'], '46%')
        self.assertEqual(result['rank'], '2')
        self.assertEqual(result['birthdays_month'], '5')
        self.assertEqual(result['birthdays_week'], '2')

    def test_monthly_kpi_uses_approved_telegram_format(self):
        data = {
            'nickname': 'Никита <admin>',
            'date': '21.7.2026',
            'shifts': '38',
            'weighted_shifts': '57',
            'reviews': '1',
            'reviews_pct': '11%',
            'forms': '32',
            'forms_pct': '85%',
            'extensions': '1',
            'extensions_pct': '11%',
            'certificates': '0',
            'certificates_pct': '0%',
            'subscriptions': '0',
            'subscriptions_pct': '0%',
            'bs': '5',
            'bs_pct': '50%',
            'initiatives': '3',
            'initiatives_pct': '30%',
            'stream': 'FALSE',
            'penalties': '0',
            'total_pct': '38%',
            'weighted_pct': '26%',
            'amount': '2 020 ₽',
            'zone': '◉',
            'rank': '8',
            'birthdays_month': '0',
            'birthdays_week': '0',
        }

        text = self.account.format_main_kpi(
            data,
            {'Автосимы': 1.5, 'Активации': 2, 'Двойные часы': 3},
        )

        self.assertIn('📊 <b>KPI за июль 2026</b>', text)
        self.assertIn('👤 <b>Никита &lt;admin&gt;</b>', text)
        self.assertIn('🕒 Смены: <b>38</b> <i>(57 взв.)</i>', text)
        self.assertIn('🎯 Итого KPI: <b>38%</b> <i>(26% взв.)</i>', text)
        self.assertIn('🚘 Автосимы: <b>1.5</b>', text)
        for removed_label in ('БС:', 'Сумма:', 'Трансляция:', 'Зона:', '—'):
            self.assertNotIn(removed_label, text)

    def test_all_time_format_has_sections_and_omits_bs(self):
        stats = {
            'Часы': 228,
            'Смены': 38,
            'Отзывы': 1,
            'Анкеты': 32,
            'БС': 7,
            'Автосимы': 2,
        }

        text = self.account.format_database_stats(
            stats, '⭐ Статистика за всё время', 'Никита'
        )

        self.assertIn('<b>⭐ Статистика за всё время</b>', text)
        self.assertIn('⏱️ <b>Рабочее время</b>', text)
        self.assertIn('📈 <b>Показатели KPI</b>', text)
        self.assertIn('🚀 <b>Дополнительные показатели</b>', text)
        self.assertNotIn('БС:', text)
        self.assertNotIn('—', text)

    def test_database_statistics_include_history_and_new_kpi_tables(self):
        conn = sqlite3.connect(self.db_path)
        additions = {
            'afterparty': ['ID INTEGER', 'dt_rep TEXT', 'club TEXT', 'desc TEXT', 'status TEXT'],
            'birthday': ['ID INTEGER', 'dt_rep TEXT', 'club TEXT', 'desc TEXT', 'status TEXT'],
            'initiative': ['ID INTEGER', 'dt_rep TEXT', 'club TEXT', 'desc TEXT', 'status TEXT'],
            'sert': ['ID INTEGER', 'num TEXT', 'd_rep TEXT', 'bonus REAL'],
            'abik': ['ID INTEGER', 'num TEXT', 'd_rep TEXT', 'bonus REAL'],
            'reviews': ['ID INTEGER', 'd_rep TEXT', 'amount REAL', 'desc TEXT'],
            'bs': ['id_bs INTEGER', 'dt_bs TEXT'],
            'penalty': ['ID INTEGER', 'dt TEXT', 'desc TEXT'],
            'autosim': ['d_rep TEXT', 'amount REAL'],
            'activation': ['d_rep TEXT', 'amount REAL'],
            'double': ['d_rep TEXT', 'amount REAL', 'desc TEXT'],
        }
        for table, columns in additions.items():
            for column in columns:
                conn.execute(f'ALTER TABLE "{table}" ADD COLUMN {column}')
        conn.execute(
            'CREATE TABLE anketi (ID INTEGER, id_ank INTEGER, dt_ank TEXT, club_ank TEXT)'
        )
        conn.execute(
            'INSERT INTO afterparty (who, ID, dt_rep) VALUES (?, ?, ?)',
            ('@old_login', 1, '2026-07-10'),
        )
        conn.execute(
            'INSERT INTO reviews (who, ID, d_rep, amount) VALUES (?, ?, ?, ?)',
            ('@old_login', 1, '2026-07-11', 3),
        )
        conn.execute(
            'INSERT INTO double (who, d_rep, amount) VALUES (?, ?, ?)',
            ('@old_login', '2026-07-12', 2.5),
        )
        conn.execute(
            'INSERT INTO shifts VALUES (?, ?, ?, ?, ?)',
            ('СтараяФамилия', 'СтароеИмя', '2026-07-13', 'Марьино', 6.0),
        )
        conn.commit()
        conn.close()

        with patch.object(self.account, 'DB_PATH', str(self.db_path)):
            result = self.account.get_database_stats(
                '@old_login', '2026-07-01', '2026-07-31'
            )

        self.assertEqual(result['Продления'], 1)
        self.assertEqual(result['Отзывы'], 3)
        self.assertEqual(result['Двойные часы'], 2.5)
        self.assertEqual(result['Часы'], 6)


if __name__ == '__main__':
    unittest.main()
