import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

import pytz
from steamtracker.db import OwnedGame, TrackerStorage
from steamtracker.jobs import start_weekly_promo


class TrackerBotManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "tracker.db"
        self.storage = TrackerStorage(self.db_path)
        self.storage.initialize()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _add_account_with_game(self, app_id: int) -> None:
        self.storage.upsert_managed_account(
            steam_id="76561198000000001",
            vanity_url="zone-1",
            club_name="Клуб",
            actor_id="owner",
            actor_name="Owner",
        )
        self.storage.record_account_scan(
            "76561198000000001",
            [OwnedGame(app_id, f"Game {app_id}", 10)],
        )

    def test_managed_game_requires_license_before_activation(self):
        self.storage.add_managed_game(
            app_id=10,
            steam_name="Test Game",
            actor_id="manager",
            actor_name="Manager",
        )

        with self.assertRaisesRegex(ValueError, "лицензия не найдена"):
            self.storage.set_game_catalog_status(
                10,
                "active",
                actor_id="manager",
                actor_name="Manager",
            )

        self._add_account_with_game(10)
        self.storage.set_game_catalog_status(
            10,
            "active",
            actor_id="manager",
            actor_name="Manager",
        )

        row = self.storage.managed_game(10)
        self.assertEqual(row["catalog_status"], "active")
        self.assertEqual(row["is_approved"], 1)
        self.assertEqual(row["owned_count"], 1)

    def test_deactivating_last_account_pauses_active_game(self):
        self.storage.add_managed_game(
            app_id=10,
            steam_name="Test Game",
            actor_id="manager",
            actor_name="Manager",
        )
        self._add_account_with_game(10)
        self.storage.set_game_catalog_status(
            10,
            "active",
            actor_id="manager",
            actor_name="Manager",
        )

        self.storage.set_account_active(
            "76561198000000001",
            False,
            actor_id="owner",
            actor_name="Owner",
        )

        row = self.storage.managed_game(10)
        self.assertEqual(row["catalog_status"], "paused")
        self.assertEqual(row["is_approved"], 0)
        actions = [row["action"] for row in self.storage.recent_audit()]
        self.assertIn("account_deactivated", actions)
        self.assertIn("game_auto_paused_no_license", actions)

    def test_settings_are_stored_in_sqlite_and_audited(self):
        self.storage.update_tracker_setting(
            "weekly_discount",
            "150 рублей",
            actor_id="owner",
            actor_name="Owner",
        )

        self.assertEqual(
            self.storage.tracker_settings()["weekly_discount"],
            "150 рублей",
        )
        audit = self.storage.recent_audit(limit=1)[0]
        self.assertEqual(audit["action"], "setting_updated")
        self.assertEqual(audit["entity_id"], "weekly_discount")

    def test_random_promotion_ignores_unlicensed_approved_game(self):
        self.storage.upsert_catalog_games(
            [
                {
                    "app_id": 10,
                    "steam_name": "Licensed",
                    "official_name": "Licensed",
                    "player_count": 1,
                    "base_description": "Описание",
                },
                {
                    "app_id": 20,
                    "steam_name": "Unlicensed",
                    "official_name": "Unlicensed",
                    "player_count": 1,
                    "base_description": "Описание",
                },
            ]
        )
        self._add_account_with_game(10)

        self.assertEqual(self.storage.random_approved_game_id(), 10)
        selection = self.storage.create_random_weekly_promotion(
            discount_text="100 рублей",
            valid_from="2026-08-03",
            valid_to="2026-08-09",
        )
        self.assertEqual(selection.app_id, 10)

    def test_missing_license_report_uses_only_active_games_and_accounts(self):
        self.storage.upsert_catalog_games(
            [
                {
                    "app_id": 10,
                    "steam_name": "Game One",
                    "official_name": "Game One",
                    "player_count": 1,
                    "base_description": "Описание",
                },
                {
                    "app_id": 20,
                    "steam_name": "Game Two",
                    "official_name": "Game Two",
                    "player_count": 1,
                    "base_description": "Описание",
                },
            ]
        )
        self.storage.add_managed_game(
            app_id=30,
            steam_name="Draft Game",
            actor_id="manager",
            actor_name="Manager",
        )
        accounts = [
            ("76561198000000001", "zone-1", "Club A"),
            ("76561198000000002", "zone-2", "Club B"),
            ("76561198000000003", "zone-3", "Club B"),
        ]
        for steam_id, zone, club in accounts:
            self.storage.upsert_managed_account(
                steam_id=steam_id,
                vanity_url=zone,
                club_name=club,
                actor_id="owner",
                actor_name="Owner",
            )
        self.storage.record_account_scan(
            "76561198000000001",
            [
                OwnedGame(10, "Game One", 0),
                OwnedGame(20, "Game Two", 0),
            ],
        )
        self.storage.record_account_scan(
            "76561198000000002",
            [OwnedGame(20, "Game Two", 0)],
        )
        self.storage.record_account_scan(
            "76561198000000003",
            [],
        )
        self.storage.set_account_active(
            "76561198000000003",
            False,
            actor_id="owner",
            actor_name="Owner",
        )

        missing_game = self.storage.missing_game_license_rows(10)
        self.assertEqual(
            [row["steam_id"] for row in missing_game],
            ["76561198000000002"],
        )
        summary = {
            row["club_name"]: dict(row)
            for row in self.storage.missing_license_club_summary()
        }
        self.assertEqual(summary["Club A"]["missing_license_count"], 0)
        self.assertEqual(summary["Club B"]["missing_license_count"], 1)
        self.assertEqual(summary["Club B"]["games_with_gaps"], 1)
        rows = self.storage.missing_license_rows_for_club("Club B")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["app_id"], 10)
        self.assertEqual(rows[0]["missing_zones"], "zone-2")

    def test_game_card_playtime_and_rank_use_all_active_accounts(self):
        self.storage.upsert_catalog_games(
            [
                {
                    "app_id": 10,
                    "steam_name": "Popular Game",
                    "official_name": "Popular Game",
                    "player_count": 1,
                    "base_description": "Описание",
                },
                {
                    "app_id": 20,
                    "steam_name": "Other Game",
                    "official_name": "Other Game",
                    "player_count": 1,
                    "base_description": "Описание",
                },
            ]
        )
        accounts = [
            ("76561198000000001", "zone-1"),
            ("76561198000000002", "zone-2"),
        ]
        for steam_id, zone in accounts:
            self.storage.upsert_managed_account(
                steam_id=steam_id,
                vanity_url=zone,
                club_name="Club",
                actor_id="owner",
                actor_name="Owner",
            )
        self.storage.record_account_scan(
            accounts[0][0],
            [
                OwnedGame(10, "Popular Game", 120),
                OwnedGame(20, "Other Game", 60),
            ],
        )
        self.storage.record_account_scan(
            accounts[1][0],
            [OwnedGame(10, "Popular Game", 30)],
        )

        popular = self.storage.managed_game(10)
        other = self.storage.managed_game(20)

        self.assertEqual(popular["total_playtime_minutes"], 150)
        self.assertEqual(popular["popularity_rank"], 1)
        self.assertEqual(other["total_playtime_minutes"], 60)
        self.assertEqual(other["popularity_rank"], 2)


class TrackerMigrationTests(unittest.TestCase):
    def test_v5_approved_games_become_managed_active_games(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "legacy.db"
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    """
                    CREATE TABLE games (
                        app_id INTEGER PRIMARY KEY,
                        steam_name TEXT NOT NULL,
                        official_name TEXT,
                        is_approved INTEGER NOT NULL DEFAULT 0,
                        player_count INTEGER,
                        base_description TEXT,
                        manager_description TEXT,
                        description_source TEXT,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO games (
                        app_id, steam_name, official_name, is_approved,
                        updated_at
                    ) VALUES (10, 'Test Game', 'Test Game', 1, '2026-01-01')
                    """
                )
                conn.execute("PRAGMA user_version = 5")
                conn.commit()
            finally:
                conn.close()

            storage = TrackerStorage(db_path)
            storage.initialize()

            with storage.connect() as conn:
                row = conn.execute(
                    """
                    SELECT managed, catalog_status
                    FROM games
                    WHERE app_id = 10
                    """
                ).fetchone()
                version = conn.execute("PRAGMA user_version").fetchone()[0]
            self.assertEqual(dict(row), {
                "managed": 1,
                "catalog_status": "active",
            })
            self.assertEqual(version, 6)
            self.assertEqual(
                storage.tracker_settings()["weekly_promo_enabled"],
                "false",
            )


class WeeklyScheduleSettingsTests(unittest.TestCase):
    def test_scheduler_uses_sqlite_day_and_time(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "tracker.db"
            storage = TrackerStorage(db_path)
            storage.initialize()
            storage.update_tracker_setting(
                "weekly_promo_enabled",
                "true",
                actor_id="owner",
                actor_name="Owner",
            )
            thread = Mock()
            with patch.dict(
                "os.environ",
                {
                    "STEAMTRACKER_DB_PATH": str(db_path),
                    "STEAMTRACKER_WEEKLY_PROMO_ENABLED": "true",
                },
                clear=False,
            ), patch(
                "steamtracker.jobs.threading.Thread",
                return_value=thread,
            ):
                before = start_weekly_promo(
                    now=pytz.timezone("Europe/Moscow").localize(
                        datetime(2026, 8, 3, 10, 29)
                    )
                )
                due = start_weekly_promo(
                    now=pytz.timezone("Europe/Moscow").localize(
                        datetime(2026, 8, 3, 10, 30)
                    )
                )

            self.assertFalse(before)
            self.assertTrue(due)
            thread.start.assert_called_once()


if __name__ == "__main__":
    unittest.main()
