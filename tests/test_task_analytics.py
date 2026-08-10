import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from task_analytics import build_task_analytics, record_task_event


class TaskAnalyticsTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / 'tasks.db'
        conn = sqlite3.connect(self.db_path)
        conn.executescript(
            '''
            CREATE TABLE tasks (
                ID INTEGER PRIMARY KEY,
                dtrep TEXT,
                type TEXT,
                club TEXT,
                title TEXT,
                status TEXT,
                dtfb TEXT
            );
            INSERT INTO tasks VALUES
                (1, '2026-08-01', 'Вопрос/жалоба/предложение', 'Марьино',
                 'Старое обращение', 'Архив', '2026-08-03'),
                (2, '2026-08-05', 'Ремонт', 'Дмитровка',
                 'Шлем', 'В работе', NULL),
                (3, '2026-08-08', 'Общее обращение', 'Марьино',
                 'Новое обращение', 'Выполнено', '2026-08-08'),
                (4, '2026-09-01', 'Улучшение бота', 'Каширка',
                 'Новая кнопка', 'На проверке', '2026-09-02');
            '''
        )
        record_task_event(
            conn, 3, 'created',
            datetime(2026, 8, 8, 10, 0, tzinfo=ZoneInfo('Europe/Moscow')),
        )
        record_task_event(
            conn, 3, 'confirmed',
            datetime(2026, 8, 8, 15, 0, tzinfo=ZoneInfo('Europe/Moscow')),
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_monthly_analytics_merge_legacy_types_and_mixed_time_precision(self):
        result = build_task_analytics(
            str(self.db_path),
            mode='month',
            month='2026-08',
            now=datetime(2026, 8, 10, 12, 0, tzinfo=ZoneInfo('Europe/Moscow')),
        )

        self.assertEqual(result['summary']['created'], 3)
        self.assertEqual(result['summary']['completed'], 2)
        self.assertEqual(result['summary']['open'], 1)
        self.assertAlmostEqual(result['summary']['completion_rate'], 2 / 3)
        self.assertEqual(result['summary']['precision'], 'day')
        self.assertEqual(result['summary']['average_seconds'], (2 * 86400 + 5 * 3600) / 2)
        self.assertEqual(result['types'][0]['label'], 'Ремонт')
        general = next(item for item in result['types'] if item['label'] == 'Общее обращение')
        self.assertEqual(general['count'], 2)
        self.assertEqual(result['oldest_open']['id'], 2)
        self.assertEqual(result['oldest_open']['age_days'], 5)

    def test_yearly_analytics_include_compact_monthly_trend(self):
        result = build_task_analytics(
            str(self.db_path),
            mode='year',
            year='2026',
            now=datetime(2026, 10, 1, tzinfo=ZoneInfo('Europe/Moscow')),
        )

        self.assertEqual(len(result['trend']), 12)
        self.assertEqual(result['trend'][7], {
            'month': 8,
            'created': 3,
            'completed': 2,
        })
        self.assertEqual(result['trend'][8], {
            'month': 9,
            'created': 1,
            'completed': 0,
        })

    def test_invalid_period_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Неизвестный период'):
            build_task_analytics(str(self.db_path), mode='week')


if __name__ == '__main__':
    unittest.main()
