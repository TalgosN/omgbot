import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from steamtracker.catalog import resolve_catalog


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.legacy_db = Path(self.temp_dir.name) / "legacy.db"
        with closing(sqlite3.connect(self.legacy_db)) as conn:
            conn.execute(
                "CREATE TABLE games (app_id INTEGER PRIMARY KEY, name TEXT)"
            )
            conn.executemany(
                "INSERT INTO games (app_id, name) VALUES (?, ?)",
                [
                    (10, "Arizona Sunshine® VR Legacy"),
                    (20, "Beat Saber"),
                ],
            )
            conn.commit()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_resolves_app_id_and_excludes_pixel_dungeon(self):
        result = resolve_catalog(
            [
                {
                    "name": "Arizona Sunshine VR Legacy",
                    "player_count": "4",
                    "description": "Зомби-шутер",
                },
                {
                    "name": "Beat Saber",
                    "player_count": "1",
                    "description": "Ритм-игра",
                },
                {
                    "name": "Pixel Dungeon VR: Prologue",
                    "player_count": "",
                    "description": "",
                },
            ],
            self.legacy_db,
        )

        self.assertEqual([game["app_id"] for game in result.games], [10, 20])
        self.assertEqual(result.excluded, ["Pixel Dungeon VR: Prologue"])
        self.assertEqual(result.unresolved, [])

    def test_reports_unresolved_game_without_inventing_app_id(self):
        result = resolve_catalog(
            [{"name": "Unknown Game", "player_count": "1"}],
            self.legacy_db,
        )

        self.assertEqual(result.games, [])
        self.assertEqual(result.unresolved, ["Unknown Game"])


if __name__ == "__main__":
    unittest.main()
