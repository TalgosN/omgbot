import os
import sqlite3
import tempfile
import unittest
from contextlib import ExitStack, closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from test_kpi import load_kpi_module


class LocalOnlyHashtagsTest(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.db_path = str(Path(directory) / 'hashtags.sqlite')
        self.kpi = load_kpi_module()
        self.real_connect = sqlite3.connect
        with closing(self.real_connect(self.db_path)) as conn, conn:
            conn.executescript('''
                CREATE TABLE shifts (dt_shift TEXT, shift_login TEXT, club TEXT);
                CREATE TABLE afterparty (dt_rep TEXT, who TEXT, club TEXT, desc TEXT);
                CREATE TABLE initiative (dt_rep TEXT, who TEXT, club TEXT, desc TEXT);
                CREATE TABLE sert (num TEXT, d_rep TEXT, who TEXT, bonus REAL);
                CREATE TABLE abik (num TEXT, d_rep TEXT, who TEXT, bonus REAL);
                CREATE TABLE reviews (who TEXT, d_rep TEXT, amount INTEGER, desc TEXT);
            ''')
        self.stack.enter_context(patch.dict(os.environ, {'HASHTAGS_LOCAL_ONLY': 'true'}))
        self.stack.enter_context(patch.object(
            self.kpi.sqlite3, 'connect',
            side_effect=lambda _path, *args, **kwargs: self.real_connect(self.db_path, *args, **kwargs),
        ))
        self.get = self.stack.enter_context(patch.object(self.kpi.requests, 'get'))
        self.post = self.stack.enter_context(patch.object(self.kpi.requests, 'post'))
        self.get.side_effect = AssertionError('Unexpected Shift GET in local-only mode')
        self.post.side_effect = AssertionError('Unexpected Shift POST in local-only mode')
        self.stack.enter_context(patch.object(self.kpi, 'get_active_custom_goal_by_hashtag', return_value=None))
        self.stack.enter_context(patch.object(self.kpi, 'Insert', self.insert_action, create=True))
        self.stack.enter_context(patch.object(self.kpi, 'Insert_bonus', self.insert_bonus, create=True))

    def tearDown(self):
        self.get.assert_not_called()
        self.post.assert_not_called()

    def insert_action(self, table, *values):
        with closing(self.real_connect(self.db_path)) as conn, conn:
            conn.execute(f'INSERT INTO {table} VALUES (?, ?, ?, ?)', values)

    def insert_bonus(self, table, *values):
        with closing(self.real_connect(self.db_path)) as conn, conn:
            conn.execute(f'INSERT INTO {table} VALUES (?, ?, ?, ?)', values)

    def message(self, text):
        return SimpleNamespace(
            text=text, message_id=101, id=101,
            chat=SimpleNamespace(id=-100500),
            from_user=SimpleNamespace(username='employee'),
        )

    def query(self, sql):
        with closing(self.real_connect(self.db_path)) as conn:
            return conn.execute(sql).fetchall()

    def test_default_is_online_and_flag_can_be_switched_back(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(self.kpi.hashtags_local_only())
        for value, expected in (('true', True), (' YES ', True), ('1', True), ('false', False), ('0', False)):
            with self.subTest(value=value), patch.dict(os.environ, {'HASHTAGS_LOCAL_ONLY': value}):
                self.assertEqual(self.kpi.hashtags_local_only(), expected)

    def test_all_local_kpi_are_saved_once_without_schedule_or_remote_requests(self):
        message = self.message(
            '#продление Анна 15:00–16:00\n'
            '#инициатива Починил крепление\n'
            '#серт 1234 1800\n'
            '#абик 512 3000\n'
            '#отзывы 2 2ГИС'
        )
        self.assertEqual(self.kpi.hash_handle(message)[0], self.kpi.KPI_SUCCESS)
        self.assertEqual(self.kpi.hash_handle(message)[0], self.kpi.KPI_SUCCESS)
        for table in ('afterparty', 'initiative', 'sert', 'abik', 'reviews'):
            self.assertEqual(self.query(f'SELECT COUNT(*) FROM {table}'), [(1,)])
        self.assertEqual(self.query('SELECT club FROM afterparty'), [('Неизвестно',)])
        self.assertEqual(self.query('SELECT DISTINCT status,source FROM hashtag_events'), [('applied', 'local_kpi')])
        self.assertEqual(self.query('SELECT COUNT(*) FROM hashtag_events'), [(5,)])

    def test_cached_schedule_is_optional_and_ambiguous_club_is_not_guessed(self):
        today = datetime.now(timezone(timedelta(hours=3))).date().isoformat()
        with closing(self.real_connect(self.db_path)) as conn, conn:
            conn.execute('INSERT INTO shifts VALUES (?, ?, ?)', (today, '@EMPLOYEE', 'Марьино'))
        self.assertEqual(self.kpi.get_user_club_today('employee'), 'Марьино')
        with closing(self.real_connect(self.db_path)) as conn, conn:
            conn.execute('INSERT INTO shifts VALUES (?, ?, ?)', (today, '@employee', 'Каширка'))
        self.assertIsNone(self.kpi.get_user_club_today('@employee'))

    def test_payroll_hashtags_keep_values_units_comments_and_deduplicate(self):
        message = self.message(
            '#др Анна\n#двойная 2,5 подмена\n#автосим 125,50\n#активация 500 вечерняя продажа'
        )
        self.assertEqual(self.kpi.hash_handle(message)[0], self.kpi.KPI_SUCCESS)
        self.assertEqual(self.kpi.hash_handle(message)[0], self.kpi.KPI_SUCCESS)
        self.assertEqual(self.query(
            'SELECT hashtag,value,value_unit,comment,status,source,hashtag_index FROM hashtag_events ORDER BY id'
        ), [
            ('#др', 500, 'rubles', 'Анна', 'applied', 'local_hashtag', 0),
            ('#двойная', 2.5, 'hours', 'подмена', 'applied', 'local_hashtag', 1),
            ('#автосим', 125.5, 'rubles', '', 'applied', 'local_hashtag', 2),
            ('#активация', 500, 'rubles', 'вечерняя продажа', 'applied', 'local_hashtag', 3),
        ])

    def test_invalid_payroll_amount_is_rejected_before_any_batch_writes(self):
        for args in ('0', '-5', 'не число', '1.5.2', '9' * 500):
            with self.subTest(args=args):
                result = self.kpi.hash_handle(self.message('#серт 1234 1800\n#активация ' + args))
                self.assertEqual(result[0], self.kpi.KPI_INVALID)
                self.assertEqual(self.query('SELECT COUNT(*) FROM sert'), [(0,)])

    def test_custom_goal_stays_local(self):
        goal = {'hashtag': '#повторы', 'unit_label': 'шт.', 'integer_only': True}
        with patch.object(self.kpi, 'get_active_custom_goal_by_hashtag', return_value=goal), \
                patch.object(self.kpi, '_get_hashtag_rule') as rule:
            message = self.message('#повторы 2 постоянные гости')
            self.assertEqual(self.kpi.hash_handle(message)[0], self.kpi.KPI_SUCCESS)
            self.assertEqual(self.kpi.hash_handle(message)[0], self.kpi.KPI_SUCCESS)
        rule.assert_not_called()
        self.assertEqual(self.query('SELECT hashtag,value,status FROM hashtag_events'), [('#повторы', 2, 'applied')])

    def test_reenabling_shift_restores_remote_processing_without_replaying_offline_event(self):
        message = self.message('#активация 500')
        self.assertEqual(self.kpi.hash_handle(message)[0], self.kpi.KPI_SUCCESS)
        with patch.dict(os.environ, {'HASHTAGS_LOCAL_ONLY': 'false'}), \
                patch.object(self.kpi, '_get_hashtag_rule', return_value=None), \
                patch.object(self.kpi, '_post_remote_hashtag') as post:
            self.assertEqual(self.kpi.hash_handle(message)[0], self.kpi.KPI_REMOTE_SUCCESS)
            post.assert_not_called()
            post.return_value = (self.kpi.KPI_REMOTE_SUCCESS, '', {'ok': True}, None)
            fresh_message = self.message('#активация 700')
            fresh_message.id = fresh_message.message_id = 102
            self.assertEqual(self.kpi.hash_handle(fresh_message)[0], self.kpi.KPI_REMOTE_SUCCESS)
            post.assert_called_once()
        self.assertEqual(self.query('SELECT COUNT(*) FROM hashtag_events'), [(2,)])

    def test_offline_rules_are_available_without_network(self):
        rules = self.kpi.get_remote_hashtag_rules()
        self.assertEqual({rule['hashtag'] for rule in rules}, {'#др', '#двойная', '#автосим', '#активация'})

    def test_penalty_still_requires_owner_permission(self):
        with closing(self.real_connect(self.db_path)) as conn, conn:
            conn.execute('CREATE TABLE users (login TEXT)')
            conn.execute("INSERT INTO users VALUES ('@target')")
        with patch.object(self.kpi, 'role_of', return_value=0):
            result = self.kpi.hash_handle(self.message('#штраф @target причина'))
        self.assertEqual(result[0], self.kpi.KPI_INVALID)


if __name__ == '__main__':
    unittest.main()
