"""Транзакционное управление согласованным каталогом из Google Sheets."""

import math
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .catalog import EXCLUDED_GAMES, normalize_name
from .db import TrackerStorage
from .store import SteamStoreClient, StoreMetadata


ACTIVE_STATUSES = {"", "активна", "active"}
EXCLUDED_STATUSES = {
    "исключена",
    "excluded",
    "приостановлена",
    "paused",
}
DRAFT_STATUSES = {"черновик", "draft"}


def _cell_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and not math.isfinite(value):
        return ""
    return str(value).strip()


def _first_cell(row: dict, *keys: str) -> str:
    for key in keys:
        value = _cell_text(row.get(key))
        if value:
            return value
    return ""


def parse_app_id(value: object) -> int | None:
    text = _cell_text(value)
    if not text:
        return None
    if text.isdigit():
        return int(text)
    match = re.search(r"/app/(\d+)", text)
    return int(match.group(1)) if match else None


def parse_player_count(value: object) -> int:
    text = _cell_text(value)
    if not text:
        raise ValueError("не заполнено количество игроков")
    try:
        decimal_value = Decimal(text.replace(",", "."))
    except InvalidOperation as error:
        raise ValueError("некорректное количество игроков") from error
    if decimal_value != decimal_value.to_integral_value():
        raise ValueError("количество игроков должно быть целым")
    result = int(decimal_value)
    if result < 1:
        raise ValueError("количество игроков должно быть больше нуля")
    return result


@dataclass(frozen=True)
class CatalogSyncResult:
    applied: bool
    active_games: int
    excluded_games: int
    draft_games: int
    errors: list[str]


class CatalogManagementService:
    def __init__(
        self,
        storage: TrackerStorage,
        store_client: SteamStoreClient,
    ):
        self.storage = storage
        self.store_client = store_client

    def sync(
        self,
        rows: list[dict],
        *,
        apply: bool = False,
    ) -> CatalogSyncResult:
        known_names = self.storage.catalog_name_index()
        known_apps = self.storage.catalog_app_index()
        active_games: list[dict] = []
        metadata_to_save: list[StoreMetadata] = []
        excluded = 0
        drafts = 0
        errors: list[str] = []
        seen_app_ids: set[int] = set()

        for index, row in enumerate(rows, start=2):
            name = _first_cell(
                row,
                "name",
                "Игра",
                "Название",
                "Название_Steam",
            )
            app_id_value = _first_cell(
                row,
                "steam_app_id",
                "Steam AppID",
                "Ссылка Steam",
            )
            if not name and not app_id_value:
                continue

            status = _cell_text(row.get("Статус")).casefold()
            normalized_name = normalize_name(name)
            if normalized_name in EXCLUDED_GAMES:
                excluded += 1
                continue
            if status in EXCLUDED_STATUSES:
                excluded += 1
                continue
            if status in DRAFT_STATUSES:
                drafts += 1
                continue
            if status not in ACTIVE_STATUSES:
                errors.append(f"Строка {index}: неизвестный статус «{status}»")
                continue

            app_id = parse_app_id(app_id_value)
            known = known_names.get(normalized_name)
            if app_id is None and known:
                app_id = known[0]
            if app_id is None:
                errors.append(
                    f"Строка {index} {name or '(без названия)'}: "
                    "нужен Steam AppID или ссылка"
                )
                continue
            if app_id in seen_app_ids:
                errors.append(f"Строка {index}: AppID {app_id} повторяется")
                continue

            known_app = known_apps.get(app_id)
            metadata = None
            try:
                player_count = parse_player_count(
                    _first_cell(
                        row,
                        "player_count",
                        "Количество_игроков",
                    )
                )
                if known_app is None:
                    metadata = self.store_client.get_metadata(app_id)
            except Exception as error:
                errors.append(
                    f"Строка {index} {name or app_id}: {error}"
                )
                continue

            official_name = (
                name
                or (known_app["official_name"] if known_app else None)
                or (metadata.name if metadata else None)
                or f"Steam App {app_id}"
            )
            manager_description = _first_cell(
                row,
                "Описание_менеджера",
                "Ручное_описание",
            )
            legacy_description = _cell_text(row.get("description"))
            active_games.append(
                {
                    "app_id": app_id,
                    "steam_name": (
                        known_app["steam_name"]
                        if known_app
                        else metadata.name
                    ),
                    "official_name": official_name,
                    "player_count": player_count,
                    "base_description": (
                        legacy_description
                        or (
                            known_app["base_description"]
                            if known_app
                            else metadata.description
                        )
                    ),
                    "manager_description": manager_description or None,
                    "description_source": (
                        "manager"
                        if manager_description
                        else (
                            f"steam_store_{metadata.source_language}"
                            if metadata
                            else "existing_catalog"
                        )
                    ),
                }
            )
            if metadata:
                metadata_to_save.append(metadata)
            seen_app_ids.add(app_id)

        if errors:
            return CatalogSyncResult(
                applied=False,
                active_games=len(active_games),
                excluded_games=excluded,
                draft_games=drafts,
                errors=errors,
            )

        if not apply:
            return CatalogSyncResult(
                applied=False,
                active_games=len(active_games),
                excluded_games=excluded,
                draft_games=drafts,
                errors=[],
            )

        self.storage.replace_approved_catalog(active_games)
        for metadata in metadata_to_save:
            self.storage.save_game_metadata(
                metadata.app_id,
                steam_name=metadata.name,
                store_description=metadata.description,
                genres=metadata.genres,
                categories=metadata.categories,
                header_image=metadata.header_image,
                screenshots=metadata.screenshots,
                is_free=metadata.is_free,
                source_language=metadata.source_language,
            )
        return CatalogSyncResult(
            applied=True,
            active_games=len(active_games),
            excluded_games=excluded,
            draft_games=drafts,
            errors=[],
        )
