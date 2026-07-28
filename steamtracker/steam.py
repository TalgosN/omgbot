"""Клиент Steam Web API и безопасная синхронизация лицензий."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import requests

from .db import OwnedGame, ScanResult, TrackerStorage


class OwnedGamesClient(Protocol):
    def get_owned_games(self, steam_id: str) -> list[OwnedGame]:
        ...


class SteamApiError(RuntimeError):
    pass


class SteamClient:
    ENDPOINT = (
        "https://api.steampowered.com/"
        "IPlayerService/GetOwnedGames/v0001/"
    )

    def __init__(self, api_key: str, *, timeout: int = 30):
        if not api_key:
            raise ValueError("STEAM_API_KEY не задан")
        self.api_key = api_key
        self.timeout = timeout

    def get_owned_games(self, steam_id: str) -> list[OwnedGame]:
        response = requests.get(
            self.ENDPOINT,
            params={
                "key": self.api_key,
                "steamid": steam_id,
                "format": "json",
                "include_appinfo": 1,
                "include_played_free_games": 1,
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json().get("response", {})
        if "game_count" not in payload:
            raise SteamApiError(
                f"Steam не вернул библиотеку для аккаунта {steam_id}"
            )
        return [
            OwnedGame(
                app_id=int(game["appid"]),
                name=game.get("name") or f"App {game['appid']}",
                playtime_minutes=int(game.get("playtime_forever", 0)),
            )
            for game in payload.get("games", [])
        ]


@dataclass(frozen=True)
class SyncSummary:
    accounts_ok: int
    accounts_failed: int
    games_seen: int
    licenses_added: int
    licenses_removed: int
    errors: list[str]


class LicenseSyncService:
    def __init__(
        self,
        storage: TrackerStorage,
        client: OwnedGamesClient,
        *,
        removal_threshold: int = 3,
    ):
        self.storage = storage
        self.client = client
        self.removal_threshold = removal_threshold

    def sync(self) -> SyncSummary:
        timestamp = datetime.now(UTC).replace(microsecond=0).isoformat()
        results: list[ScanResult] = []
        errors: list[str] = []

        for steam_id in self.storage.active_steam_ids():
            try:
                results.append(
                    self.sync_account(
                        steam_id,
                        timestamp=timestamp,
                    )
                )
            except Exception as error:
                errors.append(f"{steam_id}: {error}")

        return SyncSummary(
            accounts_ok=len(results),
            accounts_failed=len(errors),
            games_seen=sum(result.seen_games for result in results),
            licenses_added=sum(result.added for result in results),
            licenses_removed=sum(result.removed for result in results),
            errors=errors,
        )

    def sync_account(
        self,
        steam_id: str,
        *,
        timestamp: str | None = None,
    ) -> ScanResult:
        timestamp = timestamp or datetime.now(UTC).replace(
            microsecond=0
        ).isoformat()
        games = self.client.get_owned_games(steam_id)
        return self.storage.record_account_scan(
            steam_id,
            games,
            scanned_at=timestamp,
            snapshot_date=timestamp[:10],
            removal_threshold=self.removal_threshold,
        )
