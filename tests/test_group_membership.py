import types
import unittest
from unittest.mock import Mock, patch

import group_membership


class GroupMembershipTest(unittest.TestCase):
    def setUp(self):
        group_membership.clear_membership_cache()

    def tearDown(self):
        group_membership.clear_membership_cache()

    def test_active_membership_is_cached(self):
        bot = Mock()
        bot.get_chat_member.return_value = types.SimpleNamespace(status='member')

        self.assertTrue(group_membership.is_main_group_member(bot, -100, 123))
        self.assertTrue(group_membership.is_main_group_member(bot, -100, 123))
        bot.get_chat_member.assert_called_once_with(-100, 123)

    def test_left_member_and_api_failure_are_denied(self):
        left_bot = Mock()
        left_bot.get_chat_member.return_value = types.SimpleNamespace(status='left')
        failed_bot = Mock()
        failed_bot.get_chat_member.side_effect = RuntimeError('telegram unavailable')

        self.assertFalse(group_membership.is_main_group_member(left_bot, -100, 123))
        self.assertFalse(group_membership.is_main_group_member(failed_bot, -100, 456))


if __name__ == '__main__':
    unittest.main()
