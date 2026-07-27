"""CLI Steam Tracker внутри основного проекта Виарыча."""

import argparse
import csv
import json
from dataclasses import asdict
from datetime import date
from pathlib import Path

from .catalog import read_catalog_csv, resolve_catalog
from .config import Settings
from .db import TrackerStorage
from .llm import build_generator
from .management import CatalogManagementService
from .promo import DryRunPublisher, PromotionWorkflow
from .sheets import GoogleSheetsManager
from .steam import LicenseSyncService, SteamClient
from .store import GameEnrichmentService, SteamStoreClient
from .weekly import WeeklyPromotionService


def _storage(settings: Settings) -> TrackerStorage:
    storage = TrackerStorage(settings.db_path)
    storage.initialize()
    return storage


def command_init(args: argparse.Namespace, settings: Settings) -> None:
    storage = _storage(settings)
    accounts = storage.import_legacy_accounts(args.legacy_db)
    rows = read_catalog_csv(
        csv_path=args.catalog_csv,
        csv_url=None if args.catalog_csv else args.catalog_url,
    )
    catalog = resolve_catalog(rows, args.legacy_db)
    imported = storage.upsert_catalog_games(catalog.games)
    if catalog.unresolved:
        raise RuntimeError(
            "Не найдены AppID для игр: " + ", ".join(catalog.unresolved)
        )
    print(
        json.dumps(
            {
                "db": str(settings.db_path),
                "accounts": accounts,
                "approved_games": imported,
                "excluded": catalog.excluded,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def command_sync(args: argparse.Namespace, settings: Settings) -> None:
    api_key = args.api_key or settings.steam_api_key
    if not api_key:
        raise RuntimeError(
            "STEAM_API_KEY не задан. Для тестов без ключа используйте unittest."
        )
    storage = _storage(settings)
    summary = LicenseSyncService(
        storage,
        SteamClient(api_key),
        removal_threshold=settings.removal_threshold,
    ).sync()
    print(json.dumps(summary.__dict__, ensure_ascii=False, indent=2))


def command_enrich_store(
    args: argparse.Namespace,
    settings: Settings,
) -> None:
    storage = _storage(settings)
    summary = GameEnrichmentService(
        storage,
        SteamStoreClient(),
    ).enrich(force=args.force, limit=args.limit)
    print(json.dumps(summary.__dict__, ensure_ascii=False, indent=2))


def command_setup_sheets(
    args: argparse.Namespace,
    settings: Settings,
) -> None:
    result = GoogleSheetsManager(settings).setup(apply=args.apply)
    print(
        json.dumps(
            {
                "applied": result.applied,
                "actions": [asdict(action) for action in result.actions],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if not args.apply:
        print("Изменений нет. Для применения повторите с --apply.")


def command_sync_catalog_sheets(
    args: argparse.Namespace,
    settings: Settings,
) -> None:
    storage = _storage(settings)
    manager = GoogleSheetsManager(settings)
    result = CatalogManagementService(
        storage,
        SteamStoreClient(),
    ).sync(
        manager.read_catalog_rows(),
        apply=args.apply,
    )
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    if result.errors:
        raise RuntimeError(
            "Каталог не изменён из-за ошибок в Google Sheets"
        )
    if not args.apply:
        print("Каталог проверен. Для применения повторите с --apply.")


def command_sync_data_sheets(
    args: argparse.Namespace,
    settings: Settings,
) -> None:
    storage = _storage(settings)
    manager = GoogleSheetsManager(settings)
    setup = manager.setup(apply=args.apply)
    missing = [
        action.sheet
        for action in setup.actions
        if action.action == "create_sheet"
    ]
    if missing and not args.apply:
        print(
            json.dumps(
                {
                    "applied": False,
                    "missing_sheets": missing,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        print(
            "Сначала создайте листы командой "
            "setup-sheets --apply либо повторите эту команду с --apply."
        )
        return
    result = manager.sync_tracker_data(storage, apply=args.apply)
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    if not args.apply:
        print("Данные не записаны. Для применения повторите с --apply.")


def command_create_promo(
    args: argparse.Namespace,
    settings: Settings,
) -> None:
    storage = _storage(settings)
    promotion_id = storage.create_promotion(
        app_id=args.app_id,
        discount_text=args.discount,
        valid_from=args.valid_from,
        valid_to=args.valid_to,
        manager_comment=args.comment,
        image_url=args.image_url,
    )
    print(f"Создано промо #{promotion_id}")


def _workflow(settings: Settings) -> PromotionWorkflow:
    if settings.publish_mode != "dry_run":
        raise RuntimeError(
            "На первом этапе разрешён только PUBLISH_MODE=dry_run"
        )
    return PromotionWorkflow(
        _storage(settings),
        build_generator(settings),
        DryRunPublisher(),
    )


def command_generate(args: argparse.Namespace, settings: Settings) -> None:
    texts = _workflow(settings).generate(args.promotion_id)
    print(
        json.dumps(
            texts.__dict__,
            ensure_ascii=False,
            indent=2,
        )
    )


def command_approve(args: argparse.Namespace, settings: Settings) -> None:
    _workflow(settings).approve_and_dispatch(
        args.promotion_id,
        approved_by=args.by,
    )
    print(
        f"Промо #{args.promotion_id} согласовано; "
        "созданы только dry-run задания."
    )


def command_sync_promo_sheet(
    args: argparse.Namespace,
    settings: Settings,
) -> None:
    result = GoogleSheetsManager(settings).sync_promotion(
        _storage(settings),
        args.promotion_id,
        apply=args.apply,
    )
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    if not args.apply:
        print("Промо не записано. Для применения повторите с --apply.")


def command_weekly_promo(
    args: argparse.Namespace,
    settings: Settings,
) -> None:
    storage = _storage(settings)
    service = WeeklyPromotionService(
        storage,
        _workflow(settings),
        GoogleSheetsManager(settings),
    )
    if not args.apply:
        print(
            json.dumps(
                service.preview(),
                ensure_ascii=False,
                indent=2,
            )
        )
        print("Промо не создано. Для применения повторите с --apply.")
        return
    result = service.run(
        reference_date=(
            date.fromisoformat(args.date) if args.date else None
        ),
        force=args.force,
    )
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))


def command_status(args: argparse.Namespace, settings: Settings) -> None:
    storage = _storage(settings)
    print(json.dumps(storage.summary(), ensure_ascii=False, indent=2))


def command_export_availability(
    args: argparse.Namespace,
    settings: Settings,
) -> None:
    storage = _storage(settings)
    rows = storage.approved_license_matrix()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(
            [
                "steam_app_id",
                "game_name",
                "player_count",
                "club_name",
                "zone",
                "owned",
                "last_seen_at",
                "playtime_minutes",
            ]
        )
        writer.writerows(
            [
                row["app_id"],
                row["game_name"],
                row["player_count"],
                row["club_name"],
                row["zone"],
                row["owned"],
                row["last_seen_at"],
                row["last_playtime_minutes"],
            ]
            for row in rows
        )
    print(f"Матрица лицензий: {args.output} ({len(rows)} строк)")


def build_parser(settings: Settings) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Steam Tracker v2 — лицензии и промо Виарыча"
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=settings.db_path,
        help="Путь к компактной SQLite DB",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init")
    init_parser.add_argument(
        "--legacy-db",
        type=Path,
        default=settings.legacy_db_path,
    )
    init_parser.add_argument("--catalog-csv", type=Path)
    init_parser.add_argument(
        "--catalog-url",
        default=settings.catalog_url,
    )
    init_parser.set_defaults(handler=command_init)

    sync_parser = subparsers.add_parser("sync-steam")
    sync_parser.add_argument("--api-key")
    sync_parser.set_defaults(handler=command_sync)

    enrich_parser = subparsers.add_parser("enrich-store")
    enrich_parser.add_argument("--force", action="store_true")
    enrich_parser.add_argument("--limit", type=int)
    enrich_parser.set_defaults(handler=command_enrich_store)

    setup_sheets_parser = subparsers.add_parser("setup-sheets")
    setup_sheets_parser.add_argument("--apply", action="store_true")
    setup_sheets_parser.set_defaults(handler=command_setup_sheets)

    sync_catalog_parser = subparsers.add_parser("sync-catalog-sheets")
    sync_catalog_parser.add_argument("--apply", action="store_true")
    sync_catalog_parser.set_defaults(handler=command_sync_catalog_sheets)

    sync_data_parser = subparsers.add_parser("sync-data-sheets")
    sync_data_parser.add_argument("--apply", action="store_true")
    sync_data_parser.set_defaults(handler=command_sync_data_sheets)

    promo_parser = subparsers.add_parser("create-promo")
    promo_parser.add_argument("--app-id", type=int, required=True)
    promo_parser.add_argument("--discount", required=True)
    promo_parser.add_argument("--from", dest="valid_from")
    promo_parser.add_argument("--to", dest="valid_to")
    promo_parser.add_argument("--comment")
    promo_parser.add_argument("--image-url")
    promo_parser.set_defaults(handler=command_create_promo)

    generate_parser = subparsers.add_parser("generate")
    generate_parser.add_argument("promotion_id", type=int)
    generate_parser.set_defaults(handler=command_generate)

    approve_parser = subparsers.add_parser("approve")
    approve_parser.add_argument("promotion_id", type=int)
    approve_parser.add_argument("--by", required=True)
    approve_parser.set_defaults(handler=command_approve)

    sync_promo_parser = subparsers.add_parser("sync-promo-sheet")
    sync_promo_parser.add_argument("promotion_id", type=int)
    sync_promo_parser.add_argument("--apply", action="store_true")
    sync_promo_parser.set_defaults(handler=command_sync_promo_sheet)

    weekly_parser = subparsers.add_parser("weekly-promo")
    weekly_parser.add_argument("--apply", action="store_true")
    weekly_parser.add_argument(
        "--force",
        action="store_true",
        help="Игнорировать weekly_promo_enabled в тестовом запуске",
    )
    weekly_parser.add_argument(
        "--date",
        help="Дата внутри тестовой недели в формате YYYY-MM-DD",
    )
    weekly_parser.set_defaults(handler=command_weekly_promo)

    status_parser = subparsers.add_parser("status")
    status_parser.set_defaults(handler=command_status)

    export_parser = subparsers.add_parser("export-availability")
    export_parser.add_argument(
        "--output",
        type=Path,
        default=Path("Reports") / "steam_license_availability.csv",
    )
    export_parser.set_defaults(handler=command_export_availability)
    return parser


def main() -> None:
    settings = Settings.from_env()
    parser = build_parser(settings)
    args = parser.parse_args()
    settings = Settings(
        db_path=args.db,
        legacy_db_path=settings.legacy_db_path,
        catalog_url=settings.catalog_url,
        steam_api_key=settings.steam_api_key,
        publish_mode=settings.publish_mode,
        removal_threshold=settings.removal_threshold,
        generator_provider=settings.generator_provider,
        openrouter_api_key=settings.openrouter_api_key,
        openrouter_model=settings.openrouter_model,
        telegram_approval_enabled=settings.telegram_approval_enabled,
        telegram_approver_ids=settings.telegram_approver_ids,
        steam_sync_enabled=settings.steam_sync_enabled,
        store_enrichment_enabled=settings.store_enrichment_enabled,
        spreadsheet_id=settings.spreadsheet_id,
        google_service_account_file=settings.google_service_account_file,
        catalog_sync_enabled=settings.catalog_sync_enabled,
        google_export_enabled=settings.google_export_enabled,
        weekly_promo_enabled=settings.weekly_promo_enabled,
    )
    args.handler(args, settings)


if __name__ == "__main__":
    main()
