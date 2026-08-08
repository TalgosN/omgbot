import copy
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from shift_config_store import (
    CLOSE_ACTION,
    OPEN_ACTION,
    get_editor_config,
    list_versions,
    rollback_version,
    save_editor_config,
)


def clubs_fixture():
    return {
        'Клуб': {
            '_config_id': 'club_01',
            'shift_name': 'Клуб',
            'schedule_visible': True,
            'schedule_emoji': '1️⃣',
            'questions': {
                OPEN_ACTION: [[{'text': 'Открытие?', 'type': 'text'}]],
                CLOSE_ACTION: [[{'text': 'Закрытие?', 'type': 'photo'}]],
            },
            'checklists': {
                OPEN_ACTION: [['Включить свет']],
                CLOSE_ACTION: [[]],
            },
        },
        'Глобально': {
            '_config_id': 'club_02',
            'shift_name': 'Глобально',
            'schedule_visible': False,
            'schedule_emoji': '',
        },
    }


class ShiftConfigStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / 'test.sql')
        self.clubs = clubs_fixture()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _get(self):
        return copy.deepcopy(self.clubs)

    def _save(self, value, source='google'):
        self.clubs = copy.deepcopy(value)
        return copy.deepcopy(value)

    def test_save_applies_questions_and_records_history(self):
        with patch('shift_config_store.get_clubs', side_effect=self._get), patch(
            'shift_config_store.save_clubs', side_effect=self._save
        ):
            payload = get_editor_config(self.db_path)
            payload['clubs'][0]['actions']['open'][0]['questions'][0]['text'] = 'Новый вопрос'
            saved = save_editor_config(self.db_path, payload, 'manager')

            self.assertEqual(
                self.clubs['Клуб']['questions'][OPEN_ACTION][0][0]['text'],
                'Новый вопрос',
            )
            self.assertNotEqual(saved['version'], payload['version'])
            self.assertEqual(len(list_versions(self.db_path)), 2)

    def test_stale_save_is_rejected(self):
        with patch('shift_config_store.get_clubs', side_effect=self._get), patch(
            'shift_config_store.save_clubs', side_effect=self._save
        ):
            payload = get_editor_config(self.db_path)
            payload['version'] = 'old'
            with self.assertRaisesRegex(RuntimeError, 'уже изменилась'):
                save_editor_config(self.db_path, payload, 'manager')

    def test_rollback_restores_previous_snapshot(self):
        with patch('shift_config_store.get_clubs', side_effect=self._get), patch(
            'shift_config_store.save_clubs', side_effect=self._save
        ):
            original = get_editor_config(self.db_path)
            initial_id = list_versions(self.db_path)[0]['ID']
            changed = copy.deepcopy(original)
            changed['clubs'][0]['actions']['open'][0]['questions'][0]['text'] = 'Изменено'
            current = save_editor_config(self.db_path, changed, 'manager')

            restored = rollback_version(self.db_path, initial_id, current['version'], 'owner')

            self.assertEqual(
                restored['clubs'][0]['actions']['open'][0]['questions'][0]['text'],
                'Открытие?',
            )
            connection = sqlite3.connect(self.db_path)
            try:
                action = connection.execute(
                    'SELECT action FROM shift_config_versions ORDER BY ID DESC LIMIT 1'
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(action, f'rollback:{initial_id}')

    def test_empty_question_is_rejected(self):
        with patch('shift_config_store.get_clubs', side_effect=self._get), patch(
            'shift_config_store.save_clubs', side_effect=self._save
        ):
            payload = get_editor_config(self.db_path)
            payload['clubs'][0]['actions']['open'][0]['questions'][0]['text'] = ' '
            with self.assertRaisesRegex(ValueError, 'пустой вопрос'):
                save_editor_config(self.db_path, payload, 'manager')

    def test_more_than_ten_photo_questions_in_variant_is_rejected(self):
        with patch('shift_config_store.get_clubs', side_effect=self._get), patch(
            'shift_config_store.save_clubs', side_effect=self._save
        ):
            payload = get_editor_config(self.db_path)
            payload['clubs'][0]['actions']['open'][0]['questions'] = [
                {'text': f'Фото {index}', 'type': 'photo'}
                for index in range(1, 12)
            ]

            with self.assertRaisesRegex(ValueError, 'не более 10 вопросов с фото'):
                save_editor_config(self.db_path, payload, 'manager')


if __name__ == '__main__':
    unittest.main()
