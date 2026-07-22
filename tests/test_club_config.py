import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import club_config


class ClubConfigStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / 'clubs.json'
        self.backup = Path(self.temp_dir.name) / 'clubs.json.bak'
        self.old_cache = club_config._clubs
        self.old_status = dict(club_config._status)

    def tearDown(self):
        club_config._clubs = self.old_cache
        club_config._status.clear()
        club_config._status.update(self.old_status)
        self.temp_dir.cleanup()

    def patched_paths(self):
        return patch.multiple(
            club_config,
            CLUBS_PATH=self.path,
            CLUBS_BACKUP_PATH=self.backup,
        )

    def write(self, data):
        self.path.write_text(
            json.dumps(data, ensure_ascii=False),
            encoding='utf-8',
        )

    def test_save_updates_memory_immediately_and_keeps_backup(self):
        old = {'Старый': {'tag': '@old'}}
        new = {'Новый': {'tag': '@new'}}
        self.write(old)

        with self.patched_paths():
            club_config._clubs = None
            self.assertEqual(club_config.get_clubs(), old)
            club_config.save_clubs(new)

            self.assertEqual(club_config.get_clubs(), new)
            self.assertEqual(json.loads(self.path.read_text(encoding='utf-8')), new)
            self.assertEqual(json.loads(self.backup.read_text(encoding='utf-8')), old)
            self.assertFalse(self.path.with_name('clubs.json.tmp').exists())

    def test_legacy_callcenter_key_is_migrated_on_load(self):
        self.write({'КЦ': {'acc_name': 'КЦ', 'tag': '@callcenter'}})

        with self.patched_paths():
            club_config._clubs = None
            clubs = club_config.reload_clubs()

            self.assertNotIn('КЦ', clubs)
            self.assertEqual(clubs['Коллцентр']['acc_name'], 'Коллцентр')
            persisted = json.loads(self.path.read_text(encoding='utf-8'))
            self.assertIn('Коллцентр', persisted)

    def test_schedule_locations_use_separate_shift_name(self):
        club_config._clubs = {
            'Название в боте': {
                'shift_name': 'Название в OMG Shift',
                'schedule_visible': True,
                'schedule_emoji': '🎮',
            },
            'Скрытый клуб': {
                'schedule_visible': False,
            },
        }

        self.assertEqual(club_config.get_schedule_locations(), [{
            'name': 'Название в боте',
            'source_name': 'Название в OMG Shift',
            'emoji': '🎮',
        }])


if __name__ == '__main__':
    unittest.main()
