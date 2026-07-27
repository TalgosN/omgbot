"""Безопасная подготовка, чтение и обновление листов Steam Tracker."""

import json
import math
from dataclasses import dataclass
from typing import Iterable

from .catalog import EXCLUDED_GAMES, normalize_name
from .config import Settings
from .db import TrackerStorage


CURRENT_STATE_HEADERS = [
    "steam_app_id",
    "steam_id",
    "nickname",
    "club_name",
    "owned",
    "playtime_minutes",
    "recorded_at",
]

DEFAULT_TRACKER_SETTINGS = [
    {
        "Параметр": "weekly_discount",
        "Значение": "100 рублей",
        "Комментарий": "Точное значение скидки для игры недели",
    },
    {
        "Параметр": "generation_day",
        "Значение": "monday",
        "Комментарий": "День автоматического выбора игры",
    },
    {
        "Параметр": "generation_time",
        "Значение": "10:30",
        "Комментарий": "Время по часовому поясу Steam Tracker",
    },
    {
        "Параметр": "timezone",
        "Значение": "Europe/Moscow",
        "Комментарий": "Часовой пояс автоматического задания",
    },
    {
        "Параметр": "weekly_promo_enabled",
        "Значение": "false",
        "Комментарий": "Включить после проверки полного dry-run процесса",
    },
]

SHEET_SCHEMAS: dict[str, list[str]] = {
    "Игры": [
        "name",
        "player_count",
        "description",
        "steam_app_id",
        "Статус",
        "Комментарий_менеджера",
        "Описание_менеджера",
        "Название_Steam",
        "Описание_Steam",
        "Язык_описания",
        "Жанры_Steam",
        "Категории_Steam",
        "Изображение_URL",
        "Обновлено_Steam",
        "Наличие_лицензий",
        "Последнее_промо",
        "Цикл_ротации",
        "Проверено",
        "Ошибка",
    ],
    "Промо-план": [
        "Игра",
        "Статус",
        "Текст_сотрудникам",
        "ID",
        "Тестовый",
        "steam_app_id",
        "Скидка",
        "Акция_с",
        "Акция_по",
        "Комментарий_менеджера",
        "Текст_TG",
        "Текст_VK",
        "Изображение_URL",
        "Ответственный",
        "Взято_в_работу",
        "Согласовал",
        "Согласовано_дата",
        "Цикл_ротации",
        "Ошибка",
    ],
    "Current_State": CURRENT_STATE_HEADERS,
    "Steam Динамика": [
        "Дата",
        "steam_app_id",
        "Клуб",
        "SteamID",
        "Игровое_время_минут",
        "Изменение_минут",
    ],
    "Ошибки Steam Tracker": [
        "Дата",
        "Источник",
        "steam_app_id",
        "Игра",
        "Ошибка",
        "Статус",
    ],
    "Настройки Steam Tracker": [
        "Параметр",
        "Значение",
        "Комментарий",
    ],
}


@dataclass(frozen=True)
class SheetSetupAction:
    sheet: str
    action: str
    details: str


@dataclass(frozen=True)
class SheetSetupResult:
    applied: bool
    actions: list[SheetSetupAction]


@dataclass(frozen=True)
class SheetDataSyncResult:
    applied: bool
    current_state_rows: int
    game_rows: int
    settings_rows: int
    unmatched_game_rows: int


@dataclass(frozen=True)
class PromotionSheetSyncResult:
    applied: bool
    promotion_id: int
    action: str


def _column_label(index: int) -> str:
    value = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        value = chr(65 + remainder) + value
    return value


def _json_text(value: object) -> str:
    if not value:
        return ""
    if isinstance(value, list):
        items = value
    else:
        try:
            items = json.loads(str(value))
        except (TypeError, json.JSONDecodeError):
            return str(value)
    return ", ".join(str(item) for item in items)


def _sheet_value(value: object):
    if value is None:
        return ""
    if isinstance(value, float) and not math.isfinite(value):
        return ""
    return value


def _parse_app_id(value: object) -> int | None:
    text = str(_sheet_value(value) or "").strip()
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


class GoogleSheetsManager:
    def __init__(
        self,
        settings: Settings,
        *,
        client=None,
        worksheet_not_found: type[Exception]
        | tuple[type[Exception], ...]
        | None = None,
    ):
        self.settings = settings
        if client is None:
            import pygsheets

            client = pygsheets.authorize(
                service_file=str(settings.google_service_account_file)
            )
            worksheet_not_found = pygsheets.WorksheetNotFound
        self.client = client
        self.worksheet_not_found = worksheet_not_found or LookupError

    def open(self):
        return self.client.open_by_key(self.settings.spreadsheet_id)

    @staticmethod
    def _current_headers(
        worksheet,
        title: str,
    ) -> tuple[list[str], bool]:
        headers = [
            str(value).strip()
            for value in worksheet.get_row(
                1,
                include_tailing_empty=False,
            )
        ]
        filled_blank_header = False
        if title == "Промо-план":
            while len(headers) < 3:
                headers.append("")
            if not headers[2]:
                headers[2] = "Текст_сотрудникам"
                filled_blank_header = True
        return headers, filled_blank_header

    @staticmethod
    def _replace_table(
        worksheet,
        headers: list[str],
        records: Iterable[dict],
    ) -> None:
        previous = worksheet.get_all_values(
            include_tailing_empty=False,
            include_tailing_empty_rows=False,
        )
        values = [headers] + [
            [
                _sheet_value(record.get(header, ""))
                for header in headers
            ]
            for record in records
        ]
        worksheet.update_values(
            "A1",
            values,
            extend=True,
            parse=False,
        )

        previous_rows = len(previous)
        previous_cols = max((len(row) for row in previous), default=0)
        if previous_rows > len(values):
            worksheet.clear(
                start=f"A{len(values) + 1}",
                end=(
                    f"{_column_label(max(previous_cols, len(headers)))}"
                    f"{previous_rows}"
                ),
                fields="userEnteredValue",
            )
        if previous_cols > len(headers):
            worksheet.clear(
                start=f"{_column_label(len(headers) + 1)}1",
                end=f"{_column_label(previous_cols)}{max(previous_rows, len(values))}",
                fields="userEnteredValue",
            )

    def setup(self, *, apply: bool = False) -> SheetSetupResult:
        spreadsheet = self.open()
        actions: list[SheetSetupAction] = []

        for title, required_headers in SHEET_SCHEMAS.items():
            try:
                worksheet = spreadsheet.worksheet_by_title(title)
                exists = True
            except self.worksheet_not_found:
                worksheet = None
                exists = False

            if not exists:
                actions.append(
                    SheetSetupAction(
                        sheet=title,
                        action="create_sheet",
                        details=f"{len(required_headers)} колонок",
                    )
                )
                if apply:
                    worksheet = spreadsheet.add_worksheet(
                        title,
                        rows=5000 if title == "Current_State" else 1000,
                        cols=max(20, len(required_headers)),
                    )
                    worksheet.update_values("A1", [required_headers])
                continue

            current_headers, filled_blank_header = self._current_headers(
                worksheet,
                title,
            )
            missing = [
                header
                for header in required_headers
                if header not in current_headers
            ]
            if missing or filled_blank_header:
                merged = current_headers + [
                    header for header in missing if header
                ]
                actions.append(
                    SheetSetupAction(
                        sheet=title,
                        action="extend_headers",
                        details=", ".join(missing)
                        if missing
                        else "заголовок колонки C",
                    )
                )
                if apply:
                    worksheet.update_values("A1", [merged])
            else:
                actions.append(
                    SheetSetupAction(
                        sheet=title,
                        action="unchanged",
                        details="структура актуальна",
                    )
                )

        return SheetSetupResult(applied=apply, actions=actions)

    def read_catalog_rows(self) -> list[dict]:
        spreadsheet = self.open()
        worksheet = spreadsheet.worksheet_by_title("Игры")
        return worksheet.get_all_records()

    def read_tracker_settings(self) -> dict[str, str]:
        spreadsheet = self.open()
        worksheet = spreadsheet.worksheet_by_title(
            "Настройки Steam Tracker"
        )
        result: dict[str, str] = {}
        for row in worksheet.get_all_records():
            key = str(
                _sheet_value(row.get("Параметр")) or ""
            ).strip()
            if key:
                result[key] = str(
                    _sheet_value(row.get("Значение")) or ""
                ).strip()
        return result

    def update_tracker_setting(self, key: str, value: str) -> None:
        allowed = {
            row["Параметр"]
            for row in DEFAULT_TRACKER_SETTINGS
        }
        if key not in allowed:
            raise ValueError(f"Неизвестная настройка Steam Tracker: {key}")
        value = str(value).strip()
        if not value:
            raise ValueError("Значение настройки не может быть пустым")

        spreadsheet = self.open()
        worksheet = spreadsheet.worksheet_by_title(
            "Настройки Steam Tracker"
        )
        records = self._merge_settings_records(
            worksheet.get_all_records()
        )
        for record in records:
            if str(
                _sheet_value(record.get("Параметр")) or ""
            ).strip() == key:
                record["Значение"] = value
                break
        self._replace_table(
            worksheet,
            SHEET_SCHEMAS["Настройки Steam Tracker"],
            records,
        )

    def sync_tracker_data(
        self,
        storage: TrackerStorage,
        *,
        apply: bool = False,
    ) -> SheetDataSyncResult:
        spreadsheet = self.open()
        games_sheet = spreadsheet.worksheet_by_title("Игры")
        current_sheet = spreadsheet.worksheet_by_title("Current_State")
        settings_sheet = spreadsheet.worksheet_by_title(
            "Настройки Steam Tracker"
        )

        database_games = [
            dict(row) for row in storage.approved_game_sheet_rows()
        ]
        existing_game_rows = games_sheet.get_all_records()
        game_records, unmatched = self._merge_game_records(
            existing_game_rows,
            database_games,
        )
        current_records = [
            {
                "steam_app_id": row["app_id"],
                "steam_id": str(row["steam_id"]),
                "nickname": row["nickname"] or "",
                "club_name": row["club_name"] or "",
                "owned": int(row["owned"]),
                "playtime_minutes": int(row["playtime_minutes"]),
                "recorded_at": row["recorded_at"] or "",
            }
            for row in storage.current_state_matrix()
        ]
        settings_records = self._merge_settings_records(
            settings_sheet.get_all_records()
        )

        if apply:
            game_headers, _ = self._current_headers(games_sheet, "Игры")
            for header in SHEET_SCHEMAS["Игры"]:
                if header not in game_headers:
                    game_headers.append(header)
            self._replace_table(games_sheet, game_headers, game_records)
            self._replace_table(
                current_sheet,
                CURRENT_STATE_HEADERS,
                current_records,
            )
            self._replace_table(
                settings_sheet,
                SHEET_SCHEMAS["Настройки Steam Tracker"],
                settings_records,
            )

        return SheetDataSyncResult(
            applied=apply,
            current_state_rows=len(current_records),
            game_rows=len(game_records),
            settings_rows=len(settings_records),
            unmatched_game_rows=unmatched,
        )

    @staticmethod
    def _merge_settings_records(existing: list[dict]) -> list[dict]:
        records = [dict(row) for row in existing]
        positions = {
            str(
                _sheet_value(row.get("Параметр")) or ""
            ).strip(): index
            for index, row in enumerate(records)
            if str(
                _sheet_value(row.get("Параметр")) or ""
            ).strip()
        }
        for default in DEFAULT_TRACKER_SETTINGS:
            key = default["Параметр"]
            index = positions.get(key)
            if index is None:
                positions[key] = len(records)
                records.append(dict(default))
                continue
            if not str(
                _sheet_value(records[index].get("Значение")) or ""
            ).strip():
                records[index]["Значение"] = default["Значение"]
            if not str(
                _sheet_value(records[index].get("Комментарий")) or ""
            ).strip():
                records[index]["Комментарий"] = default["Комментарий"]
        return records

    @staticmethod
    def _merge_game_records(
        existing: list[dict],
        database_games: list[dict],
    ) -> tuple[list[dict], int]:
        by_app = {int(row["app_id"]): row for row in database_games}
        by_name: dict[str, dict] = {}
        for row in database_games:
            for value in (row.get("steam_name"), row.get("official_name")):
                if value:
                    by_name[normalize_name(str(value))] = row

        result: list[dict] = []
        seen: set[int] = set()
        unmatched = 0
        for source in existing:
            record = dict(source)
            source_name = str(
                _sheet_value(record.get("Название_Steam"))
                or _sheet_value(record.get("name"))
                or ""
            ).strip()
            if normalize_name(source_name) in EXCLUDED_GAMES:
                record["Статус"] = "Исключена"
                result.append(record)
                continue
            app_id = _parse_app_id(record.get("steam_app_id"))
            game = by_app.get(app_id) if app_id is not None else None
            if game is None and source_name:
                game = by_name.get(normalize_name(source_name))
            if game is None:
                if source_name or app_id is not None:
                    unmatched += 1
                    record["Ошибка"] = "Не найдена в согласованном каталоге"
                result.append(record)
                continue
            app_id = int(game["app_id"])
            seen.add(app_id)
            record.update(GoogleSheetsManager._automatic_game_values(game))
            result.append(record)

        for game in database_games:
            app_id = int(game["app_id"])
            if app_id in seen:
                continue
            record = {
                "name": game["steam_name"],
                "player_count": game.get("player_count") or "",
                "description": game.get("base_description") or "",
                "Статус": "Активна",
                "Комментарий_менеджера": "",
                "Описание_менеджера": game.get("manager_description") or "",
            }
            record.update(GoogleSheetsManager._automatic_game_values(game))
            result.append(record)
        return result, unmatched

    @staticmethod
    def _automatic_game_values(game: dict) -> dict:
        account_count = int(game.get("account_count") or 0)
        owned_count = int(game.get("owned_count") or 0)
        return {
            "steam_app_id": int(game["app_id"]),
            "Название_Steam": game.get("steam_name") or "",
            "Описание_Steam": game.get("store_description") or "",
            "Язык_описания": game.get("source_language") or "",
            "Жанры_Steam": _json_text(game.get("genres_json")),
            "Категории_Steam": _json_text(game.get("categories_json")),
            "Изображение_URL": game.get("header_image") or "",
            "Обновлено_Steam": game.get("metadata_updated_at") or "",
            "Наличие_лицензий": f"{owned_count}/{account_count}",
            "Последнее_промо": game.get("last_promotion") or "",
            "Цикл_ротации": game.get("last_rotation_cycle") or "",
            "Проверено": game.get("metadata_updated_at") or "",
            "Ошибка": game.get("last_error") or "",
        }

    def sync_promotion(
        self,
        storage: TrackerStorage,
        promotion_id: int,
        *,
        apply: bool = False,
    ) -> PromotionSheetSyncResult:
        spreadsheet = self.open()
        worksheet = spreadsheet.worksheet_by_title("Промо-план")
        headers, _ = self._current_headers(worksheet, "Промо-план")
        for header in SHEET_SCHEMAS["Промо-план"]:
            if header not in headers:
                headers.append(header)

        records = [dict(row) for row in worksheet.get_all_records()]
        row = dict(storage.promotion_sheet_row(promotion_id))
        record = {
            "Игра": row["steam_name"],
            "Статус": {
                "draft": "Черновик",
                "review": "На согласовании",
                "approved": "Согласовано",
                "postponed": "Отложено",
                "cancelled": "Отменено",
            }.get(row["status"], row["status"]),
            "Тестовый": "Да" if row["is_test"] else "",
            "Текст_сотрудникам": row["employee_text"] or "",
            "ID": row["id"],
            "steam_app_id": row["app_id"],
            "Скидка": row["discount_text"],
            "Акция_с": row["valid_from"] or "",
            "Акция_по": row["valid_to"] or "",
            "Комментарий_менеджера": row["manager_comment"] or "",
            "Текст_TG": row["telegram_text"] or "",
            "Текст_VK": row["vk_text"] or "",
            "Изображение_URL": row["publish_image_url"] or "",
            "Ответственный": row["claimed_name"] or "",
            "Взято_в_работу": row["claimed_at"] or "",
            "Согласовал": row["approved_by"] or "",
            "Согласовано_дата": row["approved_at"] or "",
            "Цикл_ротации": row["cycle_number"] or "",
            "Ошибка": "",
        }
        position = next(
            (
                index
                for index, existing in enumerate(records)
                if _parse_app_id(existing.get("ID")) == promotion_id
            ),
            None,
        )
        if position is None:
            records.append(record)
            action = "append"
        else:
            records[position].update(record)
            action = "update"

        if apply:
            self._replace_table(worksheet, headers, records)
        return PromotionSheetSyncResult(
            applied=apply,
            promotion_id=promotion_id,
            action=action,
        )

    def remove_promotion(self, promotion_id: int) -> bool:
        spreadsheet = self.open()
        worksheet = spreadsheet.worksheet_by_title("Промо-план")
        headers, _ = self._current_headers(worksheet, "Промо-план")
        for header in SHEET_SCHEMAS["Промо-план"]:
            if header not in headers:
                headers.append(header)
        records = [dict(row) for row in worksheet.get_all_records()]
        filtered = [
            row
            for row in records
            if _parse_app_id(row.get("ID")) != promotion_id
        ]
        if len(filtered) == len(records):
            return False
        self._replace_table(worksheet, headers, filtered)
        return True

    def clear_promotions(self) -> int:
        spreadsheet = self.open()
        worksheet = spreadsheet.worksheet_by_title("Промо-план")
        headers, _ = self._current_headers(worksheet, "Промо-план")
        for header in SHEET_SCHEMAS["Промо-план"]:
            if header not in headers:
                headers.append(header)
        records = worksheet.get_all_records()
        self._replace_table(worksheet, headers, [])
        return len(records)
