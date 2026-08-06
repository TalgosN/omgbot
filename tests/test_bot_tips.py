import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from bot_tips import append_daily_bot_tip, get_daily_bot_tip, load_bot_tips


class BotTipsTest(unittest.TestCase):
    def test_approved_file_contains_44_tips(self):
        tips = load_bot_tips()

        self.assertEqual(len(tips), 44)
        self.assertTrue(all(tip.strip() for tip in tips))

    def test_tip_changes_daily_and_repeats_after_full_cycle(self):
        start = date(2026, 8, 6)
        first = get_daily_bot_tip(start)

        self.assertNotEqual(first, get_daily_bot_tip(start + timedelta(days=1)))
        self.assertEqual(first, get_daily_bot_tip(start + timedelta(days=44)))

    def test_tip_is_appended_to_schedule(self):
        text = append_daily_bot_tip('Расписание', date(2026, 8, 6))

        self.assertTrue(text.startswith('Расписание\n\n💡 А ты знал?'))

    def test_missing_tip_file_does_not_change_message(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            missing_path = Path(temp_dir) / 'missing.md'
            original = load_bot_tips
            try:
                import bot_tips
                bot_tips.load_bot_tips = lambda: original(missing_path)
                self.assertEqual(
                    append_daily_bot_tip('Расписание', date(2026, 8, 6)),
                    'Расписание',
                )
            finally:
                bot_tips.load_bot_tips = original


if __name__ == '__main__':
    unittest.main()
