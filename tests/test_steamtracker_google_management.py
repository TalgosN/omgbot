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
from steamtracker.sheets import GoogleSheetsManager
from steamtracker.store import StoreMetadata


class FakeWorksheet:
    def __init__(self, headers=None):
        self.headers = list(headers or [])
        self.updates = []

    def get_row(self, row, include_tailing_empty=False):
        return list(self.headers)

    def update_values(self, start, values):
        self.headers = list(values[0])
        self.updates.append((start, values))

    def get_all_records(self):
        return []


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
        self.assertIn("Наличие лицензий", self.spreadsheet.worksheets)


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
