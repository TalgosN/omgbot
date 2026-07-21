import importlib.util
import os
import sqlite3
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch


def load_taskboard_module():
    telebot = types.ModuleType('telebot')
    telebot.__all__ = []
    constants = types.ModuleType('constants')
    constants.__all__ = []
    permissions = types.ModuleType('permissions')
    permissions.ROLE_EMPLOYEE = 0
    permissions.ROLE_TECHNICIAN = 1
    permissions.require_role = lambda *_args: True
    permissions.role_of = lambda *_args: 0
    pytz = types.ModuleType('pytz')
    pytz.timezone = lambda _name: timezone(timedelta(hours=3))

    modules = {
        'telebot': telebot,
        'constants': constants,
        'permissions': permissions,
        'pytz': pytz,
    }
    with patch.dict(sys.modules, modules):
        spec = importlib.util.spec_from_file_location('taskboard_under_test', 'taskboard.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


class TaskboardLifecycleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.taskboard = load_taskboard_module()

    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix='.sql')
        os.close(handle)
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            '''CREATE TABLE tasks (
                   id INTEGER PRIMARY KEY, type TEXT, club TEXT, title TEXT,
                   status TEXT, dtfb TEXT, feedback TEXT
               )'''
        )
        conn.execute(
            '''CREATE TABLE users_new (
                   id INTEGER PRIMARY KEY, login TEXT, status INTEGER, chatid TEXT
               )'''
        )
        conn.commit()
        conn.close()
        self.db_patch = patch.object(self.taskboard, 'TASK_DB_PATH', self.db_path)
        self.db_patch.start()

    def tearDown(self):
        self.db_patch.stop()
        os.remove(self.db_path)

    def test_review_is_closed_on_fourteenth_day(self):
        conn = sqlite3.connect(self.db_path)
        conn.executemany(
            '''INSERT INTO tasks (id, type, club, title, status, dtfb, feedback)
               VALUES (?, 'Ремонт', 'Клуб', ?, 'На проверке', ?, ?)''',
            [
                (1, 'Старое решение', '2026-07-01', 'Ответ'),
                (2, 'Свежее решение', '2026-07-02', 'Ответ'),
                (3, 'Без старой даты', None, 'Ответ'),
            ],
        )
        conn.commit()
        conn.close()

        closed = self.taskboard.auto_close_review_tasks(datetime(2026, 7, 15, 9, 10))

        conn = sqlite3.connect(self.db_path)
        rows = conn.execute('SELECT id, status, dtfb, feedback FROM tasks ORDER BY id').fetchall()
        conn.close()
        self.assertEqual(closed, 1)
        self.assertEqual(rows[0][1:3], ('Выполнено', '2026-07-15'))
        self.assertIn('автоматически закрыта', rows[0][3])
        self.assertEqual(rows[1][1], 'На проверке')
        self.assertEqual(rows[2][1:3], ('На проверке', '2026-07-15'))

    def test_reminders_go_only_to_today_shift_employees_by_club(self):
        conn = sqlite3.connect(self.db_path)
        conn.executemany(
            '''INSERT INTO tasks (id, type, club, title, status)
               VALUES (?, ?, ?, ?, 'На проверке')''',
            [
                (1, 'Ремонт', 'Прокшино', 'Проверить кресло'),
                (2, 'Вопрос', 'Марьино', 'Проверить ответ'),
            ],
        )
        conn.executemany(
            'INSERT INTO users_new (login, status, chatid) VALUES (?, ?, ?)',
            [('@Alice', 0, '101'), ('@Charlie', 1, '103'), ('@Blocked', -1, '104')],
        )
        conn.commit()
        conn.close()

        rasp = types.ModuleType('rasp')
        rasp.fetch_schedule_from_api = lambda _date: {
            'ok': True,
            'locations': [
                {'title': 'Прокшино', 'shifts': [
                    {'telegram': '@alice'}, {'telegram': '@Alice'}, {'telegram': '@Blocked'}
                ]},
                {'title': 'Марьино', 'shifts': [{'telegram': 'charlie'}]},
            ],
        }
        bot = Mock()
        with patch.dict(sys.modules, {'rasp': rasp}):
            sent = self.taskboard.send_shift_review_reminders(
                bot, datetime(2026, 7, 21, 9, 10)
            )

        self.assertEqual(sent, 2)
        self.assertEqual(bot.send_message.call_count, 2)
        messages = {call.args[0]: call.args[1] for call in bot.send_message.call_args_list}
        self.assertIn('Проверить кресло', messages[101])
        self.assertNotIn('Проверить ответ', messages[101])
        self.assertIn('Проверить ответ', messages[103])
        self.assertNotIn(104, messages)


if __name__ == '__main__':
    unittest.main()
