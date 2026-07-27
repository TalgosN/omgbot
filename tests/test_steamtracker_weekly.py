import random
import tempfile
import unittest
from datetime import date
from pathlib import Path

from steamtracker.db import TrackerStorage
from steamtracker.promo import DryRunPublisher, FakeGenerator, PromotionWorkflow
from steamtracker.weekly import WeeklyPromotionService, week_period


class FakeSheets:
    def __init__(self):
        self.synced = []
        self.settings = {
            "weekly_discount": "100 рублей",
            "weekly_promo_enabled": "true",
        }

    def read_tracker_settings(self):
        return dict(self.settings)

    def sync_promotion(self, storage, promotion_id, *, apply=False):
        self.synced.append((promotion_id, apply))


class WeeklyPromotionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage = TrackerStorage(Path(self.temp_dir.name) / "tracker.db")
        self.storage.initialize()
        self.storage.upsert_catalog_games(
            [
                {
                    "app_id": 10,
                    "steam_name": "Licensed Game One",
                    "official_name": "Old Game One",
                    "player_count": 4,
                    "base_description": "Командная игра.",
                },
                {
                    "app_id": 20,
                    "steam_name": "Licensed Game Two",
                    "official_name": "Old Game Two",
                    "player_count": 1,
                    "base_description": "Одиночная игра.",
                },
            ]
        )
        self.sheets = FakeSheets()
        self.workflow = PromotionWorkflow(
            self.storage,
            FakeGenerator(),
            DryRunPublisher(),
        )
        self.service = WeeklyPromotionService(
            self.storage,
            self.workflow,
            self.sheets,
            rng=random.Random(1),
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_week_period_is_monday_to_sunday(self):
        self.assertEqual(
            week_period(date(2026, 8, 6)),
            (date(2026, 8, 3), date(2026, 8, 9)),
        )

    def test_weekly_run_is_idempotent_and_uses_licensed_name(self):
        first = self.service.run(reference_date=date(2026, 8, 3))
        repeated = self.service.run(reference_date=date(2026, 8, 3))

        self.assertTrue(first.created)
        self.assertTrue(first.generated)
        self.assertFalse(repeated.created)
        self.assertFalse(repeated.generated)
        self.assertEqual(first.promotion_id, repeated.promotion_id)
        promo = dict(self.storage.promotion_context(first.promotion_id))
        self.assertEqual(promo["discount_text"], "100 рублей")
        self.assertEqual(promo["valid_from"], "2026-08-03")
        self.assertEqual(promo["valid_to"], "2026-08-09")
        self.assertIn(promo["game_name"], promo["employee_text"])
        self.assertNotIn("Old Game", promo["employee_text"])
        if promo["player_count"] > 1:
            self.assertIn(
                "проверить подключение нескольких игроков",
                promo["employee_text"],
            )

    def test_approved_game_is_not_selected_again_in_same_cycle(self):
        first = self.service.run(reference_date=date(2026, 8, 3))
        self.workflow.approve_and_dispatch(
            first.promotion_id,
            approved_by="manager",
        )
        second = self.service.run(reference_date=date(2026, 8, 10))

        self.assertNotEqual(first.app_id, second.app_id)
        self.assertEqual(first.cycle_number, second.cycle_number)
        summary = self.storage.rotation_summary()
        self.assertEqual(summary["used_games"], 1)
        self.assertEqual(summary["reserved_games"], 1)

    def test_manager_can_replace_unapproved_random_game(self):
        first = self.service.run(reference_date=date(2026, 8, 3))

        replacement = self.service.replace(first.promotion_id)

        self.assertNotEqual(first.app_id, replacement.app_id)
        with self.storage.connect() as conn:
            old_status = conn.execute(
                "SELECT status FROM promotions WHERE id = ?",
                (first.promotion_id,),
            ).fetchone()["status"]
            rotation_status = conn.execute(
                """
                SELECT status
                FROM game_rotation
                WHERE promotion_id = ?
                """,
                (first.promotion_id,),
            ).fetchone()["status"]
        self.assertEqual(old_status, "cancelled")
        self.assertEqual(rotation_status, "released")

    def test_overlapping_manual_promotions_are_rejected(self):
        self.storage.create_promotion(
            app_id=10,
            discount_text="100 рублей",
            valid_from="2026-08-03",
            valid_to="2026-08-09",
            manager_comment=None,
            image_url=None,
        )

        with self.assertRaisesRegex(ValueError, "пересекается"):
            self.storage.create_promotion(
                app_id=20,
                discount_text="100 рублей",
                valid_from="2026-08-09",
                valid_to="2026-08-15",
                manager_comment=None,
                image_url=None,
            )

    def test_test_promo_does_not_block_week_or_enter_rotation(self):
        test_id = self.storage.create_promotion(
            app_id=10,
            discount_text="100 рублей",
            valid_from="2026-08-03",
            valid_to="2026-08-09",
            manager_comment="Тест",
            image_url=None,
            is_test=True,
        )
        self.workflow.generate(test_id)

        with self.assertRaisesRegex(ValueError, "Тестовое промо"):
            self.workflow.approve_and_dispatch(
                test_id,
                approved_by="manager",
            )

        real = self.service.run(reference_date=date(2026, 8, 3))
        self.assertTrue(real.created)
        self.assertNotEqual(real.promotion_id, test_id)
        current = self.storage.current_promotion("2026-08-03")
        self.assertEqual(current["id"], real.promotion_id)

        self.storage.delete_test_promotion(test_id)
        with self.assertRaisesRegex(ValueError, "не найдено"):
            self.storage.promotion_context(test_id)

    def test_existing_draft_can_be_marked_and_deleted_as_test(self):
        selection = self.service.run(reference_date=date(2026, 8, 3))

        self.storage.mark_promotion_as_test(selection.promotion_id)

        row = dict(
            self.storage.promotion_admin_row(selection.promotion_id)
        )
        self.assertEqual(row["is_test"], 1)
        self.assertEqual(row["rotation_status"], "released")
        self.assertIsNone(
            self.storage.current_promotion("2026-08-03")
        )
        self.storage.delete_test_promotion(selection.promotion_id)


if __name__ == "__main__":
    unittest.main()
