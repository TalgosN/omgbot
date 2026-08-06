"""Разовое безопасное переименование «КЦ» в «Коллцентр» в основной БД."""

import argparse
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_DB_PATH = Path('db/omgbot.sql')
TARGET_NAME = 'Коллцентр'
SOURCE_NAMES = {'кц'}


def normalized_club_name(value):
    return str(value or '').strip().casefold()


def backup_sqlite(source):
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup_dir = source.parent / 'backups'
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / (
        f'{source.stem}.before-callcenter-rename-{timestamp}{source.suffix}'
    )
    with (
        closing(sqlite3.connect(source)) as source_connection,
        closing(sqlite3.connect(target)) as target_connection,
    ):
        source_connection.backup(target_connection)
    return target


def rename_callcenter(db_path, apply=False):
    db_path = Path(db_path)
    if not db_path.is_file():
        raise FileNotFoundError(f'База данных не найдена: {db_path}')

    conn = sqlite3.connect(db_path)
    try:
        table_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='clubs'"
        ).fetchone()
        if not table_exists:
            raise RuntimeError('В базе данных отсутствует таблица clubs')

        rows = conn.execute(
            'SELECT rowid, club, status FROM clubs ORDER BY rowid'
        ).fetchall()
        target_rows = [
            row for row in rows
            if normalized_club_name(row[1]) == TARGET_NAME.casefold()
        ]
        source_rows = [
            row for row in rows
            if normalized_club_name(row[1]) in SOURCE_NAMES
        ]

        if target_rows and source_rows:
            raise RuntimeError(
                'В clubs одновременно найдены «Коллцентр» и старое название «КЦ»'
            )
        if len(target_rows) > 1 or len(source_rows) > 1:
            raise RuntimeError('В clubs найдено несколько записей Коллцентра')
        if target_rows:
            return {
                'status': 'already_renamed',
                'db': str(db_path),
                'club': target_rows[0][1],
                'club_status': target_rows[0][2],
                'applied': False,
            }
        if not source_rows:
            raise RuntimeError('В clubs не найдены ни «КЦ», ни «Коллцентр»')

        source_row = source_rows[0]
        result = {
            'status': 'ready' if not apply else 'renamed',
            'db': str(db_path),
            'old_name': source_row[1],
            'new_name': TARGET_NAME,
            'club_status': source_row[2],
            'applied': apply,
        }
        if not apply:
            return result

        backup_path = backup_sqlite(db_path)
        with conn:
            conn.execute(
                'UPDATE clubs SET club=? WHERE rowid=?',
                (TARGET_NAME, source_row[0]),
            )
        result['backup'] = str(backup_path)
        return result
    finally:
        conn.close()


def build_parser():
    parser = argparse.ArgumentParser(
        description='Переименовывает старую запись «КЦ» в «Коллцентр»'
    )
    parser.add_argument(
        '--db',
        type=Path,
        default=DEFAULT_DB_PATH,
        help='Путь к основной SQLite БД',
    )
    parser.add_argument(
        '--apply',
        action='store_true',
        help='Применить переименование; без флага показывается только план',
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        result = rename_callcenter(args.db, apply=args.apply)
    except (FileNotFoundError, RuntimeError, sqlite3.Error) as error:
        raise SystemExit(f'Переименование не выполнено: {error}') from error
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result['status'] == 'ready':
        print('Изменений нет. Для применения повторите команду с --apply.')


if __name__ == '__main__':
    main()
