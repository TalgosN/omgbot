import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from steamtracker.db import TrackerStorage
from steamtracker.promo import (
    DryRunPublisher,
    FakeGenerator,
    PromotionWorkflow,
)
from steamtracker.reset_promos import backup_sqlite


class PromotionResetTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "tracker.db"
        self.storage = TrackerStorage(self.db_path)
        self.storage.initialize()
        self.storage.upsert_catalog_games(
            [
                {
                    "app_id": 10,
                    "steam_name": "Test Game",
                    "official_name": "Test Game",
                    "player_count": 1,
                    "base_description": "Описание",
                }
            ]
        )
        self.workflow = PromotionWorkflow(
            self.storage,
            FakeGenerator(),
            DryRunPublisher(),
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_reset_keeps_catalog_and_restarts_promotion_ids(self):
        selection = self.storage.create_random_weekly_promotion(
            discount_text="100 рублей",
            valid_from="2026-08-03",
            valid_to="2026-08-09",
        )
        self.workflow.generate(selection.promotion_id)
        self.workflow.approve_and_dispatch(
            selection.promotion_id,
            approved_by="manager",
        )
        backup = backup_sqlite(self.db_path)

        deleted = self.storage.reset_all_promotions()

        self.assertEqual(deleted["promotions"], 1)
        self.assertEqual(deleted["content_generations"], 1)
        self.assertEqual(deleted["outbox"], 3)
        self.assertEqual(deleted["game_rotation"], 1)
        self.assertEqual(
            self.storage.promotion_reset_summary(),
            {
                "promotions": 0,
                "content_generations": 0,
                "outbox": 0,
                "game_rotation": 0,
            },
        )
        self.assertEqual(self.storage.summary()["approved_games"], 1)
        new_id = self.storage.create_promotion(
            app_id=10,
            discount_text="100 рублей",
            valid_from="2026-08-10",
            valid_to="2026-08-16",
            manager_comment=None,
            image_url=None,
        )
        self.assertEqual(new_id, 1)

        with closing(sqlite3.connect(backup)) as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM promotions"
                ).fetchone()[0],
                1,
            )


if __name__ == "__main__":
    unittest.main()
