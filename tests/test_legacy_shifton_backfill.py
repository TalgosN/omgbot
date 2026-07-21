import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


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

    def test_large_period_is_fetched_in_month_sized_chunks(self):
        responses = [
            {"access_token": "token", "refresh_token": "refresh"},
            [{"id": 7, "full_name": "Иванов Иван"}],
            [{"id": 1}],
            [],
            [{"id": 2}],
            [],
            [{"id": 3}],
            [],
        ]
        credentials = {
            "username": "user",
            "password": "pass",
            "client_id": "2",
            "client_secret": "secret",
        }

        with patch.object(self.backfill, "request_json", side_effect=responses) as request:
            shifts, employees = self.backfill.fetch_legacy_shifts(
                "2026-01-01", "2026-03-05", credentials
            )

        self.assertEqual(shifts, [{"id": 1}, {"id": 2}, {"id": 3}])
        self.assertEqual(employees[0]["id"], 7)
        shift_requests = [
            call for call in request.call_args_list
            if call.args[1].endswith("/shifts")
        ]
        self.assertEqual(len(shift_requests), 3)

    def test_archived_employee_is_loaded_from_deleted_employees_endpoint(self):
        responses = [
            {"access_token": "token", "refresh_token": "refresh"},
            [{"id": 7, "full_name": "Иванов Иван"}],
            [{
                "id": 100,
                "employee_id": 99,
                "planned_from": "2025-01-01 09:00:00",
                "planned_to": "2025-01-01 15:00:00",
                "location": {"title": "Марьино"},
            }],
            [{"id": 99, "full_name": "Архивный Сотрудник"}],
        ]
        credentials = {
            "username": "user",
            "password": "pass",
            "client_id": "2",
            "client_secret": "secret",
        }

        with patch.object(self.backfill, "request_json", side_effect=responses) as request:
            shifts, employees = self.backfill.fetch_legacy_shifts(
                "2025-01-01", "2025-01-01", credentials
            )

        self.assertEqual(shifts[0]["employee_id"], 99)
        self.assertEqual(employees[-1]["full_name"], "Архивный Сотрудник")
        self.assertIn("/employees/deleted", request.call_args_list[-1].args[1])

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

    def test_imported_alias_inherits_existing_shift_login(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "test.sql"
            conn = sqlite3.connect(db_path)
            conn.execute(
                "CREATE TABLE shifts (shift_second_name TEXT, shift_first_name TEXT, "
                "dt_shift TEXT, club TEXT, dur REAL, source TEXT, shift_login TEXT)"
            )
            conn.execute(
                "INSERT INTO shifts VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("Песков", "Максим", "2026-07-12", "Марьино", 6.0,
                 "legacy_shifton", "@maxon"),
            )
            conn.execute(
                "CREATE TABLE users_new (login TEXT, second_name TEXT, first_name TEXT)"
            )
            conn.execute(
                "INSERT INTO users_new VALUES (?, ?, ?)",
                ("@maxon", "Песков", "Максон"),
            )
            conn.commit()
            conn.close()

            self.backfill.apply_rows(
                db_path,
                [("Песков", "Максим", "2025-01-10", "Марьино", 6.0,
                  "legacy_shifton")],
                "2025-01-10",
                "2025-01-10",
            )

            conn = sqlite3.connect(db_path)
            login = conn.execute(
                "SELECT shift_login FROM shifts WHERE dt_shift='2025-01-10'"
            ).fetchone()[0]
            conn.close()
            self.assertEqual(login, "@maxon")


if __name__ == "__main__":
    unittest.main()
