"""Неблокирующие фоновые задания Steam Tracker для scheduler Виарыча."""

import threading
from datetime import datetime

import pytz

from .config import Settings
from .db import TrackerStorage
from .llm import build_generator
from .promo import DryRunPublisher, PromotionWorkflow
from .sheets import GoogleSheetsManager
from .steam import LicenseSyncService, SteamClient
from .store import GameEnrichmentService, SteamStoreClient
from .weekly import WeeklyPromotionService, setting_enabled, week_period


_license_lock = threading.Lock()
_store_lock = threading.Lock()
_catalog_lock = threading.Lock()
_weekly_lock = threading.Lock()


def _sync_google_data(
    settings: Settings,
    storage: TrackerStorage,
) -> None:
    if not settings.google_export_enabled:
        return
    try:
        result = GoogleSheetsManager(settings).sync_tracker_data(
            storage,
            apply=True,
        )
        print(
            "Steam Tracker Google: "
            f"Current_State={result.current_state_rows}, "
            f"динамика={result.dynamics_rows}, "
            f"игр={result.game_rows}"
        )
    except Exception as error:
        print(f"Steam Tracker Google: ошибка выгрузки: {error}")


def _run_license_sync(settings: Settings) -> None:
    with _license_lock:
        try:
            storage = TrackerStorage(settings.db_path)
            storage.initialize()
            summary = LicenseSyncService(
                storage,
                SteamClient(settings.steam_api_key or ""),
                removal_threshold=settings.removal_threshold,
            ).sync()
            print(
                "Steam Tracker: "
                f"аккаунтов={summary.accounts_ok}, "
                f"ошибок={summary.accounts_failed}, "
                f"добавлено={summary.licenses_added}, "
                f"удалено={summary.licenses_removed}"
            )
            _sync_google_data(settings, storage)
        except Exception as error:
            print(f"Steam Tracker: ошибка синхронизации лицензий: {error}")


def start_license_sync() -> bool:
    settings = Settings.from_env()
    if not settings.steam_sync_enabled:
        return False
    if not settings.steam_api_key:
        print("Steam Tracker: STEAM_API_KEY не задан, синхронизация пропущена")
        return False
    if _license_lock.locked():
        return False
    threading.Thread(
        target=_run_license_sync,
        args=(settings,),
        name="steamtracker-license-sync",
        daemon=True,
    ).start()
    return True


def _run_store_enrichment(settings: Settings) -> None:
    with _store_lock:
        try:
            storage = TrackerStorage(settings.db_path)
            storage.initialize()
            summary = GameEnrichmentService(
                storage,
                SteamStoreClient(),
            ).enrich()
            print(
                "Steam Tracker Store: "
                f"обновлено={summary.updated}, "
                f"свежих={summary.skipped_fresh}, "
                f"ошибок={summary.failed}"
            )
            _sync_google_data(settings, storage)
        except Exception as error:
            print(f"Steam Tracker Store: ошибка обогащения: {error}")


def start_store_enrichment() -> bool:
    settings = Settings.from_env()
    if not settings.store_enrichment_enabled:
        return False
    if _store_lock.locked():
        return False
    threading.Thread(
        target=_run_store_enrichment,
        args=(settings,),
        name="steamtracker-store-enrichment",
        daemon=True,
    ).start()
    return True


def start_catalog_sync() -> bool:
    # Совместимость со старым планировщиком: Google Sheets больше не
    # является источником каталога и не должна перезаписывать SQLite.
    return False


def _run_weekly_promo(settings: Settings, bot=None) -> None:
    with _weekly_lock:
        try:
            if settings.publish_mode != "dry_run":
                raise RuntimeError(
                    "Автоматическое промо пока разрешено только в dry_run"
                )
            storage = TrackerStorage(settings.db_path)
            storage.initialize()
            sheets = GoogleSheetsManager(settings)
            service = WeeklyPromotionService(
                storage,
                PromotionWorkflow(
                    storage,
                    build_generator(settings),
                    DryRunPublisher(),
                ),
                sheets,
            )
            result = service.run()
            print(
                "Steam Tracker Weekly: "
                f"промо={result.promotion_id}, "
                f"app_id={result.app_id}, "
                f"цикл={result.cycle_number}"
            )
            if (
                bot is not None
                and settings.telegram_approval_enabled
                and settings.telegram_approver_ids
            ):
                from .telegram import send_promotion_to_approvers

                send_promotion_to_approvers(
                    bot,
                    settings,
                    result.promotion_id,
                )
        except Exception as error:
            print(f"Steam Tracker Weekly: ошибка: {error}")


def start_weekly_promo(bot=None, *, now: datetime | None = None) -> bool:
    settings = Settings.from_env()
    if not settings.weekly_promo_enabled:
        return False
    if _weekly_lock.locked():
        return False
    storage = TrackerStorage(settings.db_path)
    storage.initialize()
    tracker_settings = storage.tracker_settings()
    if not setting_enabled(tracker_settings.get("weekly_promo_enabled")):
        return False
    day_names = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
    }
    generation_day = str(
        tracker_settings.get("generation_day") or ""
    ).strip().casefold()
    if generation_day not in day_names:
        print(
            "Steam Tracker Weekly: неизвестный день запуска "
            f"{generation_day!r}"
        )
        return False
    generation_time = str(
        tracker_settings.get("generation_time") or ""
    ).strip()
    try:
        configured_time = datetime.strptime(
            generation_time,
            "%H:%M",
        ).time()
        timezone = pytz.timezone(
            str(
                tracker_settings.get("timezone")
                or "Europe/Moscow"
            ).strip()
        )
    except (ValueError, TypeError) as error:
        print(f"Steam Tracker Weekly: неверные настройки времени: {error}")
        return False
    current = now.astimezone(timezone) if now else datetime.now(timezone)
    if current.weekday() != day_names[generation_day]:
        return False
    if current.time().replace(second=0, microsecond=0) < configured_time:
        return False
    valid_from, valid_to = week_period(current.date())
    if storage.has_real_promotion_for_period(
        valid_from.isoformat(),
        valid_to.isoformat(),
    ):
        return False
    threading.Thread(
        target=_run_weekly_promo,
        args=(settings, bot),
        name="steamtracker-weekly-promo",
        daemon=True,
    ).start()
    return True
