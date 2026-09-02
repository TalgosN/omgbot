import json
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

import shift_tasks


class FakeBot:
    def __init__(self):
        self.messages = []

    def send_message(self, chat_id, text, **kwargs):
        message = SimpleNamespace(message_id=len(self.messages) + 1)
        self.messages.append(('message', str(chat_id), text, kwargs))
        return message

    def send_photo(self, chat_id, photo, **kwargs):
        message = SimpleNamespace(message_id=len(self.messages) + 1)
        self.messages.append(('photo', str(chat_id), photo, kwargs))
        return message

    def send_video(self, chat_id, video, **kwargs):
        message = SimpleNamespace(message_id=len(self.messages) + 1)
        self.messages.append(('video', str(chat_id), video, kwargs))
        return message

    def send_media_group(self, chat_id, media):
        self.messages.append(('album', str(chat_id), media, {}))
        return [SimpleNamespace(message_id=100 + index) for index in range(len(media))]

    def edit_message_reply_markup(self, **_kwargs):
        return None

    def edit_message_text(self, *_args, **_kwargs):
        return None

    def answer_callback_query(self, *_args, **_kwargs):
        return None


class ShiftTasksTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / 'tasks.sqlite3')
        conn = sqlite3.connect(self.db_path)
        with conn:
            conn.execute(
                '''CREATE TABLE broadcasts (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       text TEXT, photo TEXT, time TEXT, freq_type TEXT,
                       freq_days TEXT, status INTEGER DEFAULT 1
                   )'''
            )
            conn.execute(
                '''CREATE TABLE users (
                       ID INTEGER PRIMARY KEY, login TEXT, first_name TEXT,
                       second_name TEXT, nick_name TEXT, status INTEGER,
                       chatid TEXT
                   )'''
            )
            conn.execute(
                '''CREATE TABLE shifts (
                       shift_second_name TEXT, shift_first_name TEXT,
                       dt_shift TEXT, club TEXT, dur REAL, source TEXT,
                       shift_login TEXT, shift_start TEXT, shift_end TEXT
                   )'''
            )
            conn.execute(
                '''CREATE TABLE activity (
                       ID INTEGER PRIMARY KEY AUTOINCREMENT, dtrep TEXT,
                       login TEXT, club TEXT, action TEXT
                   )'''
            )
        conn.close()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_schema_seeds_editable_cleanliness_and_migrates_deep_cleaning(self):
        conn = sqlite3.connect(self.db_path)
        with conn:
            for broadcast_id in range(1, 13):
                text = 'Обычная рассылка'
                if broadcast_id == 8:
                    text = (
                        'Понедельник - ПЫЛЬ И ПОЛКИ\n'
                        '@omgvr_len @omgvr_mar\nПротереть полки\n'
                        'Ждем фото до/после в чатик )\n'
                        'Срок выполнения задачи: сегодня, до 20:00'
                    )
                if broadcast_id == 10:
                    text = (
                        'Четверг - САНУЗЕЛ ( Для Мар - бэк)\n'
                        '@omgvr_len @omgvr_mar\nТщательно убрать санузел\n'
                        'Срок выполнения задачи: сегодня, до 20:00'
                    )
                conn.execute(
                    '''INSERT INTO broadcasts
                       (id, text, photo, time, freq_type, freq_days, status)
                       VALUES (?, ?, 'None', '13:00', 'custom', '0', 1)''',
                    (broadcast_id, text),
                )
        conn.close()

        shift_tasks.initialize_shift_tasks_schema(self.db_path)

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        migrated = conn.execute('SELECT * FROM broadcasts WHERE id=8').fetchone()
        cleanliness = conn.execute(
            '''SELECT * FROM broadcasts
               WHERE task_category=?''',
            (shift_tasks.CATEGORY_CLEANLINESS,),
        ).fetchone()
        kashirka = [
            row[0] for row in conn.execute(
                '''SELECT label FROM shift_task_requirements
                   WHERE template_id=? AND club='Каширка' ORDER BY position''',
                (cleanliness['id'],),
            )
        ]
        maryino_special = conn.execute(
            '''SELECT title, clubs_json FROM broadcasts
               WHERE created_by='legacy:10:maryino' '''
        ).fetchone()
        regular_clubs = json.loads(conn.execute(
            'SELECT clubs_json FROM broadcasts WHERE id=10'
        ).fetchone()[0])
        conn.close()

        self.assertEqual(migrated['kind'], shift_tasks.KIND_TASK)
        self.assertEqual(json.loads(migrated['clubs_json']), ['Ленинский', 'Марьино'])
        self.assertNotIn('@omgvr_', migrated['text'])
        self.assertNotIn('Срок выполнения', migrated['text'])
        self.assertEqual(cleanliness['time'], '12:00')
        self.assertEqual(cleanliness['due_time'], '20:00')
        self.assertEqual(len(kashirka), 7)
        self.assertIn('Туалет №2', kashirka)
        self.assertEqual(maryino_special['title'], 'Глубокая уборка бэка')
        self.assertEqual(json.loads(maryino_special['clubs_json']), ['Марьино'])
        self.assertEqual(regular_clubs, ['Ленинский'])

    def test_scheduler_creates_shared_club_task_and_does_not_duplicate_notifications(self):
        shift_tasks.initialize_shift_tasks_schema(self.db_path)
        conn = sqlite3.connect(self.db_path)
        with conn:
            conn.execute(
                '''INSERT INTO users
                   (ID, login, first_name, second_name, nick_name, status, chatid)
                   VALUES (1, '@employee', 'Иван', 'Иванов', 'Иван', 0, '101')'''
            )
            conn.execute(
                '''INSERT INTO shifts
                   (dt_shift, club, dur, source, shift_login, shift_start, shift_end)
                   VALUES ('2026-09-01', 'Ленинский', 10, 'omg_shift',
                           '@employee', '10:00', '20:00')'''
            )
        conn.close()
        bot = FakeBot()
        now = datetime(2026, 9, 1, 12, 0, tzinfo=ZoneInfo('Europe/Moscow'))

        first = shift_tasks.process_shift_tasks(bot, now=now, db_path=self.db_path)
        second = shift_tasks.process_shift_tasks(bot, now=now, db_path=self.db_path)

        conn = sqlite3.connect(self.db_path)
        instances = conn.execute(
            '''SELECT COUNT(*) FROM shift_task_instances
               WHERE occurrence_date='2026-09-01' AND club='Ленинский' '''
        ).fetchone()[0]
        notifications = conn.execute(
            '''SELECT COUNT(*) FROM shift_task_notifications
               WHERE recipient_chatid='101' AND notification_type='activation' '''
        ).fetchone()[0]
        conn.close()
        self.assertEqual(first, 1)
        self.assertEqual(second, 0)
        self.assertEqual(instances, 1)
        self.assertEqual(notifications, 1)

    def test_first_scheduler_run_does_not_backfill_already_expired_tasks(self):
        shift_tasks.initialize_shift_tasks_schema(self.db_path)
        bot = FakeBot()
        now = datetime(2026, 9, 1, 23, 30, tzinfo=ZoneInfo('Europe/Moscow'))

        shift_tasks.process_shift_tasks(bot, now=now, db_path=self.db_path)

        conn = sqlite3.connect(self.db_path)
        count = conn.execute(
            '''SELECT COUNT(*) FROM shift_task_instances
               WHERE occurrence_date='2026-09-01' '''
        ).fetchone()[0]
        conn.close()
        self.assertEqual(count, 0)
        self.assertEqual(bot.messages, [])

    def test_close_warning_is_recorded_once_and_does_not_block(self):
        shift_tasks.initialize_shift_tasks_schema(self.db_path)
        shift_tasks.ensure_task_instances(
            '2026-09-01', db_path=self.db_path,
            now=datetime(2026, 9, 1, 12, 0, tzinfo=ZoneInfo('Europe/Moscow')),
        )
        bot = FakeBot()
        actor = {
            'login': '@employee', 'nick_name': 'Иван', 'chatid': '101',
        }
        chats = {'reports': '-1001', 'main_group': '-1002'}
        with patch.object(shift_tasks, 'CHATS', chats):
            first = shift_tasks.record_close_task_warning(
                bot, 'run-1', 'Ленинский', '2026-09-01', actor,
                db_path=self.db_path,
            )
            second = shift_tasks.record_close_task_warning(
                bot, 'run-1', 'Ленинский', '2026-09-01', actor,
                db_path=self.db_path,
            )

        conn = sqlite3.connect(self.db_path)
        warnings = conn.execute(
            'SELECT COUNT(*) FROM shift_task_close_warnings WHERE run_id=?',
            ('run-1',),
        ).fetchone()[0]
        conn.close()
        report_messages = [item for item in bot.messages if item[1] == '-1001']
        self.assertEqual(first['count'], 1)
        self.assertEqual(second['count'], 1)
        self.assertEqual(warnings, 1)
        self.assertEqual(len(report_messages), 1)

    def test_finishing_task_keeps_telegram_references_and_closes_shared_instance(self):
        shift_tasks.initialize_shift_tasks_schema(self.db_path)
        shift_tasks.ensure_task_instances(
            '2026-09-01', db_path=self.db_path,
            now=datetime(2026, 9, 1, 12, 0, tzinfo=ZoneInfo('Europe/Moscow')),
        )
        conn = sqlite3.connect(self.db_path)
        instance_id = conn.execute(
            '''SELECT id FROM shift_task_instances
               WHERE occurrence_date='2026-09-01' AND club='Ленинский'
               ORDER BY id LIMIT 1'''
        ).fetchone()[0]
        with conn:
            conn.execute(
                '''INSERT INTO shift_task_drafts
                   (chatid, instance_id, state, updated_at)
                   VALUES ('101', ?, 'collecting', '2026-09-01 12:05:00')''',
                (instance_id,),
            )
            conn.executemany(
                '''INSERT INTO shift_task_media (
                       instance_id, telegram_file_id, telegram_file_unique_id,
                       media_type, file_size, submitted_by_chatid,
                       submitted_by_login, created_at, state
                   ) VALUES (?, ?, ?, 'photo', 1000, '101', '@employee',
                             '2026-09-01 12:05:00', 'draft')''',
                [
                    (instance_id, f'telegram-photo-id-{index}', f'unique-photo-id-{index}')
                    for index in range(1, 8)
                ],
            )
        conn.close()
        bot = FakeBot()
        call = SimpleNamespace(
            id='callback-1',
            message=SimpleNamespace(chat=SimpleNamespace(id=101)),
        )
        actor = {'chatid': '101', 'login': '@employee', 'name': 'Иван'}
        with (
            patch.object(shift_tasks, 'DB_PATH', self.db_path),
            patch.object(shift_tasks, '_actor_snapshot', return_value=actor),
            patch.object(shift_tasks, 'CHATS', {
                'reports': '-1001', 'main_group': '-1002',
            }),
        ):
            shift_tasks._finish_task(call, bot, instance_id)

        conn = sqlite3.connect(self.db_path)
        instance = conn.execute(
            '''SELECT status, completed_by_login, report_chatid
               FROM shift_task_instances WHERE id=?''',
            (instance_id,),
        ).fetchone()
        media = conn.execute(
            '''SELECT telegram_file_id, state FROM shift_task_media
               WHERE instance_id=? ORDER BY id LIMIT 1''',
            (instance_id,),
        ).fetchone()
        draft_count = conn.execute(
            'SELECT COUNT(*) FROM shift_task_drafts WHERE chatid=?', ('101',),
        ).fetchone()[0]
        conn.close()
        self.assertEqual(instance, ('completed', '@employee', '-1001'))
        self.assertEqual(media, ('telegram-photo-id-1', 'submitted'))
        self.assertEqual(draft_count, 0)
        self.assertTrue(any(item[0] == 'album' for item in bot.messages))


if __name__ == '__main__':
    unittest.main()
