"""Безопасная подготовка и чтение управляющих Google-листов."""

from dataclasses import dataclass

from .config import Settings


SHEET_SCHEMAS: dict[str, list[str]] = {
    "Игры": [
        "name",
        "player_count",
        "description",
        "steam_app_id",
        "Статус",
        "Комментарий_менеджера",
        "Название_Steam",
        "Наличие_лицензий",
        "Проверено",
        "Ошибка",
    ],
    "Промо-план": [
        "Игра",
        "Статус",
        "Текст_сотрудникам",
        "ID",
        "steam_app_id",
        "Скидка",
        "Акция_с",
        "Акция_по",
        "Комментарий_менеджера",
        "Текст_TG",
        "Текст_VK",
        "Изображение_URL",
        "Согласовал",
        "Согласовано_дата",
        "Ошибка",
    ],
    "Наличие лицензий": [
        "steam_app_id",
        "Игра",
        "Количество_игроков",
        "Клуб",
        "Зона",
        "Лицензия",
        "Проверено",
        "Игровое_время_минут",
    ],
    "Steam Динамика": [
        "Дата",
        "steam_app_id",
        "Игра",
        "Клуб",
        "Зона",
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


class GoogleSheetsManager:
    def __init__(
        self,
        settings: Settings,
        *,
        client=None,
        worksheet_not_found: type[Exception] | tuple[type[Exception], ...] | None = None,
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
                        rows=1000,
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
