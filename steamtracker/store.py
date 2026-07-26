"""Обогащение согласованных игр публичными данными Steam Store."""

import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import requests

from .db import TrackerStorage


@dataclass(frozen=True)
class StoreMetadata:
    app_id: int
    name: str
    description: str | None
    genres: list[str]
    categories: list[str]
    header_image: str | None
    screenshots: list[str]
    is_free: bool | None
    source_language: str


class SteamStoreError(RuntimeError):
    pass


class SteamStoreClient:
    ENDPOINT = "https://store.steampowered.com/api/appdetails"

    def __init__(self, *, timeout: int = 30, session=requests):
        self.timeout = timeout
        self.session = session

    def _request(self, app_id: int, language: str, country: str) -> dict | None:
        response = self.session.get(
            self.ENDPOINT,
            params={
                "appids": app_id,
                "l": language,
                "cc": country,
            },
            headers={"User-Agent": "OMG-VR-SteamTracker/2"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json().get(str(app_id), {})
        return payload.get("data") if payload.get("success") else None

    def get_metadata(self, app_id: int) -> StoreMetadata:
        data = self._request(app_id, "russian", "ru")
        source_language = "ru"
        if not data:
            data = self._request(app_id, "english", "us")
            source_language = "en"
        if not data:
            raise SteamStoreError(f"Steam Store не вернул AppID {app_id}")

        description = data.get("short_description") or None
        if description and not re.search(r"[А-Яа-яЁё]", description):
            source_language = "en"
        return StoreMetadata(
            app_id=app_id,
            name=data.get("name") or f"Steam App {app_id}",
            description=description,
            genres=[
                item["description"]
                for item in data.get("genres", [])
                if item.get("description")
            ],
            categories=[
                item["description"]
                for item in data.get("categories", [])
                if item.get("description")
            ],
            header_image=data.get("header_image") or None,
            screenshots=[
                item["path_full"]
                for item in data.get("screenshots", [])
                if item.get("path_full")
            ],
            is_free=data.get("is_free"),
            source_language=source_language,
        )


@dataclass(frozen=True)
class EnrichmentSummary:
    updated: int
    skipped_fresh: int
    failed: int
    errors: list[str]


class GameEnrichmentService:
    def __init__(
        self,
        storage: TrackerStorage,
        client: SteamStoreClient,
        *,
        refresh_days: int = 30,
        request_interval: float = 0.25,
    ):
        self.storage = storage
        self.client = client
        self.refresh_days = refresh_days
        self.request_interval = request_interval

    def enrich(
        self,
        *,
        force: bool = False,
        limit: int | None = None,
    ) -> EnrichmentSummary:
        cutoff = datetime.now(UTC) - timedelta(days=self.refresh_days)
        updated = 0
        skipped = 0
        errors: list[str] = []

        rows = self.storage.approved_games_for_enrichment()
        if limit is not None:
            rows = rows[:limit]

        for row in rows:
            metadata_updated_at = row["metadata_updated_at"]
            if metadata_updated_at and not force:
                timestamp = datetime.fromisoformat(metadata_updated_at)
                if timestamp >= cutoff:
                    skipped += 1
                    continue
            try:
                metadata = self.client.get_metadata(row["app_id"])
                self.storage.save_game_metadata(
                    metadata.app_id,
                    store_description=metadata.description,
                    genres=metadata.genres,
                    categories=metadata.categories,
                    header_image=metadata.header_image,
                    screenshots=metadata.screenshots,
                    is_free=metadata.is_free,
                    source_language=metadata.source_language,
                )
                updated += 1
            except Exception as error:
                errors.append(f"{row['app_id']} {row['game_name']}: {error}")
            if self.request_interval:
                time.sleep(self.request_interval)

        return EnrichmentSummary(
            updated=updated,
            skipped_fresh=skipped,
            failed=len(errors),
            errors=errors,
        )
