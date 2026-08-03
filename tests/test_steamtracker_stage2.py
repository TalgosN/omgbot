import json
import os
import types
import unittest
from unittest.mock import Mock, patch

from steamtracker.llm import OpenRouterGenerator
from steamtracker import admin as promo_admin
from steamtracker.admin import register_promo_admin_callbacks
from steamtracker.config import Settings
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
    def test_google_sheet_defaults_use_current_games_spreadsheet(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings.from_env()

        spreadsheet_id = "1h_pCl6tpwYAhZveVSfUVGh4awl0r3yVCv1EwIIhJBZw"
        self.assertEqual(settings.spreadsheet_id, spreadsheet_id)
        self.assertIn(spreadsheet_id, settings.catalog_url)

    def test_employee_chat_defaults_to_existing_main_group(self):
        with patch.dict(
            os.environ,
            {
                "EMPLOYEE_DELIVERY_ENABLED": "true",
                "STEAMTRACKER_EMPLOYEE_CHAT_ID": "",
                "CHAT_MAIN_GROUP": "-1001234567890",
            },
            clear=False,
        ):
            settings = Settings.from_env()

        self.assertTrue(settings.employee_delivery_enabled)
        self.assertEqual(settings.employee_chat_id, -1001234567890)

    def test_invalid_employee_chat_id_is_rejected(self):
        with patch.dict(
            os.environ,
            {
                "EMPLOYEE_DELIVERY_ENABLED": "true",
                "STEAMTRACKER_EMPLOYEE_CHAT_ID": "not-a-chat",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(ValueError, "Telegram chat ID"):
                Settings.from_env()

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
        self.assertEqual(texts.telegram.count("Test Game"), 1)
        self.assertEqual(texts.vk.count("Test Game"), 1)
        self.assertNotIn("<b>Акция:</b>", texts.telegram)
        self.assertNotIn("\nАкция:", texts.vk)
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

    def test_promo_entry_uses_unified_tracker_dashboard(self):
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
        self.assertIn("stpa:menu:0", callbacks)
        self.assertIn("stpa:catalog:all:0", callbacks)
        self.assertIn("stpa:audit:0", callbacks)
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

    def test_game_card_uses_grouped_management_actions(self):
        class Markup:
            def __init__(self, **_kwargs):
                self.keyboard = []

            def add(self, *buttons):
                for button in buttons:
                    self.keyboard.append([button])

            def row(self, *buttons):
                self.keyboard.append(list(buttons))

        class Button:
            def __init__(self, text, **kwargs):
                self.text = text
                self.callback_data = kwargs.get("callback_data")

        telegram_types = types.SimpleNamespace(
            InlineKeyboardMarkup=Markup,
            InlineKeyboardButton=Button,
        )
        storage = Mock()
        storage.managed_game.return_value = {
            "app_id": 10,
            "steam_name": "Test Game",
            "catalog_status": "active",
            "owned_count": 2,
            "account_count": 2,
            "player_count": 1,
            "last_promotion": None,
            "manager_description": None,
            "store_description": "Steam description",
            "base_description": None,
            "manager_comment": None,
            "header_image": "https://example.test/header.jpg",
            "total_playtime_minutes": 150,
            "popularity_rank": 1,
        }
        bot = Mock()
        bot.send_message.return_value = types.SimpleNamespace(message_id=31)
        bot.send_photo.return_value = types.SimpleNamespace(message_id=30)
        message = types.SimpleNamespace(chat=types.SimpleNamespace(id=100))
        promo_admin._context_messages.clear()

        with patch.object(
            promo_admin,
            "require_role",
            return_value={"status": promo_admin.ROLE_MANAGER},
        ), patch.object(
            promo_admin,
            "_telegram_types",
            return_value=telegram_types,
        ), patch.object(
            promo_admin,
            "_runtime",
            return_value=(Mock(), storage),
        ):
            promo_admin.show_game_card(message, 10, bot)

        markup = bot.send_message.call_args.kwargs["reply_markup"]
        callbacks = [
            button.callback_data
            for row in markup.keyboard
            for button in row
        ]
        self.assertIn("stpa:glicenses:10", callbacks)
        self.assertIn("stpa:geditmenu:10", callbacks)
        self.assertIn("stpa:gstatusmenu:10", callbacks)
        self.assertNotIn("stpa:geditplayers:10", callbacks)
        self.assertNotIn("stpa:gstatus:paused:10", callbacks)
        bot.send_photo.assert_called_once_with(
            100,
            photo="https://example.test/header.jpg",
        )
        body = bot.send_message.call_args.args[1]
        self.assertIn("Наиграно во всех клубах: 2,5 ч", body)
        self.assertIn("Место по популярности: 1", body)
        promo_admin._context_messages.clear()

    def test_employee_game_card_is_read_only(self):
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

        storage = Mock()
        storage.managed_game.return_value = {
            "app_id": 10,
            "steam_name": "Test Game",
            "catalog_status": "active",
            "owned_count": 2,
            "account_count": 2,
            "player_count": 1,
            "last_promotion": None,
            "manager_description": None,
            "store_description": "Steam description",
            "base_description": None,
            "manager_comment": None,
            "header_image": None,
            "total_playtime_minutes": 60,
            "popularity_rank": 1,
        }
        bot = Mock()
        bot.send_message.return_value = types.SimpleNamespace(message_id=32)
        message = types.SimpleNamespace(chat=types.SimpleNamespace(id=100))
        telegram_types = types.SimpleNamespace(
            InlineKeyboardMarkup=Markup,
            InlineKeyboardButton=Button,
        )
        promo_admin._context_messages.clear()

        with patch.object(
            promo_admin,
            "require_role",
            return_value={"status": promo_admin.ROLE_EMPLOYEE},
        ), patch.object(
            promo_admin,
            "_telegram_types",
            return_value=telegram_types,
        ), patch.object(
            promo_admin,
            "_runtime",
            return_value=(Mock(), storage),
        ):
            promo_admin.show_game_card(message, 10, bot)

        markup = bot.send_message.call_args.kwargs["reply_markup"]
        callbacks = [
            button.callback_data
            for row in markup.keyboard
            for button in row
        ]
        self.assertIn("stpa:glicenses:10", callbacks)
        self.assertNotIn("stpa:geditmenu:10", callbacks)
        self.assertNotIn("stpa:gstatusmenu:10", callbacks)
        self.assertNotIn("stpa:grefresh:10", callbacks)
        promo_admin._context_messages.clear()

    def test_employee_catalog_has_search_and_game_filters(self):
        class Markup:
            def __init__(self, **_kwargs):
                self.keyboard = []

            def add(self, *buttons):
                for button in buttons:
                    self.keyboard.append([button])

            def row(self, *buttons):
                self.keyboard.append(list(buttons))

        class Button:
            def __init__(self, text, **kwargs):
                self.text = text
                self.callback_data = kwargs.get("callback_data")

        storage = Mock()
        storage.managed_games.return_value = []
        bot = Mock()
        bot.send_message.return_value = types.SimpleNamespace(message_id=33)
        message = types.SimpleNamespace(chat=types.SimpleNamespace(id=100))
        telegram_types = types.SimpleNamespace(
            InlineKeyboardMarkup=Markup,
            InlineKeyboardButton=Button,
        )
        promo_admin._context_messages.clear()

        with patch.object(
            promo_admin,
            "require_role",
            return_value={"status": promo_admin.ROLE_EMPLOYEE},
        ), patch.object(
            promo_admin,
            "_telegram_types",
            return_value=telegram_types,
        ), patch.object(
            promo_admin,
            "_runtime",
            return_value=(Mock(), storage),
        ):
            promo_admin.show_catalog(
                message,
                bot,
                status="missing_pc",
            )

        storage.managed_games.assert_called_once_with(
            status="missing_pc",
            limit=promo_admin.PAGE_SIZE + 1,
            offset=0,
        )
        markup = bot.send_message.call_args.kwargs["reply_markup"]
        callbacks = {
            button.callback_data
            for row in markup.keyboard
            for button in row
        }
        self.assertIn("stpa:gsearch:0", callbacks)
        self.assertIn("stpa:catalog:active:0", callbacks)
        self.assertIn("stpa:catalog:single_player:0", callbacks)
        self.assertIn("stpa:catalog:multiplayer:0", callbacks)
        self.assertIn("stpa:catalog:fully_licensed:0", callbacks)
        self.assertIn("stpa:catalog:missing_pc:0", callbacks)
        self.assertNotIn("stpa:gadd:0", callbacks)
        self.assertNotIn("stpa:missingreport:0", callbacks)
        promo_admin._context_messages.clear()

    def test_employee_search_is_limited_to_active_games(self):
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

        storage = Mock()
        storage.managed_games.return_value = []
        bot = Mock()
        bot.send_message.return_value = types.SimpleNamespace(message_id=34)
        message = types.SimpleNamespace(
            chat=types.SimpleNamespace(id=100),
            text="beat",
        )
        telegram_types = types.SimpleNamespace(
            InlineKeyboardMarkup=Markup,
            InlineKeyboardButton=Button,
        )
        promo_admin._context_messages.clear()

        with patch.object(
            promo_admin,
            "require_role",
            return_value={"status": promo_admin.ROLE_EMPLOYEE},
        ), patch.object(
            promo_admin,
            "_telegram_types",
            return_value=telegram_types,
        ), patch.object(
            promo_admin,
            "_runtime",
            return_value=(Mock(), storage),
        ):
            promo_admin.show_game_search_results(message, bot)

        storage.managed_games.assert_called_once_with(
            status="active",
            search="beat",
            limit=20,
        )
        promo_admin._context_messages.clear()

    def test_employee_callback_rejects_management_action(self):
        handlers = []
        bot = Mock()

        def callback_query_handler(**_kwargs):
            def decorator(handler):
                handlers.append(handler)
                return handler

            return decorator

        bot.callback_query_handler.side_effect = callback_query_handler
        register_promo_admin_callbacks(bot)
        call = types.SimpleNamespace(
            id="callback",
            data="stpa:grefresh:10",
            message=types.SimpleNamespace(
                chat=types.SimpleNamespace(id=100),
            ),
        )

        with patch.object(
            promo_admin,
            "require_role",
            return_value={"status": promo_admin.ROLE_EMPLOYEE},
        ), patch.object(
            promo_admin,
            "_show_callback_error",
        ) as show_error:
            handlers[0](call)

        error = show_error.call_args.args[2]
        self.assertIsInstance(error, PermissionError)

    def test_manager_can_preview_and_apply_generated_game_description(self):
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
        )
        generated_text = (
            "Динамичная VR-игра с понятными правилами и насыщенным "
            "игровым процессом. Она быстро погружает гостя в виртуальный "
            "мир и дарит яркие эмоции.\n\n"
            "Кому рекомендовать: гостям, которые любят активные "
            "развлечения и хотят быстро освоиться в виртуальной реальности."
        )
        row = {
            "app_id": 10,
            "steam_name": "Test Game",
            "catalog_status": "active",
            "player_count": 1,
            "manager_description": None,
            "manager_comment": "Комментарий",
            "store_description": "Исходное описание Steam.",
            "base_description": None,
            "source_language": "ru",
            "genres_json": "[]",
            "categories_json": "[]",
        }
        storage = Mock()
        storage.managed_game.return_value = row
        generator = Mock()
        generator.generate.return_value = types.SimpleNamespace(
            text=generated_text,
        )
        settings = Mock()
        bot = Mock()
        bot.send_message.side_effect = [
            types.SimpleNamespace(message_id=40),
            types.SimpleNamespace(message_id=41),
            types.SimpleNamespace(message_id=42),
        ]
        message = types.SimpleNamespace(chat=types.SimpleNamespace(id=100))
        call = types.SimpleNamespace(
            message=message,
            from_user=types.SimpleNamespace(
                id=200,
                username="manager",
                is_bot=False,
            ),
        )
        user = {
            "status": promo_admin.ROLE_MANAGER,
            "login": "@manager",
        }
        promo_admin._context_messages.clear()
        promo_admin._game_description_previews.clear()

        with patch.object(
            promo_admin,
            "require_role",
            return_value=user,
        ), patch.object(
            promo_admin,
            "_telegram_types",
            return_value=telegram_types,
        ), patch.object(
            promo_admin,
            "_runtime",
            return_value=(settings, storage),
        ), patch.object(
            promo_admin,
            "build_manager_description_generator",
            return_value=generator,
        ), patch.object(
            promo_admin,
            "_sync_tracker_data_best_effort",
            return_value=None,
        ), patch.object(
            promo_admin,
            "show_game_card",
        ) as show_card:
            promo_admin.show_game_edit_menu(message, 10, bot)
            edit_markup = bot.send_message.call_args.kwargs["reply_markup"]
            edit_callbacks = [
                button.callback_data
                for keyboard_row in edit_markup.keyboard
                for button in keyboard_row
            ]
            self.assertIn("stpa:gdescgenerate:10", edit_callbacks)

            promo_admin.generate_game_description(
                message,
                10,
                bot,
                update=call,
            )
            preview_markup = bot.send_message.call_args.kwargs[
                "reply_markup"
            ]
            callbacks = [
                button.callback_data
                for keyboard_row in preview_markup.keyboard
                for button in keyboard_row
            ]
            self.assertIn("stpa:gdescapply:10", callbacks)
            self.assertIn("stpa:gdescgenerate:10", callbacks)
            storage.update_managed_game.assert_not_called()

            promo_admin.apply_generated_game_description(
                message,
                10,
                bot,
                update=call,
                source_message=message,
            )

        storage.update_managed_game.assert_called_once_with(
            10,
            player_count=1,
            manager_description=generated_text,
            manager_comment="Комментарий",
            actor_id="200",
            actor_name="@manager",
        )
        show_card.assert_called_once()
        self.assertNotIn(
            ("200", 10),
            promo_admin._game_description_previews,
        )
        promo_admin._context_messages.clear()
        promo_admin._game_description_previews.clear()


if __name__ == "__main__":
    unittest.main()
