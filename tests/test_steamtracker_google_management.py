import math
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from steamtracker.db import TrackerStorage
from steamtracker.management import (
    CatalogManagementService,
    parse_app_id,
)
from steamtracker.sheets import CURRENT_STATE_HEADERS, GoogleSheetsManager
from steamtracker.store import StoreMetadata


class FakeWorksheet:
    def __init__(self, headers=None, records=None):
        self.headers = list(headers or [])
        self.records = [dict(row) for row in (records or [])]
        self.updates = []
        self.clears = []

    def get_row(self, row, include_tailing_empty=False):
        return list(self.headers)

    def update_values(self, start, values, **kwargs):
        self.headers = list(values[0])
        self.records = [
            {
                header: row[index] if index < len(row) else ""
                for index, header in enumerate(self.headers)
            }
            for row in values[1:]
        ]
        self.updates.append((start, values))

    def get_all_records(self):
        return [dict(row) for row in self.records]

    def get_all_values(self, **kwargs):
        return [list(self.headers)] + [
            [row.get(header, "") for header in self.headers]
            for row in self.records
        ]

    def clear(self, **kwargs):
        self.clears.append(kwargs)


class FakeSpreadsheet:
    def __init__(self, worksheets=None):
        self.worksheets = dict(worksheets or {})
        self.added = []

    def worksheet_by_title(self, title):
        if title not in self.worksheets:
            raise LookupError(title)
        return self.worksheets[title]

    def add_worksheet(self, title, rows, cols):
        worksheet = FakeWorksheet()
        self.worksheets[title] = worksheet
        self.added.append((title, rows, cols))
        return worksheet


class FakeClient:
    def __init__(self, spreadsheet):
        self.spreadsheet = spreadsheet

    def open_by_key(self, spreadsheet_id):
        return self.spreadsheet


class GoogleSetupTests(unittest.TestCase):
    def setUp(self):
        self.games = FakeWorksheet(["name", "player_count", "description"])
        self.promo = FakeWorksheet(["Игра", "Статус"])
        self.spreadsheet = FakeSpreadsheet(
            {
                "Игры": self.games,
                "Промо-план": self.promo,
            }
        )
        self.manager = GoogleSheetsManager(
            SimpleNamespace(spreadsheet_id="sheet-id"),
            client=FakeClient(self.spreadsheet),
            worksheet_not_found=LookupError,
        )

    def test_setup_is_read_only_without_apply(self):
        result = self.manager.setup(apply=False)

        self.assertFalse(result.applied)
        self.assertEqual(self.spreadsheet.added, [])
        self.assertEqual(self.games.updates, [])
        self.assertEqual(self.promo.updates, [])

    def test_apply_preserves_existing_headers_and_creates_missing_sheets(self):
        result = self.manager.setup(apply=True)

        self.assertTrue(result.applied)
        self.assertEqual(self.games.headers[:3], [
            "name",
            "player_count",
            "description",
        ])
        self.assertIn("steam_app_id", self.games.headers)
        self.assertEqual(self.promo.headers[2], "Текст_сотрудникам")
        self.assertIn("Скидка", self.promo.headers)
        self.assertIn("Current_State", self.spreadsheet.worksheets)
        self.assertNotIn("Наличие лицензий", self.spreadsheet.worksheets)


class GoogleDataSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage = TrackerStorage(Path(self.temp_dir.name) / "tracker.db")
        self.storage.initialize()
        self.storage.upsert_catalog_games(
            [
                {
                    "app_id": 10,
                    "steam_name": "Steam Game One",
                    "official_name": "Old Game One",
                    "player_count": 2,
                    "base_description": "Старое описание",
                },
                {
                    "app_id": 20,
                    "steam_name": "Steam Game Two",
                    "official_name": "Old Game Two",
                    "player_count": 1,
                    "base_description": "Старое описание 2",
                },
            ]
        )
        with self.storage.connect() as conn:
            conn.executemany(
                """
                INSERT INTO accounts (
                    steam_id, vanity_url, club_name, active, updated_at
                ) VALUES (?, ?, ?, 1, ?)
                """,
                [
                    ("76561198000000001", "zone1", "Клуб", "2026-08-03"),
                    ("76561198000000002", "zone2", "Клуб", "2026-08-03"),
                ],
            )
        from steamtracker.db import OwnedGame

        self.storage.record_account_scan(
            "76561198000000001",
            [OwnedGame(10, "Steam Game One", 50)],
            scanned_at="2026-08-03T10:00:00+00:00",
        )
        self.storage.record_account_scan(
            "76561198000000002",
            [],
            scanned_at="2026-08-03T10:01:00+00:00",
        )
        self.storage.save_game_metadata(
            10,
            steam_name="Steam Game One",
            store_description="Новое описание Steam",
            genres=["Action"],
            categories=["Multi-player"],
            header_image="https://example.test/10.jpg",
            screenshots=[],
            is_free=False,
            source_language="ru",
        )
        self.games = FakeWorksheet(
            ["name", "player_count", "description"],
            [
                {
                    "name": "Old Game One",
                    "player_count": "2",
                    "description": "Старое описание",
                },
                {
                    "name": "Old Game Two",
                    "player_count": "1",
                    "description": float("nan"),
                },
            ],
        )
        self.current = FakeWorksheet(
            ["club_name", "nickname", "game_name"]
        )
        self.settings_sheet = FakeWorksheet(
            ["Параметр", "Значение", "Комментарий"]
        )
        self.spreadsheet = FakeSpreadsheet(
            {
                "Игры": self.games,
                "Промо-план": FakeWorksheet(["Игра", "Статус"]),
                "Current_State": self.current,
                "Steam Динамика": FakeWorksheet(),
                "Ошибки Steam Tracker": FakeWorksheet(),
                "Настройки Steam Tracker": self.settings_sheet,
            }
        )
        self.manager = GoogleSheetsManager(
            SimpleNamespace(spreadsheet_id="sheet-id"),
            client=FakeClient(self.spreadsheet),
            worksheet_not_found=LookupError,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_sync_writes_full_app_id_matrix_and_steam_catalog(self):
        preview = self.manager.sync_tracker_data(self.storage)
        self.assertFalse(preview.applied)
        self.assertEqual(preview.current_state_rows, 4)
        self.assertEqual(self.current.records, [])

        result = self.manager.sync_tracker_data(self.storage, apply=True)

        self.assertTrue(result.applied)
        self.assertEqual(self.current.headers, CURRENT_STATE_HEADERS)
        self.assertEqual(len(self.current.records), 4)
        self.assertEqual(
            {row["owned"] for row in self.current.records},
            {0, 1},
        )
        self.assertNotIn("game_name", self.current.headers)
        self.assertEqual(
            {row["steam_app_id"] for row in self.current.records},
            {10, 20},
        )
        game_one = next(
            row
            for row in self.games.records
            if row["steam_app_id"] == 10
        )
        self.assertEqual(game_one["Название_Steam"], "Steam Game One")
        self.assertEqual(
            game_one["Описание_Steam"],
            "Новое описание Steam",
        )
        self.assertEqual(game_one["description"], "Старое описание")
        game_two = next(
            row
            for row in self.games.records
            if row["steam_app_id"] == 20
        )
        self.assertEqual(game_two["description"], "")
        for worksheet in (
            self.games,
            self.current,
            self.settings_sheet,
        ):
            self.assertTrue(
                all(
                    not isinstance(value, float)
                    or math.isfinite(value)
                    for row in worksheet.records
                    for value in row.values()
                )
            )
        settings = {
            row["Параметр"]: row["Значение"]
            for row in self.settings_sheet.records
        }
        self.assertEqual(settings["weekly_discount"], "100 рублей")
        self.assertEqual(settings["weekly_promo_enabled"], "false")

    def test_settings_and_test_promotions_can_be_managed(self):
        self.manager.update_tracker_setting(
            "weekly_discount",
            "150 рублей",
        )
        settings = {
            row["Параметр"]: row["Значение"]
            for row in self.settings_sheet.records
        }
        self.assertEqual(settings["weekly_discount"], "150 рублей")

        promotion_id = self.storage.create_promotion(
            app_id=10,
            discount_text="150 рублей",
            valid_from="2026-08-03",
            valid_to="2026-08-09",
            manager_comment="Тест",
            image_url=None,
            is_test=True,
        )
        result = self.manager.sync_promotion(
            self.storage,
            promotion_id,
            apply=True,
        )
        promo_sheet = self.spreadsheet.worksheets["Промо-план"]
        self.assertTrue(result.applied)
        self.assertEqual(promo_sheet.records[0]["Тестовый"], "Да")

        removed = self.manager.remove_promotion(promotion_id)
        self.assertTrue(removed)
        self.assertEqual(promo_sheet.records, [])

        second_id = self.storage.create_promotion(
            app_id=20,
            discount_text="150 рублей",
            valid_from="2026-08-03",
            valid_to="2026-08-09",
            manager_comment="Ещё один тест",
            image_url=None,
            is_test=True,
        )
        self.manager.sync_promotion(
            self.storage,
            second_id,
            apply=True,
        )
        cleared = self.manager.clear_promotions()
        self.assertEqual(cleared, 1)
        self.assertEqual(promo_sheet.records, [])


class CatalogManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage = TrackerStorage(Path(self.temp_dir.name) / "tracker.db")
        self.storage.initialize()
        self.storage.upsert_catalog_games(
            [
                {
                    "app_id": 10,
                    "steam_name": "Game One",
                    "official_name": "Game One",
                    "player_count": 2,
                    "base_description": "Описание 1",
                },
                {
                    "app_id": 20,
                    "steam_name": "Game Two",
                    "official_name": "Game Two",
                    "player_count": 4,
                    "base_description": "Описание 2",
                },
            ]
        )
        self.store = Mock()
        self.service = CatalogManagementService(self.storage, self.store)

    def tearDown(self):
        self.temp_dir.cleanup()

    def approved_count(self):
        with self.storage.connect() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM games WHERE is_approved = 1"
            ).fetchone()[0]

    def test_manager_can_exclude_game_without_deleting_history(self):
        rows = [
            {
                "name": "Game One",
                "player_count": "2",
                "Статус": "Активна",
            },
            {
                "name": "Game Two",
                "player_count": "4",
                "Статус": "Исключена",
            },
        ]

        preview = self.service.sync(rows)
        self.assertFalse(preview.applied)
        self.assertEqual(self.approved_count(), 2)

        applied = self.service.sync(rows, apply=True)
        self.assertTrue(applied.applied)
        self.assertEqual(self.approved_count(), 1)
        with self.storage.connect() as conn:
            game_two = conn.execute(
                "SELECT COUNT(*) FROM games WHERE app_id = 20"
            ).fetchone()[0]
        self.assertEqual(game_two, 1)
        self.store.get_metadata.assert_not_called()

    def test_new_game_is_validated_by_store(self):
        self.store.get_metadata.return_value = StoreMetadata(
            app_id=30,
            name="New Steam Game",
            description="Store description",
            genres=["Action"],
            categories=["VR Only"],
            header_image="https://example.test/header.jpg",
            screenshots=[],
            is_free=False,
            source_language="en",
        )

        result = self.service.sync(
            [
                {
                    "steam_app_id": "https://store.steampowered.com/app/30/test/",
                    "name": "",
                    "player_count": "3",
                    "Статус": "Активна",
                }
            ],
            apply=True,
        )

        self.assertTrue(result.applied)
        with self.storage.connect() as conn:
            row = conn.execute(
                """
                SELECT official_name, player_count
                FROM games
                WHERE app_id = 30 AND is_approved = 1
                """
            ).fetchone()
        self.assertEqual(row["official_name"], "New Steam Game")
        self.assertEqual(row["player_count"], 3)

    def test_invalid_row_keeps_previous_catalog_unchanged(self):
        result = self.service.sync(
            [
                {
                    "name": "Game One",
                    "player_count": "",
                    "Статус": "Активна",
                }
            ],
            apply=True,
        )

        self.assertFalse(result.applied)
        self.assertTrue(result.errors)
        self.assertEqual(self.approved_count(), 2)

    def test_blank_google_cells_are_not_treated_as_nan_text(self):
        result = self.service.sync(
            [
                {
                    "steam_app_id": 10,
                    "name": "Game One",
                    "player_count": "2",
                    "description": float("nan"),
                    "Описание_менеджера": float("nan"),
                    "Статус": float("nan"),
                }
            ],
            apply=True,
        )

        self.assertTrue(result.applied)
        self.assertEqual(result.errors, [])
        with self.storage.connect() as conn:
            row = conn.execute(
                """
                SELECT base_description, manager_description
                FROM games
                WHERE app_id = 10
                """
            ).fetchone()
        self.assertEqual(row["base_description"], "Описание 1")
        self.assertIsNone(row["manager_description"])

    def test_app_id_parser_accepts_id_and_steam_url(self):
        self.assertEqual(parse_app_id("620980"), 620980)
        self.assertEqual(
            parse_app_id(
                "https://store.steampowered.com/app/620980/Beat_Saber/"
            ),
            620980,
        )


if __name__ == "__main__":
    unittest.main()
