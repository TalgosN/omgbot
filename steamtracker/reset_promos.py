"""Одноразовая безопасная очистка тестового промо-контура."""

import argparse
import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from .config import Settings
from .db import TrackerStorage
from .sheets import GoogleSheetsManager


CONFIRMATION = "RESET_PROMOS"


def backup_sqlite(source: Path) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = source.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / (
        f"{source.stem}.before-promo-reset-{timestamp}{source.suffix}"
    )
    with (
        closing(sqlite3.connect(source)) as source_connection,
        closing(sqlite3.connect(target)) as target_connection,
    ):
        source_connection.backup(target_connection)
    return target


def build_parser(settings: Settings) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Удаляет все промо, генерации, outbox и ротацию, "
            "не затрагивая игры, лицензии и игровое время"
        )
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=settings.db_path,
        help="Путь к SQLite Steam Tracker",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Применить очистку; без флага показывается только план",
    )
    parser.add_argument(
        "--confirm",
        help=f"Для применения введите точное значение {CONFIRMATION}",
    )
    parser.add_argument(
        "--keep-google",
        action="store_true",
        help="Не очищать строки листа «Промо-план»",
    )
    return parser


def main() -> None:
    settings = Settings.from_env()
    args = build_parser(settings).parse_args()
    storage = TrackerStorage(args.db)
    storage.initialize()
    counts = storage.promotion_reset_summary()

    if not args.apply:
        print(
            json.dumps(
                {
                    "applied": False,
                    "db": str(args.db),
                    "will_delete": counts,
                    "google_promo_plan": (
                        "keep" if args.keep_google else "clear"
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        print(
            "Изменений нет. Для очистки остановите bot и повторите с "
            f"--apply --confirm {CONFIRMATION}."
        )
        return

    if args.confirm != CONFIRMATION:
        raise SystemExit(
            f"Очистка не выполнена: нужен --confirm {CONFIRMATION}"
        )

    backup = backup_sqlite(args.db)
    deleted = storage.reset_all_promotions()
    google_rows_deleted = 0
    google_error = None
    if not args.keep_google:
        try:
            google_rows_deleted = GoogleSheetsManager(
                settings
            ).clear_promotions()
        except Exception as error:
            google_error = str(error)

    print(
        json.dumps(
            {
                "applied": True,
                "db": str(args.db),
                "backup": str(backup),
                "deleted": deleted,
                "google_rows_deleted": google_rows_deleted,
                "google_error": google_error,
                "next_promotion_id": 1,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if google_error:
        raise SystemExit(
            "SQLite очищена, но лист «Промо-план» очистить не удалось. "
            "После исправления доступа повторите эту же команду."
        )


if __name__ == "__main__":
    main()
