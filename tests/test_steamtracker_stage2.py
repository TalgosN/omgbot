import json
import os
import types
import unittest
from unittest.mock import Mock, patch

from steamtracker.llm import OpenRouterGenerator
from steamtracker import admin as promo_admin
from steamtracker.admin import register_promo_admin_callbacks
from steamtracker.jobs import (
    start_catalog_sync,
    start_license_sync,
    start_store_enrichment,
    start_weekly_promo,
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
            "employee_description": "Командная VR-игра.",
            "employee_audience": "Любителям совместных приключений.",
            "social_headline": "командное приключение начинается!",
            "social_paragraphs": [
                "Проверьте, насколько слаженно действует ваша команда.",
                "Приходите за яркими эмоциями и новыми впечатлениями!",
            ],
            "social_benefits": [],
            "social_closing": "Бронируйте удобное время! 👇",
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

        self.assertIn("<b>🎮 Игра недели: Test Game</b>", texts.employee)
        self.assertIn("100 рублей", texts.telegram)
        self.assertIn("Test Game", texts.vk)
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
                "STEAMTRACKER_WEEKLY_PROMO_ENABLED": "false",
            },
            clear=False,
        ):
            self.assertFalse(start_license_sync())
            self.assertFalse(start_store_enrichment())
            self.assertFalse(start_catalog_sync())
            self.assertFalse(start_weekly_promo())

    def test_promo_admin_callbacks_are_registered_independently(self):
        bot = Mock()

        register_promo_admin_callbacks(bot)

        bot.callback_query_handler.assert_called_once()

    def test_promo_entry_uses_inline_plane_selector(self):
        class Markup:
            def __init__(self, **_kwargs):
                self.keyboard = []

            def add(self, *buttons):
                for button in buttons:
                    self.keyboard.append([button])

        class Button:
            def __init__(self, text, **kwargs):
                self.text = text
                self.callback_data = kwargs.get("callback_data")

        telegram_types = types.SimpleNamespace(
            InlineKeyboardMarkup=Markup,
            InlineKeyboardButton=Button,
            ReplyKeyboardRemove=lambda: object(),
        )
        promo_admin._context_messages.clear()
        bot = Mock()
        bot.send_message.side_effect = [
            types.SimpleNamespace(message_id=11),
            types.SimpleNamespace(message_id=12),
        ]
        message = types.SimpleNamespace(
            chat=types.SimpleNamespace(id=100),
        )
        user = {"status": promo_admin.ROLE_MANAGER}

        with patch.object(
            promo_admin,
            "require_role",
            return_value=user,
        ), patch.object(
            promo_admin,
            "_telegram_types",
            return_value=telegram_types,
        ):
            promo_admin.promotion_admin_menu(message, bot)

        selector = bot.send_message.call_args_list[-1].kwargs[
            "reply_markup"
        ]
        callbacks = [
            button.callback_data
            for row in selector.keyboard
            for button in row
        ]
        self.assertIn("stpa:plane:real", callbacks)
        self.assertIn("stpa:plane:test", callbacks)
        bot.register_next_step_handler.assert_not_called()
        bot.delete_message.assert_any_call(100, 11)
        promo_admin._context_messages.clear()

    def test_context_navigation_deletes_previous_bot_message(self):
        bot = Mock()
        bot.send_message.side_effect = [
            types.SimpleNamespace(message_id=21),
            types.SimpleNamespace(message_id=22),
        ]
        promo_admin._context_messages.clear()

        promo_admin._send_context_message(100, bot, "Первый экран")
        promo_admin._send_context_message(100, bot, "Второй экран")

        bot.delete_message.assert_called_with(100, 21)
        self.assertEqual(promo_admin._context_messages[100], {22})
        promo_admin._context_messages.clear()


if __name__ == "__main__":
    unittest.main()
