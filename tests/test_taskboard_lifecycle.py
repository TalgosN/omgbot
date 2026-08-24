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
    constants.__all__ = ['CHATS', 'extra_tags', 'get_clubs']
    constants.CHATS = {'reports': -1, 'main_group': -2, 'repair_extra': -3}
    constants.extra_tags = {'Ремонт': '@repair', 'Улучшение бота': '@bot'}
    constants.get_clubs = lambda: {
        'Клуб': {'tag': '@club'},
        'Прокшино': {'tag': '@prokshino'},
    }
    permissions = types.ModuleType('permissions')
    permissions.ROLE_EMPLOYEE = 0
    permissions.ROLE_MANAGER = 2
    permissions.ROLE_TECHNICIAN = 1
    permissions.require_role = lambda *_args: True
    permissions.role_of = lambda *_args: 0
    permissions.get_user = lambda *_args: {
        'chatid': '123', 'login': '@tester', 'nick_name': 'Тестер',
    }
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
                   status TEXT, dtfb TEXT, feedback TEXT, dtrep TEXT,
                   photo BLOB, desc TEXT
               )'''
        )
        conn.execute(
            '''CREATE TABLE users (
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

    def test_readonly_board_pages_all_statuses_without_mutating_tasks(self):
        conn = sqlite3.connect(self.db_path)
        conn.executemany(
            '''INSERT INTO tasks(id, type, club, title, status, dtrep)
               VALUES (?, 'Ремонт', 'Клуб', ?, 'В работе', '2026-08-01')''',
            [(task_id, f'Заявка {task_id}') for task_id in range(1, 32)],
        )
        conn.execute(
            '''INSERT INTO tasks(id, type, club, title, status, dtrep)
               VALUES (40, 'Ремонт', 'Клуб', 'Проверка', 'На проверке', '2026-08-02')'''
        )
        conn.execute(
            '''INSERT INTO tasks(id, type, club, title, status, dtrep)
               VALUES (41, 'Ремонт', 'Клуб', 'Готово', 'Выполнено', '2026-08-03')'''
        )
        conn.commit()
        before = conn.execute('SELECT COUNT(*) FROM tasks').fetchone()[0]
        conn.close()

        rows, counts, page, max_page = self.taskboard._readonly_task_rows(
            'work', 1,
        )

        conn = sqlite3.connect(self.db_path)
        after = conn.execute('SELECT COUNT(*) FROM tasks').fetchone()[0]
        conn.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(counts, {'work': 31, 'review': 1, 'done': 1})
        self.assertEqual((page, max_page), (1, 1))
        self.assertEqual(before, after)

    def test_readonly_board_uses_legacy_preview_without_management_actions(self):
        class Markup:
            def __init__(self, **_kwargs):
                self.rows = []

            def row(self, *buttons):
                self.rows.append(buttons)

        class Button:
            def __init__(self, text, **kwargs):
                self.text = text
                self.callback_data = kwargs.get('callback_data')

        self.taskboard.types = types.SimpleNamespace(
            InlineKeyboardMarkup=Markup,
            InlineKeyboardButton=Button,
            ReplyKeyboardRemove=lambda: 'remove-keyboard',
        )
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            '''INSERT INTO tasks(
                   id, type, club, title, status, dtrep, desc, feedback
               ) VALUES (
                   7, 'Ремонт', 'Клуб', 'Шлем & кабель', 'В работе',
                   '2026-08-24', 'Описание', '<b>[24.08] Админ:</b> Проверяем'
               )'''
        )
        conn.commit()
        conn.close()
        bot = Mock()
        message = types.SimpleNamespace(
            id=10, chat=types.SimpleNamespace(id=123),
        )

        self.taskboard.show_readonly_tasks(message, bot)

        list_call, instruction_call = bot.send_message.call_args_list
        text = list_call.args[1]
        markup = list_call.kwargs['reply_markup']
        self.assertIn('Вот список текущих проблем:', text)
        self.assertIn('<b>Клуб:</b>', text)
        self.assertIn('1) Шлем &amp; кабель', text)
        self.assertNotIn('OMG TASKBOARD', text)
        self.assertEqual(list_call.kwargs['parse_mode'], 'HTML')
        self.assertIn('Выбери одну', instruction_call.args[1])
        self.assertEqual(
            instruction_call.kwargs['reply_markup'], 'remove-keyboard',
        )
        callbacks = [
            button.callback_data for row in markup.rows for button in row
        ]
        self.assertNotIn('Обработать', [
            button.text for row in markup.rows for button in row
        ])
        self.assertTrue(all(value.startswith('readonly_') for value in callbacks))

    def test_readonly_detail_matches_legacy_card_and_keeps_readonly_back_button(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            '''INSERT INTO tasks(
                   id, type, club, title, status, dtrep, photo, desc, feedback
               ) VALUES (
                   9, 'Общее обращение', 'Дмитровка', 'Предложение',
                   'В работе', '2026-07-03', ?, 'Добавить вешалку', NULL
               )''',
            (b'photo-bytes',),
        )
        conn.commit()
        conn.close()
        bot = Mock()
        message = types.SimpleNamespace(chat=types.SimpleNamespace(id=123))
        detail_markup = object()

        with patch.object(
            self.taskboard,
            '_readonly_webapp_markup',
            return_value=detail_markup,
        ) as markup:
            self.taskboard.show_readonly_task_detail(
                message, bot, 9, source_status='work', page=1,
            )

        caption = bot.send_photo.call_args.kwargs['caption']
        self.assertIn('<b>Предложение</b>', caption)
        self.assertIn('<b>Тип:</b> Общее обращение', caption)
        self.assertIn('<b>Клуб:</b> Дмитровка', caption)
        self.assertIn('<b>Описание:</b> Добавить вешалку', caption)
        self.assertIn('<b>Статус:</b> В работе', caption)
        self.assertIn('<b>Дата:</b> 2026-07-03', caption)
        self.assertIn('Ожидает решения...', caption)
        self.assertEqual(bot.send_photo.call_args.kwargs['parse_mode'], 'HTML')
        self.assertIs(bot.send_photo.call_args.kwargs['reply_markup'], detail_markup)
        markup.assert_called_once_with('work', 1)

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
        events = conn.execute(
            "SELECT task_id, event_type, actor_name FROM task_events ORDER BY task_id"
        ).fetchall()
        conn.close()
        self.assertEqual(closed, 1)
        self.assertEqual(rows[0][1:3], ('Выполнено', '2026-07-15'))
        self.assertIn('автоматически закрыта', rows[0][3])
        self.assertEqual(rows[1][1], 'На проверке')
        self.assertEqual(rows[2][1:3], ('На проверке', '2026-07-15'))
        self.assertEqual(events, [(1, 'confirmed', 'Система')])

    def test_repair_photo_uses_full_copy_outside_main_chat(self):
        bot = Mock()
        self.taskboard._send_task_notification(
            bot,
            'created',
            'Ремонт',
            'Прокшино',
            'Шлем — 1 зона',
            description='Не включается',
            actor={'name': 'Иван', 'login': '@ivan'},
            photo_id='telegram-photo',
        )

        self.assertEqual(bot.send_photo.call_count, 2)
        report = bot.send_photo.call_args_list[0].kwargs['caption']
        repair = bot.send_photo.call_args_list[1].kwargs['caption']
        main = bot.send_message.call_args.args[1]
        self.assertIn('#задачи', report)
        self.assertIn('@OMGVR_Admin_Bot', report)
        self.assertIn('Не включается', report)
        self.assertNotIn('#задачи', repair)
        self.assertNotIn('@OMGVR_Admin_Bot', repair)
        self.assertIn('Не включается', repair)
        self.assertIn('Создал:</b> Иван (@ivan)', repair)
        self.assertNotIn('Не включается', main)

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
            'INSERT INTO users (login, status, chatid) VALUES (?, ?, ?)',
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

    def test_first_solution_has_actor_but_no_club_mentions_in_main_chat(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.fetchone.return_value = {
            'feedback': '',
            'title': 'Проверить кресло',
            'type': 'Вопрос',
            'club': 'Прокшино',
        }
        bot = Mock()
        message = Mock(text='Да', chat=Mock(id=123), from_user=Mock(id=123))
        self.taskboard.CHATS = {
            'reports': -1,
            'main_group': -2,
            'repair_extra': -3,
        }
        self.taskboard.types = types.SimpleNamespace(ReplyKeyboardRemove=lambda: None)

        with patch.object(self.taskboard.sqlite3, 'connect', return_value=connection), \
                patch.object(self.taskboard, 'record_task_event'), \
                patch.object(self.taskboard, 'show_active_tasks'):
            self.taskboard.change_task(message, 1, 'Всё исправлено', bot)

        main_message = next(
            call.args[1]
            for call in bot.send_message.call_args_list
            if call.args[0] == self.taskboard.CHATS['main_group']
        )
        self.assertNotIn('@prokshino', main_message)
        self.assertIn('@tester', main_message)
        self.assertTrue(main_message.startswith('👀 <b>Ответ'))


if __name__ == '__main__':
    unittest.main()
