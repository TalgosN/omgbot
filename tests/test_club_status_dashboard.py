import os
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import openclose


class ClubStatusDashboardTest(unittest.TestCase):
    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix='.sql')
        os.close(handle)
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            'CREATE TABLE clubs (ID INTEGER PRIMARY KEY, club TEXT, status TEXT)'
        )
        conn.executemany(
            'INSERT INTO clubs (club, status) VALUES (?, ?)',
            [('Тульская', 'Открыт'), ('Марьино', 'Закрыт')],
        )
        conn.commit()
        conn.close()
        openclose.initialize_club_status_dashboard_schema(self.db_path)
        self.chats_patch = patch.dict(
            openclose.CHATS,
            {'reports': -100123},
        )
        self.chats_patch.start()

    def tearDown(self):
        self.chats_patch.stop()
        os.remove(self.db_path)

    def test_dashboard_is_created_pinned_and_persisted(self):
        bot = Mock()
        bot.send_message.return_value = SimpleNamespace(message_id=321)

        result = openclose.refresh_club_status_dashboard(bot, self.db_path)

        self.assertTrue(result)
        text = bot.send_message.call_args.args[1]
        self.assertIn('🟢 Тульская — открыт', text)
        self.assertIn('🔴 Марьино — закрыт', text)
        bot.pin_chat_message.assert_called_once_with(
            -100123,
            321,
            disable_notification=True,
        )
        conn = sqlite3.connect(self.db_path)
        message_id = conn.execute(
            'SELECT message_id FROM club_status_dashboard WHERE id=1'
        ).fetchone()[0]
        conn.close()
        self.assertEqual(message_id, 321)

    def test_existing_dashboard_is_edited_without_new_message(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            'INSERT INTO club_status_dashboard (id, message_id) VALUES (1, 654)'
        )
        conn.commit()
        conn.close()
        bot = Mock()

        result = openclose.refresh_club_status_dashboard(bot, self.db_path)

        self.assertTrue(result)
        bot.edit_message_text.assert_called_once()
        bot.send_message.assert_not_called()
        bot.pin_chat_message.assert_called_once_with(
            -100123,
            654,
            disable_notification=True,
        )

    def test_status_change_time_is_shown(self):
        conn = sqlite3.connect(self.db_path)
        with conn:
            openclose._record_club_status_change(
                conn.cursor(),
                'Тульская',
                '2026-08-03 14:32:00',
            )
        text = openclose._club_status_dashboard_text(conn)
        conn.close()

        self.assertIn('🟢 Тульская — открыт (03.08 14:32)', text)


if __name__ == '__main__':
    unittest.main()
