import importlib.util
import sqlite3
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


def load_kpi_module():
    telebot = types.ModuleType("telebot")
    telebot.__all__ = []
    constants = types.ModuleType("constants")
    constants.__all__ = ["SHIFTON_API_URL", "SHIFTON_API_TOKEN", "TEXTS"]
    constants.SHIFTON_API_URL = "http://shifton.test"
    constants.SHIFTON_API_TOKEN = "test-token"
    constants.TEXTS = {"aff": ["Готово"], "penalty_phrases": ["Штраф записан"]}
    sheets = types.ModuleType("sheets")
    sheets.__all__ = []
    pytz = types.ModuleType("pytz")
    pytz.timezone = lambda _name: timezone(timedelta(hours=3))

    modules = {
        "telebot": telebot,
        "constants": constants,
        "pygsheets": types.ModuleType("pygsheets"),
        "pandas": types.ModuleType("pandas"),
        "pytz": pytz,
        "sql_scripts": types.ModuleType("sql_scripts"),
        "sheets": sheets,
    }
    with patch.dict(sys.modules, modules):
        spec = importlib.util.spec_from_file_location("kpi_under_test", "kpi.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


class KpiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kpi = load_kpi_module()

    def message(self, text):
        return SimpleNamespace(text=text, from_user=SimpleNamespace(username="employee"))

    def test_supported_hashtags(self):
        self.assertEqual(set(self.kpi.kpi_dict), {
            "#серт", "#абик", "#штраф", "#двойная", "#продление",
            "#др", "#инициатива", "#отзывы", "#автосим", "#активация",
        })

    def test_write_data_extends_sheet_before_clearing_unused_columns(self):
        events = []
        unused_range = unittest.mock.Mock()
        unused_range.clear.side_effect = lambda: events.append("clear")
        worksheet = unittest.mock.Mock(rows=607)
        worksheet.update_values.side_effect = lambda *args, **kwargs: events.append("update")
        worksheet.get_values.return_value = unused_range
        spreadsheet = unittest.mock.Mock()
        spreadsheet.worksheet_by_title.return_value = worksheet
        client = unittest.mock.Mock()
        client.open.return_value = spreadsheet
        rows = [(f"2026-07-{index:02d}", "@employee", "KPI", index) for index in range(1, 701)]

        with patch.object(self.kpi.pygsheets, "authorize", return_value=client, create=True):
            self.kpi.write_data(rows, "KPI OMG VR", "data")

        worksheet.update_values.assert_called_once_with(
            "A2", [list(row) for row in rows], extend=True
        )
        worksheet.get_values.assert_called_once_with(
            start="E2", end="F701", returnas="range"
        )
        self.assertEqual(events, ["update", "clear"])

    def test_write_data_rejects_ragged_rows_before_google_request(self):
        authorize = unittest.mock.Mock()

        with patch.object(self.kpi.pygsheets, "authorize", authorize, create=True):
            with self.assertRaisesRegex(ValueError, "non-rectangular"):
                self.kpi.write_data([[1, 2], [3]], "KPI OMG VR", "data")

        authorize.assert_not_called()

    def test_router_is_case_insensitive_and_preserves_arguments(self):
        received = []

        def handler(message, args):
            received.append((message.text, args))
            return self.kpi.KPI_SUCCESS, "ok", ""

        with patch.object(self.kpi, "kpi_dict", {"#продление": handler}):
            result = self.kpi.hash_handle(self.message("#ПРОДЛЕНИЕ Татьяна 15:00-16:00"))

        self.assertEqual(result, (self.kpi.KPI_SUCCESS, "ok", ""))
        self.assertEqual(received, [("#ПРОДЛЕНИЕ Татьяна 15:00-16:00", "Татьяна 15:00-16:00")])

    def test_bonus_number_boundaries(self):
        invalid_cert = self.kpi.do_bonus("#серт", self.message(""), "2999 5000")
        invalid_subscription = self.kpi.do_bonus("#абик", self.message(""), "1000 5000")
        self.assertEqual(invalid_cert[0], self.kpi.KPI_INVALID)
        self.assertEqual(invalid_subscription[0], self.kpi.KPI_INVALID)

        self.kpi.Insert_bonus = lambda *args: None
        self.kpi.update_table = lambda *args: None
        valid_cert = self.kpi.do_bonus("#серт", self.message(""), "3000 5000")
        valid_subscription = self.kpi.do_bonus("#абик", self.message(""), "999 5000")
        self.assertEqual(valid_cert[0], self.kpi.KPI_SUCCESS)
        self.assertEqual(valid_subscription[0], self.kpi.KPI_SUCCESS)

    def test_double_accepts_decimal_comma(self):
        message = self.message("#двойная 1,5")
        self.kpi.requests.post = lambda *args, **kwargs: SimpleNamespace(json=lambda: {"ok": True})
        self.kpi.get_user_club_today = lambda username: "Марьино"

        connection = unittest.mock.Mock()
        connection.cursor.return_value = unittest.mock.Mock()
        with patch.object(self.kpi.sqlite3, "connect", return_value=connection):
            result = self.kpi.do_double(message, "1,5 описание")

        self.assertEqual(result[0], self.kpi.KPI_SUCCESS)
        connection.cursor.return_value.execute.assert_called_once_with(
            "INSERT INTO double (who, d_rep, amount, desc) VALUES (?, ?, ?, ?)",
            ("@employee", unittest.mock.ANY, 1.5, "описание"),
        )

    def test_simple_amount_accepts_decimal_dot_and_comma(self):
        self.kpi.requests.post = lambda *args, **kwargs: SimpleNamespace(json=lambda: {"ok": True})
        self.kpi.get_user_club_today = lambda username: "Марьино"

        for value in ("125.50", "125,50"):
            connection = unittest.mock.Mock()
            connection.cursor.return_value = unittest.mock.Mock()
            with patch.object(self.kpi.sqlite3, "connect", return_value=connection):
                result = self.kpi.do_simple_amount("#активация", self.message(""), value)

            self.assertEqual(result[0], self.kpi.KPI_SUCCESS)
            connection.cursor.return_value.execute.assert_called_once_with(
                "INSERT INTO activation (who, d_rep, amount) VALUES (?, ?, ?)",
                ("@employee", unittest.mock.ANY, 125.5),
            )

    def test_simple_amount_rejects_zero_negative_and_malformed_values(self):
        for value in ("0", "-1", "1..5", "abc"):
            result = self.kpi.do_simple_amount("#автосим", self.message(""), value)
            self.assertEqual(result[0], self.kpi.KPI_INVALID)

    def test_shifton_error_is_reported_after_local_save(self):
        self.kpi.requests.post = lambda *args, **kwargs: SimpleNamespace(
            json=lambda: {"ok": False, "error": "employee_not_found"}
        )
        self.kpi.get_user_club_today = lambda username: "Марьино"
        connection = unittest.mock.Mock()
        connection.cursor.return_value = unittest.mock.Mock()

        with patch.object(self.kpi.sqlite3, "connect", return_value=connection):
            result = self.kpi.do_simple_amount("#автосим", self.message(""), "125,5")

        self.assertEqual(result[0], self.kpi.KPI_SAVED_ERROR)
        self.assertIn("employee_not_found", result[1])

    def test_shift_sync_does_not_open_database_when_api_fetch_fails(self):
        timestamp = SimpleNamespace(now=lambda tz=None: datetime(2026, 7, 21))
        with patch.object(self.kpi.pd, "Timestamp", timestamp, create=True), \
                patch.object(self.kpi.pd, "DateOffset", side_effect=lambda days: timedelta(days=days), create=True), \
                patch.object(self.kpi, "fetch_omg_shift_rows", side_effect=RuntimeError("API unavailable")), \
                patch.object(self.kpi.sqlite3, "connect") as connect:
            with self.assertRaises(RuntimeError):
                self.kpi.read_shifts()

        connect.assert_not_called()

    def test_shift_sync_preserves_history_and_legacy_backfill(self):
        timestamp = SimpleNamespace(now=lambda tz=None: datetime(2026, 7, 21))
        real_connect = sqlite3.connect
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "test.sql"
            conn = real_connect(db_path)
            conn.execute(
                "CREATE TABLE shifts (shift_second_name TEXT, shift_first_name TEXT, "
                "dt_shift TEXT, club TEXT, dur REAL, source TEXT)"
            )
            conn.executemany(
                "INSERT INTO shifts VALUES (?, ?, ?, ?, ?, ?)",
                [
                    ("Старый", "Сотрудник", "2026-07-01", "Клуб", 6.0, "omg_shift"),
                    ("Архив", "Shifton", "2026-07-18", "Клуб", 6.0, "legacy_shifton"),
                    ("Устаревшая", "Смена", "2026-07-20", "Клуб", 6.0, "omg_shift"),
                ],
            )
            conn.commit()
            conn.close()

            fresh_rows = [["Новая", "Смена", "2026-07-20", "Клуб", 7.0, "@new"]]
            with patch.object(self.kpi.pd, "Timestamp", timestamp, create=True), \
                    patch.object(self.kpi.pd, "DateOffset", side_effect=lambda days: timedelta(days=days), create=True), \
                    patch.object(self.kpi.pd, "DataFrame", side_effect=lambda rows, columns: rows, create=True), \
                    patch.object(self.kpi, "fetch_omg_shift_rows", return_value=fresh_rows), \
                    patch.object(self.kpi.sqlite3, "connect", side_effect=lambda _path: real_connect(db_path)):
                self.kpi.read_shifts()

            conn = real_connect(db_path)
            rows = conn.execute(
                "SELECT shift_second_name, date(dt_shift), source FROM shifts ORDER BY dt_shift, source"
            ).fetchall()
            conn.close()
            self.assertEqual(rows, [
                ("Старый", "2026-07-01", "omg_shift"),
                ("Архив", "2026-07-18", "legacy_shifton"),
                ("Новая", "2026-07-20", "omg_shift"),
            ])

    def test_omg_shift_duplicate_payload_rows_are_ignored(self):
        shift = {"employee": "Иванов Иван", "start": "09:00", "end": "15:00"}
        response = unittest.mock.Mock()
        response.json.return_value = {
            "ok": True,
            "locations": [{"title": "Марьино", "shifts": [shift, shift.copy()]}],
        }
        with patch.object(self.kpi.pd, "DateOffset", side_effect=lambda days: timedelta(days=days), create=True), \
                patch.object(self.kpi.requests, "get", return_value=response):
            rows = self.kpi.fetch_omg_shift_rows(datetime(2026, 7, 14))

        self.assertEqual(len(rows), 15)
        self.assertTrue(all(row[4] == 6.0 for row in rows))


if __name__ == "__main__":
    unittest.main()
