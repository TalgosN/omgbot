"""Импорт согласованного каталога игр с устойчивым матчингом по AppID."""

import csv
import io
import re
import sqlite3
import unicodedata
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

import requests


EXCLUDED_GAMES = {"pixel dungeon vr prologue"}


def normalize_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = normalized.replace("®", "").replace("™", "")
    return re.sub(r"[^a-zа-яё0-9]+", " ", normalized).strip()


def _parse_player_count(value: str) -> int | None:
    value = value.strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError as error:
        raise ValueError(f"Некорректное количество игроков: {value}") from error


@dataclass(frozen=True)
class CatalogLoadResult:
    games: list[dict]
    excluded: list[str]
    unresolved: list[str]


def read_catalog_csv(
    *,
    csv_path: str | Path | None = None,
    csv_url: str | None = None,
) -> list[dict[str, str]]:
    if csv_path:
        text = Path(csv_path).read_text(encoding="utf-8-sig")
    elif csv_url:
        response = requests.get(csv_url, timeout=30)
        response.raise_for_status()
        text = response.content.decode("utf-8-sig")
    else:
        raise ValueError("Нужно передать csv_path или csv_url")
    return list(csv.DictReader(io.StringIO(text)))


def resolve_catalog(
    rows: list[dict[str, str]],
    legacy_db_path: str | Path,
) -> CatalogLoadResult:
    with closing(
        sqlite3.connect(
            f"file:{Path(legacy_db_path)}?mode=ro",
            uri=True,
        )
    ) as legacy:
        legacy_games = legacy.execute(
            "SELECT app_id, name FROM games WHERE name IS NOT NULL"
        ).fetchall()

    by_name: dict[str, tuple[int, str]] = {}
    for app_id, name in legacy_games:
        by_name[normalize_name(name)] = (int(app_id), name)

    games: list[dict] = []
    excluded: list[str] = []
    unresolved: list[str] = []

    for row in rows:
        official_name = (row.get("name") or row.get("Игра") or "").strip()
        if not official_name:
            continue

        normalized_name = normalize_name(official_name)
        if normalized_name in EXCLUDED_GAMES:
            excluded.append(official_name)
            continue

        explicit_app_id = (row.get("steam_app_id") or "").strip()
        legacy_match = by_name.get(normalized_name)
        if explicit_app_id:
            app_id = int(explicit_app_id)
            steam_name = legacy_match[1] if legacy_match else official_name
        elif legacy_match:
            app_id, steam_name = legacy_match
        else:
            unresolved.append(official_name)
            continue

        games.append(
            {
                "app_id": app_id,
                "steam_name": steam_name,
                "official_name": official_name,
                "player_count": _parse_player_count(
                    row.get("player_count", "")
                ),
                "base_description": (row.get("description") or "").strip() or None,
                "description_source": "google_sheet",
            }
        )

    return CatalogLoadResult(
        games=games,
        excluded=excluded,
        unresolved=unresolved,
    )
