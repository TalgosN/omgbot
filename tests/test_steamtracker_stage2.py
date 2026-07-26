import json
import os
import unittest
from unittest.mock import Mock, patch

from steamtracker.llm import OpenRouterGenerator
from steamtracker.jobs import (
    start_catalog_sync,
    start_license_sync,
    start_store_enrichment,
)
from steamtracker.store import SteamStoreClient
from steamtracker.telegram import register_steamtracker_handlers


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class StageTwoTests(unittest.TestCase):
    def test_store_client_falls_back_to_english(self):
        session = Mock()
        session.get.side_effect = [
            FakeResponse({"10": {"success": False}}),
            FakeResponse(
                {
                    "10": {
                        "success": True,
                        "data": {
                            "short_description": "English description",
                            "genres": [{"description": "Action"}],
                            "categories": [{"description": "VR Only"}],
                            "header_image": "https://example.test/header.jpg",
                            "screenshots": [
                                {"path_full": "https://example.test/1.jpg"}
                            ],
                            "is_free": False,
                        },
                    }
                }
            ),
        ]

        metadata = SteamStoreClient(session=session).get_metadata(10)

        self.assertEqual(metadata.source_language, "en")
        self.assertEqual(metadata.genres, ["Action"])
        self.assertEqual(metadata.categories, ["VR Only"])
        self.assertEqual(session.get.call_count, 2)

    def test_openrouter_uses_json_mode_and_validates_discount(self):
        result = {
            "employee": "Сотрудникам: скидка 100 рублей",
            "telegram": "Анонс: скидка 100 рублей",
            "vk": "Подробный анонс: скидка 100 рублей",
        }
        session = Mock()
        session.post.return_value = FakeResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                result,
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }
        )
        generator = OpenRouterGenerator(
            "test-key",
            model="test/model",
            session=session,
        )

        texts = generator.generate(
            {
                "game_name": "Test Game",
                "app_id": 10,
                "player_count": 4,
                "base_description": "Описание",
                "genres_json": '["Экшен"]',
                "categories_json": '["Кооператив"]',
                "discount_text": "100 рублей",
                "valid_from": "2026-08-01",
                "valid_to": "2026-08-07",
                "manager_comment": None,
            }
        )

        self.assertEqual(texts.employee, result["employee"])
        request_body = session.post.call_args.kwargs["json"]
        self.assertEqual(
            request_body["response_format"],
            {"type": "json_object"},
        )
        self.assertEqual(request_body["model"], "test/model")

    def test_telegram_handlers_are_not_registered_by_default(self):
        bot = Mock()
        with patch.dict(
            os.environ,
            {"STEAMTRACKER_TELEGRAM_APPROVAL_ENABLED": "false"},
            clear=False,
        ):
            enabled = register_steamtracker_handlers(bot)

        self.assertFalse(enabled)
        bot.message_handler.assert_not_called()
        bot.callback_query_handler.assert_not_called()

    def test_background_jobs_are_disabled_by_default(self):
        with patch.dict(
            os.environ,
            {
                "STEAMTRACKER_SYNC_ENABLED": "false",
                "STEAMTRACKER_STORE_ENRICHMENT_ENABLED": "false",
                "STEAMTRACKER_CATALOG_SYNC_ENABLED": "false",
            },
            clear=False,
        ):
            self.assertFalse(start_license_sync())
            self.assertFalse(start_store_enrichment())
            self.assertFalse(start_catalog_sync())


if __name__ == "__main__":
    unittest.main()
