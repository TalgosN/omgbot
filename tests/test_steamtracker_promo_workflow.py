import json
import sqlite3
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
            valid_from="2026-07-27",
            valid_to="2026-08-02",
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
        self.assertIn(
            "<b>Количество игроков:</b> до 4",
            texts.employee,
        )
        self.assertIn(
            "<b>Акция:</b> скидка 100 рублей",
            texts.employee,
        )
        self.assertIn(
            "<b>Период:</b> 27 июля - 2 августа",
            texts.employee,
        )
        self.assertIn(
            "На игру действует <b>скидка 100 рублей</b>. "
            "Предложение актуально с 27 июля по 2 августа.",
            texts.telegram,
        )
        self.assertIn(
            "На игру действует скидка 100 рублей. "
            "Предложение актуально с 27 июля по 2 августа.",
            texts.vk,
        )
        self.assertNotIn("<b>Акция:</b>", texts.telegram)
        self.assertNotIn("\nАкция:", texts.vk)

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

    def test_approved_promotion_cannot_be_changed_or_cancelled(self):
        promotion_id = self.storage.create_promotion(
            app_id=10,
            discount_text="100 рублей",
            valid_from="2026-08-01",
            valid_to="2026-08-07",
            manager_comment=None,
            image_url=None,
        )
        self.workflow.generate(promotion_id)
        self.workflow.approve_and_dispatch(
            promotion_id,
            approved_by="manager",
        )

        with self.assertRaisesRegex(ValueError, "Тексты можно менять"):
            self.workflow.regenerate(promotion_id, section="all")
        with self.assertRaisesRegex(ValueError, "Нельзя изменить статус"):
            self.storage.set_promotion_status(
                promotion_id,
                "cancelled",
            )

    def test_only_claimant_can_take_and_approve_promotion(self):
        promotion_id = self.storage.create_promotion(
            app_id=10,
            discount_text="100 рублей",
            valid_from="2026-08-01",
            valid_to="2026-08-07",
            manager_comment=None,
            image_url=None,
        )
        self.workflow.generate(promotion_id)

        self.storage.claim_promotion(
            promotion_id,
            claimed_by="manager-1",
            claimed_name="Первый",
        )
        with self.assertRaisesRegex(ValueError, "у Первый"):
            self.storage.claim_promotion(
                promotion_id,
                claimed_by="manager-2",
                claimed_name="Второй",
            )
        with self.assertRaisesRegex(ValueError, "другим сотрудником"):
            self.workflow.approve_and_dispatch(
                promotion_id,
                approved_by="manager-2",
            )

        self.storage.claim_promotion(
            promotion_id,
            claimed_by="manager-2",
            claimed_name="Второй",
            force=True,
        )
        self.workflow.approve_and_dispatch(
            promotion_id,
            approved_by="manager-2",
        )
        promotion = dict(self.storage.promotion_admin_row(promotion_id))
        self.assertEqual(promotion["status"], "approved")
        self.assertEqual(promotion["claimed_name"], "Второй")

    def test_existing_database_gets_claim_columns(self):
        legacy_path = Path(self.temp_dir.name) / "legacy-v4.db"
        conn = sqlite3.connect(legacy_path)
        try:
            conn.execute(
                """
                CREATE TABLE promotions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    app_id INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'draft',
                    discount_text TEXT NOT NULL,
                    valid_from TEXT,
                    valid_to TEXT,
                    manager_comment TEXT,
                    employee_text TEXT,
                    telegram_text TEXT,
                    vk_text TEXT,
                    image_url TEXT,
                    approved_by TEXT,
                    approved_at TEXT,
                    is_test INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

        storage = TrackerStorage(legacy_path)
        storage.initialize()

        with storage.connect() as conn:
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(promotions)")
            }
        self.assertTrue(
            {"claimed_by", "claimed_name", "claimed_at"} <= columns
        )


if __name__ == "__main__":
    unittest.main()
