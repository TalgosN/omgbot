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
        index = len(self.messages) + 1
        message = SimpleNamespace(
            message_id=index,
            media_group_id=None,
            photo=[SimpleNamespace(
                file_id=f'photo-{index}', file_unique_id=f'photo-unique-{index}',
            )],
        )
        self.messages.append(('photo', str(chat_id), photo, kwargs))
        return message

    def send_video(self, chat_id, video, **kwargs):
        index = len(self.messages) + 1
        message = SimpleNamespace(
            message_id=index,
            media_group_id=None,
            video=SimpleNamespace(
                file_id=f'video-{index}', file_unique_id=f'video-unique-{index}',
            ),
        )
        self.messages.append(('video', str(chat_id), video, kwargs))
        return message

    def send_media_group(self, chat_id, media):
        self.messages.append(('album', str(chat_id), media, {}))
        return [
            SimpleNamespace(
                message_id=100 + index,
                media_group_id='album-result',
                photo=[SimpleNamespace(
                    file_id=f'album-photo-{index}',
                    file_unique_id=f'album-photo-unique-{index}',
                )],
                video=SimpleNamespace(
                    file_id=f'album-video-{index}',
                    file_unique_id=f'album-video-unique-{index}',
                ),
            )
            for index in range(len(media))
        ]

    def edit_message_reply_markup(self, **_kwargs):
        return None

    def edit_message_text(self, *_args, **_kwargs):
        return None

    def answer_callback_query(self, *_args, **_kwargs):
        return None


class FakeTimer:
    def __init__(self, interval, function, args=()):
        self.interval = interval
        self.function = function
        self.args = args
        self.cancelled = False
        self.started = False
        self.daemon = False

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True

    def fire(self):
        self.function(*self.args)


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
        media_columns = {
            row[1] for row in conn.execute('PRAGMA table_info(shift_task_media)')
        }
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
        self.assertIn('telegram_message_id', media_columns)
        self.assertIn('media_group_id', media_columns)

    def test_media_group_progress_is_debounced_to_one_message(self):
        timers = []

        def timer_factory(*args, **kwargs):
            timer = FakeTimer(*args, **kwargs)
            timers.append(timer)
            return timer

        shift_tasks._task_media_group_timers.clear()
        with (
            patch.object(shift_tasks.threading, 'Timer', side_effect=timer_factory),
            patch.object(shift_tasks, '_send_task_media_progress') as progress,
        ):
            shift_tasks._queue_task_media_group_progress(
                FakeBot(), '101', 7, 'album-1', db_path=self.db_path,
            )
            shift_tasks._queue_task_media_group_progress(
                FakeBot(), '101', 7, 'album-1', db_path=self.db_path,
            )
            timers[0].fire()
            timers[1].fire()

        self.assertTrue(timers[0].cancelled)
        self.assertTrue(timers[0].started)
        self.assertTrue(timers[1].started)
        progress.assert_called_once()
        shift_tasks._task_media_group_timers.clear()

    def test_schema_discards_legacy_bot_drafts_and_reopens_instances(self):
        shift_tasks.initialize_shift_tasks_schema(self.db_path)
        shift_tasks.ensure_task_instances(
            '2026-09-01', db_path=self.db_path,
            now=datetime(2026, 9, 1, 12, 0, tzinfo=ZoneInfo('Europe/Moscow')),
        )
        conn = sqlite3.connect(self.db_path)
        instance_id = conn.execute(
            '''SELECT id FROM shift_task_instances
               WHERE occurrence_date='2026-09-01' ORDER BY id LIMIT 1'''
        ).fetchone()[0]
        with conn:
            conn.execute(
                '''UPDATE shift_task_instances
                   SET status='in_progress', started_at='2026-09-01 12:05:00',
                       started_by_login='@employee' WHERE id=?''',
                (instance_id,),
            )
            conn.execute(
                '''INSERT INTO shift_task_drafts
                   (chatid, instance_id, state, updated_at)
                   VALUES ('101', ?, 'collecting', '2026-09-01 12:05:00')''',
                (instance_id,),
            )
            conn.execute(
                '''INSERT INTO shift_task_media (
                       instance_id, telegram_file_id, telegram_file_unique_id,
                       media_type, submitted_by_chatid, created_at, state
                   ) VALUES (?, 'photo-id', 'photo-unique', 'photo', '101',
                             '2026-09-01 12:05:00', 'draft')''',
                (instance_id,),
            )
        conn.close()

        shift_tasks.initialize_shift_tasks_schema(self.db_path)

        conn = sqlite3.connect(self.db_path)
        instance = conn.execute(
            '''SELECT status, started_at, started_by_login
               FROM shift_task_instances WHERE id=?''',
            (instance_id,),
        ).fetchone()
        draft_count = conn.execute(
            'SELECT COUNT(*) FROM shift_task_drafts'
        ).fetchone()[0]
        media_count = conn.execute(
            "SELECT COUNT(*) FROM shift_task_media WHERE state='draft'"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(instance, ('pending', None, None))
        self.assertEqual(draft_count, 0)
        self.assertEqual(media_count, 0)

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
                       telegram_message_id, media_group_id, media_type,
                       file_size, submitted_by_chatid, submitted_by_login,
                       created_at, state
                   ) VALUES (?, ?, ?, ?, 'album-1', 'photo', 1000, '101',
                             '@employee', '2026-09-01 12:05:00', 'draft')''',
                [
                    (
                        instance_id, f'telegram-photo-id-{index}',
                        f'unique-photo-id-{index}', 108 - index,
                    )
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
        album = next(item for item in bot.messages if item[0] == 'album')
        self.assertEqual(
            [item.media for item in album[2]],
            [f'telegram-photo-id-{index}' for index in range(7, 0, -1)],
        )

    def test_app_lists_starts_and_completes_existing_task_instance(self):
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
        now = datetime(2026, 9, 1, 12, 0, tzinfo=ZoneInfo('Europe/Moscow'))
        actor = {
            'chatid': '101', 'login': '@employee', 'nick_name': 'Иван',
            'status': 0,
        }
        tasks = shift_tasks.app_task_list(
            actor, db_path=self.db_path, now=now,
        )
        self.assertTrue(tasks)
        task = shift_tasks.start_app_task(
            actor, tasks[0]['id'], db_path=self.db_path,
        )
        self.assertEqual(task['status'], 'in_progress')

        bot = FakeBot()
        uploads = [{
            'content': b'photo', 'filename': 'report.jpg',
            'media_type': 'photo', 'file_size': 5,
        }] * task['required_attachments']
        with patch.object(shift_tasks, 'CHATS', {
            'reports': '-1001', 'main_group': '-1002',
        }):
            result = shift_tasks.complete_app_task(
                actor, task['id'], uploads, bot, db_path=self.db_path,
            )

        self.assertEqual(result['status'], 'completed')
        conn = sqlite3.connect(self.db_path)
        stored = conn.execute(
            '''SELECT status, completed_by_login, report_chatid
               FROM shift_task_instances WHERE id=?''',
            (task['id'],),
        ).fetchone()
        media_count = conn.execute(
            '''SELECT COUNT(*) FROM shift_task_media
               WHERE instance_id=? AND state='submitted' ''',
            (task['id'],),
        ).fetchone()[0]
        conn.close()
        self.assertEqual(stored, ('completed', '@employee', '-1001'))
        self.assertEqual(media_count, task['required_attachments'])

    def test_information_broadcast_can_be_converted_without_copy(self):
        shift_tasks.initialize_shift_tasks_schema(self.db_path)
        conn = sqlite3.connect(self.db_path)
        with conn:
            cursor = conn.execute(
                '''INSERT INTO broadcasts
                   (text, photo, time, freq_type, freq_days, status, kind)
                   VALUES ('Проверить склад', 'photo-id', '12:00',
                           'custom', '13', 1, 'information')'''
            )
            broadcast_id = cursor.lastrowid
        conn.close()
        shift_tasks._task_admin_drafts[101] = {
            'conversion_broadcast_id': broadcast_id,
            'instructions': 'Проверить склад',
            'time': '12:00', 'freq_type': 'custom', 'freq_days': '13',
            'title': 'Проверка склада', 'clubs': ['Ленинский'],
            'due_time': '18:00', 'created_by': '@manager',
        }
        with patch.object(shift_tasks, 'DB_PATH', self.db_path):
            shift_tasks._save_task_template(101, FakeBot(), False)

        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            '''SELECT id, kind, text, photo, time, freq_type, freq_days,
                      title, clubs_json, due_time
               FROM broadcasts WHERE id=?''',
            (broadcast_id,),
        ).fetchone()
        count = conn.execute('SELECT COUNT(*) FROM broadcasts').fetchone()[0]
        conn.close()
        self.assertEqual(count, 2)  # системная задача чистоты + эта запись
        self.assertEqual(row[0], broadcast_id)
        self.assertEqual(row[1], shift_tasks.KIND_TASK)
        self.assertEqual(row[2:7], (
            'Проверить склад', 'photo-id', '12:00', 'custom', '13',
        ))
        self.assertEqual(row[7], 'Проверка склада')
        self.assertEqual(json.loads(row[8]), ['Ленинский'])
        self.assertEqual(row[9], '18:00')

    def test_task_notification_opens_shift_tasks_webapp(self):
        with (
            patch.object(shift_tasks, 'KPI_WEBAPP_URL', 'https://bot.omg-vr.ru/'),
            patch('builtins.open', side_effect=OSError),
        ):
            markup = shift_tasks._task_markup(17)
        button = markup.keyboard[0][0]
        self.assertEqual(button.web_app.url, 'https://bot.omg-vr.ru/shift/tasks?task=17')

    def test_task_notification_has_no_bot_execution_fallback(self):
        with (
            patch.object(shift_tasks, 'KPI_WEBAPP_URL', ''),
            patch('builtins.open', side_effect=OSError),
        ):
            markup = shift_tasks._task_markup(17)
        self.assertIsNone(markup)


if __name__ == '__main__':
    unittest.main()
