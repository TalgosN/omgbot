import os
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import permissions


def update(user_id):
    return SimpleNamespace(from_user=SimpleNamespace(id=user_id, is_bot=False))


class PermissionsTest(unittest.TestCase):
    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix='.sql')
        os.close(handle)
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            '''CREATE TABLE users (
                   ID INTEGER PRIMARY KEY AUTOINCREMENT,
                   login TEXT, first_name TEXT, second_name TEXT, nick_name TEXT,
                   bday TEXT, phone TEXT, email TEXT, status INTEGER, chatid TEXT
               )'''
        )
        conn.executemany(
            'INSERT INTO users (login, status, chatid) VALUES (?, ?, ?)',
            [
                ('@employee', 0, '10'),
                ('@technician', 1, '15'),
                ('@manager', 2, '20'),
                ('@owner', 3, '30'),
            ],
        )
        conn.commit()
        conn.close()
        self.db_patch = patch.object(permissions, 'DB_PATH', self.db_path)
        self.db_patch.start()
        self.membership_patch = patch.object(
            permissions,
            'is_main_group_member',
            return_value=True,
        )
        self.membership_patch.start()

    def tearDown(self):
        self.membership_patch.stop()
        self.db_patch.stop()
        os.remove(self.db_path)

    def test_initialization_preserves_current_roles_and_creates_audit(self):
        permissions.initialize_permissions_schema()
        permissions.initialize_permissions_schema()
        conn = sqlite3.connect(self.db_path)
        statuses = dict(conn.execute('SELECT login, status FROM users'))
        audit_count = conn.execute('SELECT COUNT(*) FROM role_audit').fetchone()[0]
        conn.close()
        self.assertEqual(statuses, {
            '@employee': 0,
            '@technician': 1,
            '@manager': 2,
            '@owner': 3,
        })
        self.assertEqual(audit_count, 0)

    def test_permissions_use_numeric_telegram_id(self):
        permissions.initialize_permissions_schema()
        bot = Mock()
        self.assertIsNotNone(permissions.require_role(update(30), bot, permissions.ROLE_OWNER))
        self.assertIsNotNone(permissions.require_role(update(20), bot, permissions.ROLE_MANAGER))
        self.assertIsNone(permissions.require_role(update(20), bot, permissions.ROLE_OWNER))
        self.assertIsNone(permissions.require_role(update(10), bot, permissions.ROLE_TECHNICIAN))

    def test_active_user_outside_main_group_is_denied(self):
        bot = Mock()
        self.membership_patch.stop()
        self.membership_patch = patch.object(
            permissions,
            'is_main_group_member',
            return_value=False,
        )
        self.membership_patch.start()

        self.assertIsNone(
            permissions.require_role(update(30), bot, permissions.ROLE_OWNER)
        )
        bot.send_message.assert_called_once_with(
            unittest.mock.ANY,
            'Бот доступен только участникам основной рабочей группы.',
        )

    def test_main_group_message_does_not_need_remote_membership_check(self):
        bot = Mock()
        message = update(10)
        message.chat = SimpleNamespace(id=-100)
        with patch.dict(permissions.CHATS, {'main_group': '-100'}), \
                patch.object(permissions, 'is_main_group_member', return_value=False) as check:
            self.assertIsNotNone(permissions.require_role(message, bot))

        check.assert_not_called()
        bot.get_chat_member.assert_not_called()
        bot.send_message.assert_not_called()

    def test_private_and_other_group_messages_still_require_membership(self):
        for chat_id in (10, -200):
            with self.subTest(chat_id=chat_id):
                bot = Mock()
                message = update(10)
                message.chat = SimpleNamespace(id=chat_id)
                with patch.dict(permissions.CHATS, {'main_group': '-100'}), \
                        patch.object(permissions, 'is_main_group_member', return_value=False) as check:
                    self.assertIsNone(permissions.require_role(message, bot))

                check.assert_called_once_with(bot, '-100', '10')

    def test_callback_in_main_group_still_requires_membership(self):
        bot = Mock()
        callback = update(10)
        callback.data = 'test'
        callback.id = 'callback-id'
        callback.message = SimpleNamespace(chat=SimpleNamespace(id=-100))
        with patch.dict(permissions.CHATS, {'main_group': '-100'}), \
                patch.object(permissions, 'is_main_group_member', return_value=False) as check:
            self.assertIsNone(permissions.require_role(callback, bot))

        check.assert_called_once_with(bot, '-100', '10')

    def test_main_group_message_does_not_bypass_role_or_registration(self):
        for user_id, minimum in ((10, permissions.ROLE_MANAGER), (999, permissions.ROLE_EMPLOYEE)):
            with self.subTest(user_id=user_id):
                bot = Mock()
                message = update(user_id)
                message.chat = SimpleNamespace(id=-100)
                with patch.dict(permissions.CHATS, {'main_group': '-100'}):
                    self.assertIsNone(permissions.require_role(message, bot, minimum))

    def test_blocked_user_in_main_group_is_denied(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute('UPDATE users SET status=-1 WHERE chatid=?', ('10',))
        conn.commit()
        conn.close()
        message = update(10)
        message.chat = SimpleNamespace(id=-100)
        bot = Mock()
        with patch.dict(permissions.CHATS, {'main_group': '-100'}):
            self.assertIsNone(permissions.require_role(message, bot))

        bot.send_message.assert_called_once_with(
            -100, 'Доступ запрещён. Учётная запись заблокирована.',
        )

    def test_privileged_user_cannot_be_auto_bound_by_username(self):
        self.assertTrue(permissions.can_auto_bind_by_username({'status': None}))
        self.assertTrue(permissions.can_auto_bind_by_username({'status': 1}))
        self.assertFalse(permissions.can_auto_bind_by_username({'status': 2}))
        self.assertFalse(permissions.can_auto_bind_by_username({'status': 3}))

    def test_owner_employee_mode_uses_effective_role_without_changing_database(self):
        bot = Mock()

        self.assertTrue(permissions.enable_owner_employee_mode(update(30)))
        self.assertTrue(permissions.is_owner_employee_mode(update(30)))
        self.assertEqual(permissions.role_of(update(30)), permissions.ROLE_EMPLOYEE)
        self.assertIsNone(
            permissions.require_role(update(30), bot, permissions.ROLE_MANAGER)
        )

        conn = sqlite3.connect(self.db_path)
        stored_status = conn.execute(
            "SELECT status FROM users WHERE login='@owner'"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(stored_status, permissions.ROLE_OWNER)

        self.assertTrue(permissions.disable_owner_employee_mode(update(30)))
        self.assertFalse(permissions.is_owner_employee_mode(update(30)))
        self.assertEqual(permissions.role_of(update(30)), permissions.ROLE_OWNER)

    def test_employee_cannot_enable_owner_employee_mode(self):
        self.assertFalse(permissions.enable_owner_employee_mode(update(10)))
        self.assertFalse(permissions.is_owner_employee_mode(update(10)))

    def test_role_change_is_audited_and_last_owner_is_protected(self):
        permissions.initialize_permissions_schema()
        conn = sqlite3.connect(self.db_path)
        employee_id = conn.execute("SELECT ID FROM users WHERE login='@employee'").fetchone()[0]
        conn.close()
        permissions.change_role(update(30), employee_id, permissions.ROLE_TECHNICIAN)

        conn = sqlite3.connect(self.db_path)
        status = conn.execute('SELECT status FROM users WHERE ID=?', (employee_id,)).fetchone()[0]
        audit_count = conn.execute(
            "SELECT COUNT(*) FROM role_audit WHERE actor_login='@owner'"
        ).fetchone()[0]
        owner_id = conn.execute("SELECT ID FROM users WHERE login='@owner'").fetchone()[0]
        conn.close()
        self.assertEqual(status, permissions.ROLE_TECHNICIAN)
        self.assertEqual(audit_count, 1)
        with self.assertRaisesRegex(ValueError, 'последнего'):
            permissions.change_role(update(30), owner_id, permissions.ROLE_MANAGER)


if __name__ == '__main__':
    unittest.main()
