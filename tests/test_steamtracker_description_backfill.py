import json
import tempfile
import unittest
from pathlib import Path

from steamtracker.db import TrackerStorage
from steamtracker.description_backfill import (
    APPLY_CONFIRMATION,
    ManagerDescription,
    OpenRouterManagerDescriptionGenerator,
    apply_description_artifact,
    generate_description_artifact,
)


DESCRIPTION = (
    "Динамичная VR-игра с понятной целью и насыщенным игровым процессом. "
    "Она помогает быстро погрузиться в виртуальный мир и получить яркие "
    "эмоции без долгого знакомства с правилами."
)
AUDIENCE = (
    "Гостям, которые любят активные развлечения, понятные задачи и хотят "
    "быстро освоиться в виртуальной реальности."
)


class FakeDescriptionGenerator:
    model_name = "test/model"

    def __init__(self):
        self.app_ids = []

    def generate(self, facts):
        self.app_ids.append(facts["steam_app_id"])
        return ManagerDescription(DESCRIPTION, AUDIENCE)


class FakeResponse:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "model": "resolved/model",
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            self.content,
                            ensure_ascii=False,
                        )
                    }
                }
            ],
        }


class FakeSession:
    def __init__(self, content):
        self.content = content
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse(self.content)


class DescriptionBackfillTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "tracker.db"
        self.output = Path(self.temp_dir.name) / "descriptions.json"
        self.storage = TrackerStorage(self.db_path)
        self.storage.initialize()
        self.storage.upsert_catalog_games(
            [
                {
                    "app_id": 10,
                    "steam_name": "Game One",
                    "official_name": "Game One",
                    "player_count": 1,
                    "base_description": "Исходное описание первой игры.",
                },
                {
                    "app_id": 20,
                    "steam_name": "Game Two",
                    "official_name": "Game Two",
                    "player_count": 2,
                    "base_description": "Исходное описание второй игры.",
                },
            ]
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_generation_is_resumable_and_does_not_change_database(self):
        generator = FakeDescriptionGenerator()

        first = generate_description_artifact(
            self.storage,
            generator,
            self.output,
            limit=1,
        )
        second = generate_description_artifact(
            self.storage,
            generator,
            self.output,
            resume=True,
            workers=2,
        )

        self.assertEqual(first["generated"], 1)
        self.assertEqual(second["generated"], 1)
        artifact = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertEqual(len(artifact["items"]), 2)
        self.assertTrue(all(
            item["status"] == "generated"
            for item in artifact["items"]
        ))
        self.assertIsNone(
            self.storage.managed_game(10)["manager_description"]
        )
        self.assertIsNone(
            self.storage.managed_game(20)["manager_description"]
        )

    def test_apply_requires_confirmation_and_preserves_existing_text(self):
        generate_description_artifact(
            self.storage,
            FakeDescriptionGenerator(),
            self.output,
        )
        row = self.storage.managed_game(20)
        self.storage.update_managed_game(
            20,
            player_count=row["player_count"],
            manager_description="Описание уже отредактировано менеджером.",
            manager_comment=row["manager_comment"],
            actor_id="manager",
            actor_name="Manager",
        )

        with self.assertRaisesRegex(ValueError, APPLY_CONFIRMATION):
            apply_description_artifact(
                self.storage,
                self.output,
                confirmation="wrong",
            )

        result = apply_description_artifact(
            self.storage,
            self.output,
            confirmation=APPLY_CONFIRMATION,
        )

        self.assertEqual(result["applied"], 1)
        self.assertEqual(result["skipped"], 1)
        self.assertIn(
            "Кому рекомендовать:",
            self.storage.managed_game(10)["manager_description"],
        )
        self.assertEqual(
            self.storage.managed_game(20)["manager_description"],
            "Описание уже отредактировано менеджером.",
        )

    def test_openrouter_generator_uses_separate_json_prompt(self):
        session = FakeSession(
            {
                "description": DESCRIPTION + " — это важная часть опыта.",
                "audience": AUDIENCE,
            }
        )
        generator = OpenRouterManagerDescriptionGenerator(
            "secret",
            model="configured/model",
            session=session,
        )

        result = generator.generate(
            {
                "steam_app_id": 10,
                "game_name": "Game One",
                "source_description": "Source",
                "source_language": "en",
                "player_count": 1,
                "genres": ["Action"],
                "categories": ["Single-player"],
            }
        )

        self.assertEqual(
            result.description,
            DESCRIPTION + " - это важная часть опыта.",
        )
        self.assertEqual(result.audience, AUDIENCE)
        self.assertEqual(generator.model_name, "resolved/model")
        request = session.calls[0][1]
        self.assertEqual(
            request["json"]["response_format"],
            {"type": "json_object"},
        )
        self.assertEqual(request["json"]["model"], "configured/model")
        prompt = request["json"]["messages"][1]["content"]
        self.assertIn("одновременно в одном клубе", prompt)


if __name__ == "__main__":
    unittest.main()
