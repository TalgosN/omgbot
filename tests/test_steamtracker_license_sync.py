import sqlite3
import tempfile
import unittest
from pathlib import Path

from steamtracker.db import OwnedGame, TrackerStorage


class LicenseStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "tracker.db"
        self.storage = TrackerStorage(self.db_path)
        self.storage.initialize()
        with self.storage.connect() as conn:
            conn.execute(
                """
                INSERT INTO accounts (
                    steam_id, vanity_url, club_name, active, updated_at
                ) VALUES ('steam-1', 'pc-1', 'Клуб', 1, '2026-01-01')
                """
            )

    def tearDown(self):
        self.temp_dir.cleanup()

    def _scan(self, games, day, threshold=3):
        return self.storage.record_account_scan(
            "steam-1",
            games,
            scanned_at=f"{day}T10:00:00+00:00",
            snapshot_date=day,
            removal_threshold=threshold,
        )

    def test_repeated_scan_updates_current_state_without_duplicate_history(self):
        game = OwnedGame(10, "Test Game", 15)
        first = self._scan([game], "2026-01-01")
        second = self._scan(
            [OwnedGame(10, "Test Game Renamed", 20)],
            "2026-01-01",
        )

        self.assertEqual(first.added, 1)
        self.assertEqual(second.added, 0)
        with self.storage.connect() as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM account_games").fetchone()[0],
                1,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM license_events").fetchone()[0],
                1,
            )
            daily = conn.execute(
                "SELECT playtime_minutes FROM playtime_daily"
            ).fetchone()[0]
            self.assertEqual(daily, 20)
            name = conn.execute(
                "SELECT steam_name FROM games WHERE app_id = 10"
            ).fetchone()[0]
            self.assertEqual(name, "Test Game Renamed")

    def test_license_is_removed_only_after_successful_threshold(self):
        self._scan([OwnedGame(10, "Test Game", 15)], "2026-01-01")
        first_missing = self._scan([], "2026-01-02")
        second_missing = self._scan([], "2026-01-03")

        self.assertEqual(first_missing.removed, 0)
        self.assertEqual(second_missing.removed, 0)
        with self.storage.connect() as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT owned FROM account_games WHERE app_id = 10"
                ).fetchone()[0],
                1,
            )

        third_missing = self._scan([], "2026-01-04")
        self.assertEqual(third_missing.removed, 1)
        with self.storage.connect() as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT owned FROM account_games WHERE app_id = 10"
                ).fetchone()[0],
                0,
            )
            events = conn.execute(
                """
                SELECT event_type
                FROM license_events
                ORDER BY id
                """
            ).fetchall()
            self.assertEqual(
                [event[0] for event in events],
                ["added", "removed"],
            )

    def test_approved_license_matrix_contains_every_zone(self):
        self.storage.upsert_catalog_games(
            [
                {
                    "app_id": 10,
                    "steam_name": "Test Game",
                    "official_name": "Test Game",
                    "player_count": 2,
                    "base_description": "Описание",
                }
            ]
        )
        self._scan([OwnedGame(10, "Test Game", 15)], "2026-01-01")

        rows = self.storage.approved_license_matrix()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["app_id"], 10)
        self.assertEqual(rows[0]["owned"], 1)


if __name__ == "__main__":
    unittest.main()
