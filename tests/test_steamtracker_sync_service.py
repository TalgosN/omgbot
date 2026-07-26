import tempfile
import unittest
from pathlib import Path

from steamtracker.db import OwnedGame, TrackerStorage
from steamtracker.steam import LicenseSyncService


class FakeSteamClient:
    def __init__(self, libraries):
        self.libraries = libraries

    def get_owned_games(self, steam_id):
        value = self.libraries[steam_id]
        if isinstance(value, Exception):
            raise value
        return value


class LicenseSyncServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage = TrackerStorage(Path(self.temp_dir.name) / "tracker.db")
        self.storage.initialize()
        with self.storage.connect() as conn:
            for steam_id in ("steam-1", "steam-2"):
                conn.execute(
                    """
                    INSERT INTO accounts (
                        steam_id, vanity_url, club_name, active, updated_at
                    ) VALUES (?, ?, 'Клуб', 1, '2026-01-01')
                    """,
                    (steam_id, steam_id),
                )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_failed_account_does_not_change_its_licenses(self):
        service = LicenseSyncService(
            self.storage,
            FakeSteamClient(
                {
                    "steam-1": [OwnedGame(10, "Game", 10)],
                    "steam-2": RuntimeError("API недоступен"),
                }
            ),
        )

        summary = service.sync()

        self.assertEqual(summary.accounts_ok, 1)
        self.assertEqual(summary.accounts_failed, 1)
        self.assertEqual(summary.licenses_added, 1)
        with self.storage.connect() as conn:
            rows = conn.execute("SELECT COUNT(*) FROM account_games").fetchone()[0]
        self.assertEqual(rows, 1)


if __name__ == "__main__":
    unittest.main()
