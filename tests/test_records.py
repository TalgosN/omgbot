import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import records


def create_records_schema(db_path):
    conn = sqlite3.connect(db_path)
    conn.executescript(
        '''
        CREATE TABLE users (
            ID INTEGER PRIMARY KEY,
            login TEXT,
            first_name TEXT,
            second_name TEXT,
            nick_name TEXT,
            status INTEGER
        );
        CREATE TABLE shifts (
            shift_second_name TEXT,
            shift_first_name TEXT,
            dt_shift TEXT,
            club TEXT,
            dur REAL,
            source TEXT,
            shift_login TEXT
        );
        CREATE TABLE anketi (
            ID INTEGER PRIMARY KEY,
            id_ank INTEGER,
            dt_ank TEXT,
            club_ank TEXT
        );
        CREATE TABLE afterparty (
            ID INTEGER PRIMARY KEY,
            dt_rep TEXT,
            who TEXT,
            club TEXT,
            desc TEXT,
            status TEXT
        );
        CREATE TABLE birthday (
            ID INTEGER PRIMARY KEY,
            dt_rep TEXT,
            who TEXT,
            club TEXT,
            desc TEXT,
            status TEXT
        );
        CREATE TABLE initiative (
            ID INTEGER PRIMARY KEY,
            dt_rep TEXT,
            who TEXT,
            club TEXT,
            desc TEXT,
            status TEXT
        );
        CREATE TABLE sert (
            ID INTEGER PRIMARY KEY,
            num TEXT,
            d_rep TEXT,
            who TEXT,
            bonus REAL
        );
        CREATE TABLE abik (
            ID INTEGER PRIMARY KEY,
            num TEXT,
            d_rep TEXT,
            who TEXT,
            bonus REAL
        );
        CREATE TABLE penalty (
            ID INTEGER PRIMARY KEY,
            dt TEXT,
            name TEXT,
            desc TEXT
        );
        CREATE TABLE reviews (
            ID INTEGER PRIMARY KEY,
            who TEXT,
            d_rep TEXT,
            amount REAL,
            desc TEXT
        );
        CREATE TABLE tasks (
            ID INTEGER PRIMARY KEY,
            dtrep TEXT,
            type TEXT,
            status TEXT
        );
        CREATE TABLE task_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER,
            event_type TEXT,
            event_at TEXT,
            actor_login TEXT
        );
        CREATE TABLE equipment_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            unit_id INTEGER,
            event_type TEXT,
            event_at TEXT,
            actor_login TEXT,
            actor_name TEXT
        );
        CREATE TABLE shift_webapp_runs (
            id TEXT PRIMARY KEY,
            login TEXT,
            action TEXT,
            warning_sent_at TEXT,
            completed_at TEXT
        );
        '''
    )
    conn.executemany(
        '''INSERT INTO users(
               ID, login, first_name, second_name, nick_name, status
           ) VALUES (?, ?, ?, ?, ?, ?)''',
        (
            (1, '@active', 'Аня', 'Активная', 'Аня', 0),
            (2, '@archive', 'Артур', 'Архивный', 'Артур', -1),
        ),
    )
    first_day = date.today().replace(day=1) - timedelta(days=20)
    first_day = first_day.replace(day=1)
    shifts = []
    for login, count in (('@active', 12), ('@archive', 20)):
        for offset in range(count):
            shifts.append((
                '', '', (first_day + timedelta(days=offset)).isoformat(),
                'Марьино', 6, 'test', login,
            ))
    conn.executemany(
        '''INSERT INTO shifts(
               shift_second_name, shift_first_name, dt_shift, club,
               dur, source, shift_login
           ) VALUES (?, ?, ?, ?, ?, ?, ?)''',
        shifts,
    )
    conn.execute(
        '''INSERT INTO reviews(ID, who, d_rep, amount, desc)
           VALUES (1, '@active', ?, 11, '')''',
        (first_day.isoformat(),),
    )
    conn.executescript(
        f'''
        INSERT INTO tasks(ID, dtrep, type, status)
        VALUES (1, '{first_day.isoformat()}', 'Ремонт', 'Выполнено');
        INSERT INTO task_events(task_id, event_type, event_at, actor_login)
        VALUES
            (1, 'created', '{first_day.isoformat()}T10:00:00+03:00', '@active'),
            (1, 'solution', '{first_day.isoformat()}T12:00:00+03:00', '@active'),
            (1, 'confirmed', '{first_day.isoformat()}T13:00:00+03:00', '@active');
        INSERT INTO equipment_events(
            unit_id, event_type, event_at, actor_login, actor_name
        ) VALUES (
            1, 'replaced', '{first_day.isoformat()}T12:30:00+03:00',
            '@active', 'Аня'
        );
        INSERT INTO shift_webapp_runs(
            id, login, action, warning_sent_at, completed_at
        ) VALUES ('run-1', '@active', 'open', NULL, '{first_day.isoformat()} 10:00:00');
        '''
    )
    conn.commit()
    conn.close()


class RecordsTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / 'records.sqlite')
        create_records_schema(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_retroactive_baseline_is_saved_without_notifications(self):
        bot = Mock()

        state = records.refresh_records_achievements(
            self.db_path,
            bot=bot,
            main_chat_id='-100-main',
        )

        conn = sqlite3.connect(self.db_path)
        unlocks = conn.execute(
            '''SELECT source, notified_at
               FROM records_achievement_unlocks'''
        ).fetchall()
        conn.close()

        bot.send_message.assert_not_called()
        self.assertTrue(unlocks)
        self.assertTrue(all(source == 'baseline' for source, _ in unlocks))
        self.assertTrue(all(notified_at for _, notified_at in unlocks))
        self.assertEqual(
            state['records'][0]['holders'][0]['login'], '@active',
        )
        self.assertEqual(
            state['archive_records'][0]['holders'][0]['login'], '@archive',
        )

    def test_catalog_v2_replaces_old_unlocks_without_notifications(self):
        bot = Mock()
        records.initialize_records_schema(self.db_path)
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            '''INSERT INTO records_meta(key, value, updated_at)
               VALUES ('catalog_version', '1', datetime('now'))'''
        )
        conn.execute(
            '''INSERT INTO records_achievement_unlocks(
                   employee_login, achievement_key, tier, value,
                   unlocked_at, source, notified_at
               ) VALUES (
                   '@active', 'shifts', 3, 12,
                   datetime('now'), 'progress', datetime('now')
               )'''
        )
        conn.commit()
        conn.close()

        records.refresh_records_achievements(
            self.db_path,
            bot=bot,
            main_chat_id='-100-main',
        )

        conn = sqlite3.connect(self.db_path)
        shift_unlocks = conn.execute(
            '''SELECT COUNT(*) FROM records_achievement_unlocks
               WHERE employee_login='@active' AND achievement_key='shifts' ''',
        ).fetchone()[0]
        version = conn.execute(
            "SELECT value FROM records_meta WHERE key='catalog_version'"
        ).fetchone()[0]
        pending = conn.execute(
            '''SELECT COUNT(*) FROM records_achievement_unlocks
               WHERE notified_at IS NULL'''
        ).fetchone()[0]
        conn.close()

        self.assertEqual(shift_unlocks, 0)
        self.assertEqual(version, '2')
        self.assertEqual(pending, 0)
        bot.send_message.assert_not_called()

    def test_new_tier_sends_one_message_only_to_configured_main_group(self):
        bot = Mock()
        records.refresh_records_achievements(
            self.db_path,
            bot=bot,
            main_chat_id='-100-main',
        )
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            '''INSERT INTO reviews(ID, who, d_rep, amount, desc)
               VALUES (2, '@active', date('now'), 40, '')'''
        )
        conn.commit()
        conn.close()

        records.refresh_records_achievements(
            self.db_path,
            bot=bot,
            main_chat_id='-100-main',
        )
        records.refresh_records_achievements(
            self.db_path,
            bot=bot,
            main_chat_id='-100-main',
        )

        bot.send_message.assert_called_once()
        args, kwargs = bot.send_message.call_args
        self.assertEqual(args[0], '-100-main')
        self.assertIn('Пятизвёздочный', args[1])
        self.assertIn('серебро', args[1])
        self.assertEqual(kwargs['parse_mode'], 'HTML')

    def test_repair_reporting_and_solution_achievements_are_separate(self):
        conn = sqlite3.connect(self.db_path)
        conn.executescript(
            '''INSERT INTO tasks(ID, dtrep, type, status)
               VALUES (2, date('now'), 'Общее обращение', 'Выполнено');
               INSERT INTO task_events(
                   task_id, event_type, event_at, actor_login
               ) VALUES (
                   2, 'created', datetime('now'), '@active'
               );'''
        )
        conn.commit()
        conn.close()

        state = records.calculate_records_state(self.db_path)
        employee = state['stats']['@active']

        self.assertEqual(employee['created_repairs'], 1)
        self.assertEqual(employee['useful_repairs'], 1)
        self.assertEqual(employee['solved_tasks'], 1)
        self.assertEqual(employee['solved_repairs'], 1)
        self.assertEqual(employee['first_try'], 1)
        self.assertEqual(employee['fast_solutions'], 1)
        self.assertEqual(employee['replacements'], 1)

    def test_monthly_kpi_records_require_at_least_five_shifts(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            '''INSERT INTO users(
                   ID, login, first_name, second_name, nick_name, status
               ) VALUES (3, '@tiny', 'Тим', 'Один', 'Тим', 0)'''
        )
        shift_date = (date.today().replace(day=1) - timedelta(days=20)).replace(day=1)
        conn.executemany(
            '''INSERT INTO shifts(
                   shift_second_name, shift_first_name, dt_shift, club,
                   dur, source, shift_login
               ) VALUES ('', '', ?, 'Марьино', 6, 'test', '@tiny')''',
            [((shift_date + timedelta(days=offset)).isoformat(),) for offset in range(4)],
        )
        conn.execute(
            '''INSERT INTO reviews(ID, who, d_rep, amount, desc)
               VALUES (3, '@tiny', ?, 1000, '')''',
            (shift_date.isoformat(),),
        )
        conn.commit()
        conn.close()

        state = records.calculate_records_state(self.db_path)

        self.assertEqual(state['stats']['@tiny']['kpi_peak'], 0)
        kpi_record = next(
            item for item in state['records'] if item['key'] == 'kpi_peak'
        )
        self.assertNotIn('@tiny', [holder['login'] for holder in kpi_record['holders']])

    def test_future_shifts_do_not_count_towards_achievements(self):
        future = (date.today() + timedelta(days=7)).isoformat()
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            '''INSERT INTO shifts(
                   shift_second_name, shift_first_name, dt_shift, club,
                   dur, source, shift_login
               ) VALUES ('', '', ?, 'Марьино', 600, 'test', '@active')''',
            (future,),
        )
        conn.commit()
        conn.close()

        state = records.calculate_records_state(self.db_path)

        self.assertEqual(state['stats']['@active']['shifts'], 12)

    def test_touring_levels_require_enough_shifts_in_each_club(self):
        shift_date = (date.today() - timedelta(days=1)).isoformat()
        clubs = tuple(records.PHYSICAL_KPI_CLUBS)
        conn = sqlite3.connect(self.db_path)
        conn.execute("DELETE FROM shifts WHERE shift_login='@active'")
        conn.executemany(
            '''INSERT INTO shifts(
                   shift_second_name, shift_first_name, dt_shift, club,
                   dur, source, shift_login
               ) VALUES ('', '', ?, ?, 60, 'test', '@active')''',
            [(shift_date, club) for club in clubs[:4]],
        )
        conn.commit()
        conn.close()

        state = records.calculate_records_state(self.db_path)
        values = state['stats']['@active']
        achievement = records.ACHIEVEMENTS_BY_KEY['clubs']
        self.assertEqual(records._achievement_level(achievement, values), 3)

        conn = sqlite3.connect(self.db_path)
        conn.executemany(
            '''INSERT INTO shifts(
                   shift_second_name, shift_first_name, dt_shift, club,
                   dur, source, shift_login
               ) VALUES ('', '', ?, ?, ?, 'test', '@active')''',
            [
                *((shift_date, club, 60) for club in clubs[:4]),
                (shift_date, clubs[4], 120),
            ],
        )
        conn.commit()
        conn.close()

        state = records.calculate_records_state(self.db_path)
        values = state['stats']['@active']
        payload = records._achievement_payload(achievement, values, 0)
        self.assertEqual(payload['level'], 4)
        self.assertEqual(payload['tier']['key'], 'diamond')
        self.assertEqual(len(payload['thresholds']), 4)

    def test_dashboard_contains_all_achievement_families(self):
        dashboard = records.build_records_dashboard(
            self.db_path, '@active',
        )
        achievements = [
            achievement
            for category in dashboard['categories']
            for achievement in category['achievements']
        ]

        self.assertEqual(len(achievements), 31)
        self.assertEqual(dashboard['summary']['total'], 31)
        self.assertIn('diamond', dashboard['summary'])
        self.assertFalse(dashboard['can_manage'])
        self.assertIsNone(dashboard['team'])
        self.assertTrue(any(item['key'] == 'created_repairs' for item in achievements))
        self.assertTrue(dashboard['archive_records'])

    def test_manager_dashboard_contains_team_and_selected_employee(self):
        dashboard = records.build_records_dashboard(
            self.db_path,
            '@archive',
            viewer_login='@active',
            can_manage=True,
        )

        self.assertEqual(dashboard['user']['login'], '@archive')
        self.assertEqual(dashboard['viewer']['login'], '@active')
        self.assertTrue(dashboard['can_manage'])
        self.assertEqual(
            {member['login'] for member in dashboard['team']},
            {'@active', '@archive'},
        )
        self.assertTrue(all(member['total'] == 31 for member in dashboard['team']))

    def test_dashboard_reuses_fresh_background_snapshot(self):
        records.refresh_records_achievements(self.db_path)

        with patch.object(
            records,
            'calculate_records_state',
            side_effect=AssertionError('fresh cache must be reused'),
        ):
            dashboard = records.build_records_dashboard(
                self.db_path, '@active',
            )

        self.assertEqual(dashboard['user']['login'], '@active')
        self.assertEqual(dashboard['summary']['total'], 31)


if __name__ == '__main__':
    unittest.main()
