import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path


def load_backfill_module():
    spec = importlib.util.spec_from_file_location(
        "legacy_shifton_backfill", "scripts/backfill_legacy_shifton.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LegacyShiftonBackfillTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backfill = load_backfill_module()

    def test_prepare_rows_normalizes_date_and_duration(self):
        employees = [{"id": 7, "full_name": "Иванов Иван"}]
        shifts = [{
            "employee_id": 7,
            "planned_from": "2026-07-18 09:00:00",
            "planned_to": "2026-07-18 15:30:00",
            "location": {"title": "Марьино"},
        }]

        rows, skipped = self.backfill.prepare_rows(
            shifts, employees, "2026-07-13", "2026-07-19"
        )

        self.assertEqual(skipped, 0)
        self.assertEqual(rows, [
            ("Иванов", "Иван", "2026-07-18", "Марьино", 6.5, "legacy_shifton")
        ])

    def test_apply_is_idempotent_and_preserves_omg_shift_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "test.sql"
            conn = sqlite3.connect(db_path)
            conn.execute(
                "CREATE TABLE shifts (shift_second_name TEXT, shift_first_name TEXT, "
                "dt_shift TEXT, club TEXT, dur REAL)"
            )
            conn.execute(
                "INSERT INTO shifts VALUES (?, ?, ?, ?, ?)",
                ("Петров", "Пётр", "2026-07-20", "Каширка", 6.0),
            )
            conn.commit()
            conn.close()

            rows = [("Иванов", "Иван", "2026-07-18", "Марьино", 6.5, "legacy_shifton")]
            first = self.backfill.apply_rows(
                db_path, rows, "2026-07-13", "2026-07-19"
            )
            second = self.backfill.apply_rows(
                db_path, rows, "2026-07-13", "2026-07-19"
            )

            conn = sqlite3.connect(db_path)
            saved = conn.execute(
                "SELECT shift_second_name, date(dt_shift), source FROM shifts ORDER BY dt_shift"
            ).fetchall()
            conn.close()
            self.assertEqual(first, 1)
            self.assertEqual(second, 1)
            self.assertEqual(saved, [
                ("Иванов", "2026-07-18", "legacy_shifton"),
                ("Петров", "2026-07-20", None),
            ])


if __name__ == "__main__":
    unittest.main()
