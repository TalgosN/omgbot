"""Safely renames the legacy general task type in the main SQLite database."""

import argparse
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB_PATH = Path('db/omgbot.sql')
LEGACY_GENERAL_TASK_TYPE = 'Вопрос/жалоба/предложение'
GENERAL_TASK_TYPE = 'Общее обращение'


def backup_sqlite(source):
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup_dir = source.parent / 'backups'
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / (
        f'{source.stem}.before-general-task-type-rename-{timestamp}{source.suffix}'
    )
    with (
        closing(sqlite3.connect(source)) as source_connection,
        closing(sqlite3.connect(target)) as target_connection,
    ):
        source_connection.backup(target_connection)
    return target


def _counts_by_status(connection, task_type):
    return {
        str(status or 'без статуса'): int(count)
        for status, count in connection.execute(
            '''SELECT status, COUNT(*) FROM tasks
               WHERE type=? GROUP BY status ORDER BY status''',
            (task_type,),
        )
    }


def rename_general_task_type(db_path, apply=False):
    db_path = Path(db_path)
    if not db_path.is_file():
        raise FileNotFoundError(f'База данных не найдена: {db_path}')

    connection = sqlite3.connect(db_path)
    try:
        table_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tasks'"
        ).fetchone()
        if not table_exists:
            raise RuntimeError('В базе данных отсутствует таблица tasks')

        source_counts = _counts_by_status(connection, LEGACY_GENERAL_TASK_TYPE)
        target_counts = _counts_by_status(connection, GENERAL_TASK_TYPE)
        source_total = sum(source_counts.values())
        result = {
            'status': 'ready' if source_total else 'already_renamed',
            'db': str(db_path),
            'old_type': LEGACY_GENERAL_TASK_TYPE,
            'new_type': GENERAL_TASK_TYPE,
            'matching_rows': source_total,
            'rows_by_status': source_counts,
            'existing_new_rows': sum(target_counts.values()),
            'applied': False,
        }
        if not source_total or not apply:
            return result

        backup_path = backup_sqlite(db_path)
        with connection:
            cursor = connection.execute(
                'UPDATE tasks SET type=? WHERE type=?',
                (GENERAL_TASK_TYPE, LEGACY_GENERAL_TASK_TYPE),
            )
        if cursor.rowcount != source_total:
            raise RuntimeError(
                f'Ожидалось обновить {source_total} записей, обновлено {cursor.rowcount}'
            )
        result.update({
            'status': 'renamed',
            'applied': True,
            'updated_rows': cursor.rowcount,
            'backup': str(backup_path),
        })
        return result
    finally:
        connection.close()


def build_parser():
    parser = argparse.ArgumentParser(
        description='Переименовывает тип задач «Вопрос/жалоба/предложение»',
    )
    parser.add_argument('--db', type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument(
        '--apply',
        action='store_true',
        help='Применить переименование; без флага показывается только план',
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        result = rename_general_task_type(args.db, apply=args.apply)
    except (FileNotFoundError, RuntimeError, sqlite3.Error) as error:
        raise SystemExit(f'Переименование не выполнено: {error}') from error
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result['status'] == 'ready':
        print('Изменений нет. Для применения повторите команду с --apply.')


if __name__ == '__main__':
    main()
