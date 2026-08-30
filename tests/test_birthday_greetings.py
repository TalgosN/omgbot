import os
import sqlite3
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import birthday_greetings as greetings


def create_schema(db_path, birthday=None, status=0):
    birthday = birthday or date.today()
    conn = sqlite3.connect(db_path)
    conn.executescript(
        '''
        CREATE TABLE users (
            ID INTEGER PRIMARY KEY,
            login TEXT,
            first_name TEXT,
            second_name TEXT,
            nick_name TEXT,
            bday TEXT,
            status INTEGER,
            chatid TEXT
        );
        CREATE TABLE shifts (
            shift_second_name TEXT,
            shift_first_name TEXT,
            dt_shift TEXT,
            club TEXT,
            dur REAL,
            shift_login TEXT
        );
        CREATE TABLE reviews (
            ID INTEGER PRIMARY KEY,
            who TEXT,
            d_rep TEXT,
            amount REAL
        );
        CREATE TABLE afterparty (
            ID INTEGER PRIMARY KEY,
            dt_rep TEXT,
            who TEXT,
            status TEXT
        );
        CREATE TABLE sert (
            ID INTEGER PRIMARY KEY,
            d_rep TEXT,
            who TEXT
        );
        CREATE TABLE abik (
            ID INTEGER PRIMARY KEY,
            d_rep TEXT,
            who TEXT
        );
        CREATE TABLE initiative (
            ID INTEGER PRIMARY KEY,
            dt_rep TEXT,
            who TEXT,
            status TEXT
        );
        CREATE TABLE birthday (
            ID INTEGER PRIMARY KEY,
            dt_rep TEXT,
            who TEXT,
            status TEXT
        );
        CREATE TABLE tasks (
            ID INTEGER PRIMARY KEY,
            type TEXT,
            status TEXT
        );
        CREATE TABLE task_events (
            id INTEGER PRIMARY KEY,
            task_id INTEGER,
            event_type TEXT,
            event_at TEXT,
            actor_login TEXT
        );
        CREATE TABLE equipment_events (
            id INTEGER PRIMARY KEY,
            event_type TEXT,
            event_at TEXT,
            actor_login TEXT
        );
        CREATE TABLE shift_webapp_runs (
            id TEXT PRIMARY KEY,
            login TEXT,
            completed_at TEXT
        );
        '''
    )
    conn.execute(
        '''INSERT INTO users VALUES (
               1, '@tester', 'Тест', 'Тестов', 'Тестер', ?, ?, '123'
           )''',
        (birthday.isoformat(), status),
    )
    conn.commit()
    conn.close()


class BirthdayGreetingsTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / 'birthday.sqlite')
        create_schema(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def user(self, status=0):
        return {
            'ID': 1,
            'login': '@tester',
            'first_name': 'Тест',
            'second_name': 'Тестов',
            'nick_name': 'Тестер',
            'bday': '2000-08-30',
            'status': status,
        }

    def test_role_priorities_select_only_positive_facts(self):
        stats = {
            'shifts': 50,
            'hours': 300,
            'clubs': 3,
            'kpi_peak': 1.25,
            'reviews': 20,
            'created_repairs': 4,
            'solved_repairs': 7,
            'replacements': 2,
            'shift_reports': 6,
            'initiatives': 3,
        }

        technician = greetings.select_positive_facts(
            stats, greetings.ROLE_TECHNICIAN,
        )
        manager = greetings.select_positive_facts(
            stats, greetings.ROLE_MANAGER,
        )

        self.assertIn('7 решённых ремонтов', technician[0])
        self.assertIn('3 принятые инициативы', manager[0])
        self.assertEqual(
            greetings.select_positive_facts(stats, greetings.ROLE_OWNER),
            [],
        )

    def test_employee_message_contains_facts_and_viarych_signature(self):
        stats = {'shifts': 50, 'hours': 300, 'clubs': 3, 'kpi_peak': 1.25}
        generator = Mock(return_value='Тестер, отличного нового личного года!')
        with patch.object(
            greetings,
            'collect_personal_year_stats',
            return_value=stats,
        ):
            payload = greetings.build_birthday_message(
                self.user(),
                today=date(2026, 8, 30),
                db_path=self.db_path,
                generator=generator,
            )

        self.assertIn('🎂 Сегодня день рождения у @tester — Тестер!', payload['text'])
        self.assertIn('✨ За этот год:', payload['text'])
        self.assertTrue(payload['text'].endswith('— Виарыч 🤖💜'))
        generator.assert_called_once()

    def test_owner_message_has_no_facts_block(self):
        generator = Mock(return_value='Тестер, с днём рождения!')
        payload = greetings.build_birthday_message(
            self.user(greetings.ROLE_OWNER),
            today=date(2026, 8, 30),
            db_path=self.db_path,
            generator=generator,
        )

        self.assertNotIn('За этот год', payload['text'])
        self.assertEqual(payload['facts'], [])
        generator.assert_called_once_with('Тестер', greetings.ROLE_OWNER, [])

    def test_preference_is_enabled_by_default_and_can_be_toggled(self):
        self.assertTrue(greetings.birthday_public_enabled(1, self.db_path))
        self.assertFalse(greetings.toggle_birthday_public(1, self.db_path))
        self.assertFalse(greetings.birthday_public_enabled(1, self.db_path))
        self.assertTrue(greetings.toggle_birthday_public(1, self.db_path))

    def test_daily_delivery_is_idempotent(self):
        now = datetime(2026, 8, 30, 10, 15, tzinfo=ZoneInfo('Europe/Moscow'))
        conn = sqlite3.connect(self.db_path)
        conn.execute("UPDATE users SET bday='2000-08-30'")
        conn.commit()
        conn.close()
        bot = Mock()
        payload = {
            'text': 'Готовое поздравление',
            'facts': ['50 смен'],
            'stats': {},
            'source': 'openrouter',
            'prompt_version': greetings.PROMPT_VERSION,
        }

        with patch.object(
            greetings,
            'build_birthday_message',
            return_value=payload,
        ) as build:
            first = greetings.send_today_birthday_greetings(
                bot, '-100-main', now=now, db_path=self.db_path,
            )
            second = greetings.send_today_birthday_greetings(
                bot, '-100-main', now=now, db_path=self.db_path,
            )

        self.assertEqual((first, second), (1, 0))
        build.assert_called_once()
        bot.send_message.assert_called_once_with('-100-main', 'Готовое поздравление')

    def test_failed_send_reuses_saved_generation_on_retry(self):
        now = datetime(2026, 8, 30, 11, 0, tzinfo=ZoneInfo('Europe/Moscow'))
        conn = sqlite3.connect(self.db_path)
        conn.execute("UPDATE users SET bday='2000-08-30'")
        conn.commit()
        conn.close()
        bot = Mock()
        bot.send_message.side_effect = [RuntimeError('telegram down'), None]
        payload = {
            'text': 'Один и тот же текст',
            'facts': [],
            'stats': {},
            'source': 'fallback',
            'prompt_version': greetings.PROMPT_VERSION,
        }

        with patch.object(
            greetings,
            'build_birthday_message',
            return_value=payload,
        ) as build:
            self.assertEqual(greetings.send_today_birthday_greetings(
                bot, '-100-main', now=now, db_path=self.db_path,
            ), 0)
            self.assertEqual(greetings.send_today_birthday_greetings(
                bot, '-100-main', now=now, db_path=self.db_path,
            ), 1)

        build.assert_called_once()
        self.assertEqual(bot.send_message.call_count, 2)

    def test_check_does_not_send_before_configured_time(self):
        bot = Mock()
        now = datetime(2026, 8, 30, 9, 0, tzinfo=ZoneInfo('Europe/Moscow'))

        result = greetings.send_today_birthday_greetings(
            bot, '-100-main', now=now, db_path=self.db_path,
        )

        self.assertEqual(result, 0)
        bot.send_message.assert_not_called()

    def test_openrouter_prompt_for_owner_contains_no_facts(self):
        response = Mock()
        response.json.return_value = {
            'choices': [{'message': {'content': 'Готовое поздравление'}}],
        }
        session = Mock()
        session.post.return_value = response
        with patch.dict(
            os.environ,
            {'OPENROUTER_API_KEY': 'secret', 'OPENROUTER_MODEL': 'test/model'},
        ):
            result = greetings.generate_openrouter_greeting(
                'Тестер', greetings.ROLE_OWNER, [], session=session,
            )

        self.assertEqual(result, 'Готовое поздравление')
        request_body = session.post.call_args.kwargs['json']
        self.assertEqual(request_body['model'], 'test/model')
        self.assertIn('Не используй статистику', request_body['messages'][1]['content'])
        self.assertNotIn('Подтверждённые положительные факты', request_body['messages'][1]['content'])

    def test_non_leap_year_uses_february_28_for_february_29(self):
        self.assertTrue(greetings._is_birthday_today(
            date(2000, 2, 29), date(2026, 2, 28),
        ))


if __name__ == '__main__':
    unittest.main()
