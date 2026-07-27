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
        return SimpleNamespace(
            text=text,
            id=101,
            message_id=101,
            chat=SimpleNamespace(id=-100500),
            from_user=SimpleNamespace(username="employee"),
        )

    def test_supported_hashtags(self):
        self.assertEqual(set(self.kpi.kpi_dict), {
            "#серт", "#абик", "#штраф", "#продление",
            "#инициатива", "#отзывы",
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

    def test_remote_hashtag_uses_generic_api_and_saves_applied_event(self):
        class Response:
            def __init__(self, payload):
                self.payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return self.payload

        real_connect = sqlite3.connect
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / "hashtags.sqlite")
            connect = lambda _path: real_connect(db_path)
            self.kpi._hashtag_rules_cache_until = 0
            get = unittest.mock.Mock(return_value=Response({
                "ok": True,
                "hashtags": [{
                    "hashtag": "#активация",
                    "type": "message_bonus",
                    "valueUnit": "rubles",
                }],
            }))
            post = unittest.mock.Mock(return_value=Response({"ok": True}))

            with patch.object(self.kpi.sqlite3, "connect", side_effect=connect), \
                    patch.object(self.kpi.requests, "get", get), \
                    patch.object(self.kpi.requests, "post", post):
                result = self.kpi.hash_handle(
                    self.message("#активация 125,50 вечерняя продажа")
                )

            self.assertEqual(result[0], self.kpi.KPI_REMOTE_SUCCESS)
            payload = post.call_args.kwargs["json"]
            self.assertEqual(payload["hashtag"], "#активация")
            self.assertEqual(payload["value"], "125.50")
            self.assertEqual(payload["comment"], "вечерняя продажа")
            connection = real_connect(db_path)
            row = connection.execute(
                """SELECT telegram, hashtag, value, value_unit, comment, status
                   FROM hashtag_events"""
            ).fetchone()
            connection.close()
            self.assertEqual(
                row,
                ("@employee", "#активация", 125.5, "rubles", "вечерняя продажа", "applied"),
            )

    def test_unconfigured_remote_hashtag_is_ignored_but_audited(self):
        class Response:
            def __init__(self, payload):
                self.payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return self.payload

        real_connect = sqlite3.connect
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / "hashtags.sqlite")
            connect = lambda _path: real_connect(db_path)
            self.kpi._hashtag_rules_cache_until = 0

            with patch.object(self.kpi.sqlite3, "connect", side_effect=connect), \
                    patch.object(
                        self.kpi.requests,
                        "get",
                        return_value=Response({"ok": True, "hashtags": []}),
                    ), \
                    patch.object(
                        self.kpi.requests,
                        "post",
                        return_value=Response({
                            "ok": False,
                            "error": "hashtag_not_configured",
                        }),
                    ):
                result = self.kpi.hash_handle(self.message("#неизвестный текст"))

            self.assertEqual(result[0], self.kpi.KPI_IGNORED)
            connection = real_connect(db_path)
            status = connection.execute(
                "SELECT status FROM hashtag_events"
            ).fetchone()[0]
            connection.close()
            self.assertEqual(status, "ignored")

    def test_birthday_uses_fixed_amount_from_omg_shift_rule(self):
        response = SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"ok": True},
        )
        real_connect = sqlite3.connect
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / "hashtags.sqlite")
            connect = lambda _path: real_connect(db_path)
            with patch.object(self.kpi.sqlite3, "connect", side_effect=connect), \
                    patch.object(
                        self.kpi,
                        "_get_hashtag_rule",
                        return_value={
                            "hashtag": "#др",
                            "type": "fixed_bonus",
                            "valueUnit": "rubles",
                            "amount": 650,
                        },
                    ), \
                    patch.object(self.kpi.requests, "post", return_value=response) as post:
                result = self.kpi.hash_handle(self.message("#др Анна"))

            self.assertEqual(result[0], self.kpi.KPI_REMOTE_SUCCESS)
            self.assertEqual(post.call_args.kwargs["json"]["value"], "")
            connection = real_connect(db_path)
            event = connection.execute(
                "SELECT value, value_unit, comment, status FROM hashtag_events"
            ).fetchone()
            connection.close()
            self.assertEqual(event, (650, "rubles", "Анна", "applied"))

    def test_legacy_hashtag_migration_is_idempotent_and_preserves_statuses(self):
        real_connect = sqlite3.connect
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / "hashtags.sqlite")
            connection = real_connect(db_path)
            connection.executescript(
                """
                CREATE TABLE double (
                    ID INTEGER PRIMARY KEY, who TEXT, d_rep DATE,
                    amount REAL, desc TEXT
                );
                CREATE TABLE autosim (
                    ID INTEGER PRIMARY KEY, who TEXT, d_rep DATE, amount REAL
                );
                CREATE TABLE activation (
                    ID INTEGER PRIMARY KEY, who TEXT, d_rep DATE, amount REAL
                );
                CREATE TABLE birthday (
                    ID INTEGER PRIMARY KEY, dt_rep DATE, who TEXT,
                    club TEXT, desc TEXT, status TEXT
                );
                INSERT INTO double VALUES
                    (1, '@employee', '2026-07-20', 1.5, 'вечер');
                INSERT INTO autosim VALUES
                    (1, '@employee', '2026-07-20', 50);
                INSERT INTO birthday VALUES
                    (1, '2026-07-20', '@employee', 'Марьино', '', 'Одобрено'),
                    (2, '2026-07-21', '@employee', 'Марьино', '', 'На проверке'),
                    (3, '2026-07-22', '@employee', 'Марьино', '', 'Отклонено');
                """
            )
            connection.commit()
            connection.close()
            connect = lambda _path: real_connect(db_path)

            with patch.object(self.kpi.sqlite3, "connect", side_effect=connect):
                self.kpi.initialize_hashtag_events()
                self.kpi.initialize_hashtag_events()

            connection = real_connect(db_path)
            rows = connection.execute(
                """SELECT hashtag, status, COUNT(*)
                   FROM hashtag_events
                   GROUP BY hashtag, status
                   ORDER BY hashtag, status"""
            ).fetchall()
            connection.close()

            self.assertEqual(
                rows,
                [
                    ("#автосим", "applied", 1),
                    ("#двойная", "applied", 1),
                    ("#др", "applied", 1),
                    ("#др", "pending", 1),
                    ("#др", "rejected", 1),
                ],
            )

    def test_shift_sync_does_not_open_database_when_api_fetch_fails(self):
        timestamp = SimpleNamespace(now=lambda tz=None: datetime(2026, 7, 21))
        with patch.object(self.kpi.pd, "Timestamp", timestamp, create=True), \
                patch.object(self.kpi.pd, "DateOffset", side_effect=lambda days: timedelta(days=days), create=True), \
                patch.object(self.kpi, "fetch_omg_shift_rows", side_effect=RuntimeError("API unavailable")), \
                patch.object(self.kpi.sqlite3, "connect") as connect:
            with self.assertRaises(RuntimeError):
                self.kpi.read_shifts()

        connect.assert_not_called()

    def test_shift_sync_preserves_history_from_other_sources(self):
        timestamp = SimpleNamespace(now=lambda tz=None: datetime(2026, 7, 21))
        real_connect = sqlite3.connect
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "test.sql"
            conn = real_connect(db_path)
            conn.execute(
                "CREATE TABLE shifts (shift_second_name TEXT, shift_first_name TEXT, "
                "dt_shift TEXT, club TEXT, dur REAL, source TEXT, shift_login TEXT)"
            )
            conn.executemany(
                "INSERT INTO shifts VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    ("Старый", "Сотрудник", "2026-07-01", "Клуб", 6.0, "omg_shift", "@old"),
                    ("Архив", "Shifton", "2026-07-18", "Клуб", 6.0, "legacy_shifton", "@archive"),
                    ("Без", "Источника", "2026-07-20", "Клуб", 6.0, None, "@unknown"),
                    ("Устаревшая", "Смена", "2026-07-20", "Клуб", 6.0, "omg_shift", "@stale"),
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
                ("Без", "2026-07-20", None),
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

    def test_legacy_sheet_controls_import_penalties_and_current_stream(self):
        class Worksheet:
            def __init__(self, title, values):
                self.title = title
                self.values = values

            def get_values(self, **_kwargs):
                return self.values

        employees = Worksheet(
            "employees",
            [["Алиса", "@alice"], ["-", "-"]],
        )
        main_rows = [["", "", "2026-07-01"]] + [[] for _ in range(6)]
        main_rows.append(["", "Алиса"] + [""] * 16 + ["TRUE"])
        main = Worksheet("main", main_rows)
        penalties = Worksheet(
            "Штрафы 08.10.2025",
            [
                ["Сотрудник", "Опоздание", "Форма", "", "", ""],
                ["Ник", "", "", "", "", ""],
                ["Алиса", "2", "1", "", "", ""],
                ["Бывший", "1", "", "", "", ""],
                ["", "1", "", "", "", ""],
            ],
        )

        class Spreadsheet:
            def worksheet_by_title(self, title):
                return {
                    "Сотрудники": employees,
                    "Главный": main,
                }[title]

            def worksheets(self):
                return [main, penalties]

        imported = []
        streams = []
        with patch.object(self.kpi, "initialize_kpi_calculation_schema"), \
                patch.object(
                    self.kpi,
                    "_legacy_nickname_logins",
                    return_value={"Бывший": "@former"},
                ), \
                patch.object(
                    self.kpi,
                    "import_sheet_penalty",
                    side_effect=lambda *args: imported.append(args) or True,
                ), \
                patch.object(
                    self.kpi,
                    "set_monthly_stream",
                    side_effect=lambda *args, **kwargs: streams.append((args, kwargs)),
                ):
            result = self.kpi.import_legacy_kpi_sheet_controls(Spreadsheet())

        self.assertEqual(result["penalties"], 4)
        self.assertEqual(len(result["unmatched"]), 0)
        self.assertEqual(
            [item[2] for item in imported],
            ["Опоздание", "Опоздание", "Форма", "Опоздание"],
        )
        self.assertEqual(len(streams), 1)
        self.assertEqual(streams[0][0][:3], ("@alice", "2026-07-01", True))


if __name__ == "__main__":
    unittest.main()
