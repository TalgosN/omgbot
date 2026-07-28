import os
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path


DEFAULT_SOURCES = (
    Path("db/omgbot.sql"),
    Path("db/steamtracker_v2.db"),
)
DEFAULT_BACKUP_DIR = Path("db/backups")
DEFAULT_RETENTION_DAYS = 7


class BackupError(RuntimeError):
    pass


def backup_sqlite(source, backup_dir=DEFAULT_BACKUP_DIR, now=None):
    source = Path(source)
    backup_dir = Path(backup_dir)
    if not source.is_file():
        raise BackupError(f"База данных не найдена: {source}")

    current = now or datetime.now(UTC)
    timestamp = current.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"{source.stem}-{timestamp}.sqlite3"
    temporary = target.with_suffix(f"{target.suffix}.tmp")

    try:
        with (
            closing(sqlite3.connect(source, timeout=30)) as source_connection,
            closing(sqlite3.connect(temporary, timeout=30)) as target_connection,
        ):
            source_connection.backup(target_connection)
            result = [
                row[0]
                for row in target_connection.execute("PRAGMA integrity_check")
            ]
        if result != ["ok"]:
            raise BackupError(
                f"Проверка целостности {source.name} завершилась с ошибкой: "
                f"{'; '.join(result)}"
            )
        os.replace(temporary, target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    return target


def remove_expired_backups(
    source,
    backup_dir=DEFAULT_BACKUP_DIR,
    retention_days=DEFAULT_RETENTION_DAYS,
    now=None,
):
    if retention_days < 1:
        raise ValueError("Срок хранения резервных копий должен быть не меньше дня")

    source = Path(source)
    backup_dir = Path(backup_dir)
    current = now or datetime.now(UTC)
    cutoff = current.timestamp() - timedelta(days=retention_days).total_seconds()
    removed = []
    for candidate in backup_dir.glob(f"{source.stem}-*.sqlite3"):
        if candidate.is_file() and candidate.stat().st_mtime < cutoff:
            candidate.unlink()
            removed.append(candidate)
    return removed


def create_daily_backups(
    sources=DEFAULT_SOURCES,
    backup_dir=DEFAULT_BACKUP_DIR,
    retention_days=DEFAULT_RETENTION_DAYS,
    now=None,
):
    current = now or datetime.now(UTC)
    created = []
    errors = []

    for source in (Path(item) for item in sources):
        try:
            created.append(backup_sqlite(source, backup_dir, current))
            remove_expired_backups(
                source,
                backup_dir,
                retention_days,
                current,
            )
        except Exception as error:
            errors.append(f"{source}: {error}")

    if errors:
        raise BackupError("; ".join(errors))
    return created


def run_scheduled_backup(bot, error_chat_id):
    try:
        created = create_daily_backups()
        print(
            "Резервное копирование завершено: "
            + ", ".join(str(path) for path in created)
        )
        return True
    except Exception as error:
        message = f"Ошибка резервного копирования SQLite: {error}"
        print(message)
        try:
            bot.send_message(error_chat_id, message)
        except Exception as notification_error:
            print(
                "Ошибка отправки уведомления о резервном копировании: "
                f"{notification_error}"
            )
        return False


if __name__ == "__main__":
    for backup_path in create_daily_backups():
        print(backup_path)
