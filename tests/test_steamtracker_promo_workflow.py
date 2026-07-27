import json
import tempfile
import unittest
from pathlib import Path

from steamtracker.db import TrackerStorage
from steamtracker.promo import DryRunPublisher, FakeGenerator, PromotionWorkflow


class PromotionWorkflowTests(unittest.TestCase):
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
                    "player_count": 4,
                    "base_description": "Командная игра для VR.",
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

    def test_generates_three_texts_and_dispatches_only_to_dry_run(self):
        promotion_id = self.storage.create_promotion(
            app_id=10,
            discount_text="100 рублей",
            valid_from="2026-08-01",
            valid_to="2026-08-07",
            manager_comment="Проверить кооперативный режим",
            image_url="https://example.test/header.jpg",
        )

        texts = self.workflow.generate(promotion_id)
        self.assertNotEqual(texts.employee, texts.telegram)
        self.assertNotEqual(texts.telegram, texts.vk)
        for text in texts.__dict__.values():
            self.assertIn("100 рублей", text)
            self.assertIn("Test Game", text)
        self.assertIn(
            "<b>Что должен сделать администратор:</b>",
            texts.employee,
        )
        self.assertIn(
            "проверить и выучить управление",
            texts.employee,
        )
        self.assertIn(
            "проверить подключение нескольких игроков",
            texts.employee,
        )
        self.assertIn("<b>🎮", texts.telegram)
        self.assertNotIn("<b>", texts.vk)

        self.workflow.approve_and_dispatch(
            promotion_id,
            approved_by="manager",
        )
        with self.storage.connect() as conn:
            rows = conn.execute(
                """
                SELECT channel, payload_json, status
                FROM outbox
                ORDER BY channel
                """
            ).fetchall()
        self.assertEqual(len(rows), 3)
        self.assertEqual(
            {row["channel"] for row in rows},
            {"employees", "telegram", "vk"},
        )
        self.assertTrue(
            all(row["status"] == "ready_dry_run" for row in rows)
        )
        self.assertTrue(
            all(json.loads(row["payload_json"])["text"] for row in rows)
        )

        self.workflow.approve_and_dispatch(
            promotion_id,
            approved_by="manager",
        )
        with self.storage.connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]
            generations = conn.execute(
                "SELECT COUNT(*) FROM content_generations"
            ).fetchone()[0]
        self.assertEqual(count, 3)
        self.assertEqual(generations, 1)


if __name__ == "__main__":
    unittest.main()
