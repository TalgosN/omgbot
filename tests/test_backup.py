import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

import backup


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.source = self.root / "source.db"
        self.backup_dir = self.root / "backups"
        with closing(sqlite3.connect(self.source)) as connection:
            connection.execute("CREATE TABLE records (value TEXT NOT NULL)")
            connection.execute("INSERT INTO records VALUES ('saved')")
            connection.commit()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_backup_is_consistent_and_removes_temporary_file(self):
        current = datetime(2026, 7, 28, 0, 0, tzinfo=UTC)

        target = backup.backup_sqlite(
            self.source,
            self.backup_dir,
            current,
        )

        with closing(sqlite3.connect(target)) as connection:
            self.assertEqual(
                connection.execute("SELECT value FROM records").fetchone()[0],
                "saved",
            )
            self.assertEqual(
                connection.execute("PRAGMA integrity_check").fetchone()[0],
                "ok",
            )
        self.assertFalse(target.with_suffix(".sqlite3.tmp").exists())

    def test_retention_removes_only_expired_backups_for_source(self):
        current = datetime(2026, 7, 28, 0, 0, tzinfo=UTC)
        self.backup_dir.mkdir()
        expired = self.backup_dir / "source-20260720T000000Z.sqlite3"
        recent = self.backup_dir / "source-20260727T000000Z.sqlite3"
        unrelated = self.backup_dir / "other-20260720T000000Z.sqlite3"
        for path in (expired, recent, unrelated):
            path.touch()
        old_timestamp = (current - timedelta(days=8)).timestamp()
        os.utime(expired, (old_timestamp, old_timestamp))
        os.utime(unrelated, (old_timestamp, old_timestamp))

        removed = backup.remove_expired_backups(
            self.source,
            self.backup_dir,
            retention_days=7,
            now=current,
        )

        self.assertEqual(removed, [expired])
        self.assertFalse(expired.exists())
        self.assertTrue(recent.exists())
        self.assertTrue(unrelated.exists())

    def test_all_sources_are_attempted_before_raising_error(self):
        missing = self.root / "missing.db"

        with self.assertRaises(backup.BackupError):
            backup.create_daily_backups(
                (missing, self.source),
                self.backup_dir,
            )

        self.assertEqual(
            len(list(self.backup_dir.glob("source-*.sqlite3"))),
            1,
        )

    def test_scheduler_notifies_owner_only_on_error(self):
        bot = mock.Mock()
        with mock.patch(
            "backup.create_daily_backups",
            side_effect=backup.BackupError("test failure"),
        ):
            result = backup.run_scheduled_backup(bot, "owner-chat")

        self.assertFalse(result)
        bot.send_message.assert_called_once()
        self.assertEqual(bot.send_message.call_args.args[0], "owner-chat")

    def test_scheduler_is_silent_on_success(self):
        bot = mock.Mock()
        with mock.patch(
            "backup.create_daily_backups",
            return_value=[Path("db/backups/omgbot.sqlite3")],
        ):
            result = backup.run_scheduled_backup(bot, "owner-chat")

        self.assertTrue(result)
        bot.send_message.assert_not_called()


if __name__ == "__main__":
    unittest.main()
