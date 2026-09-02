"""Safely repairs the known SQLite freelist and Bukza index inconsistencies."""

import argparse
import json
import re
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_DB_PATH = Path('db/omgbot.sql')
TARGET_INDEXES = (
    'idx_bukza_order_history_order',
    'idx_bukza_orders_reservation_at',
)
INDEX_ISSUES = {
    f'wrong # of entries in index {index}' for index in TARGET_INDEXES
}


def integrity_issues(connection):
    messages = [row[0] for row in connection.execute('PRAGMA integrity_check')]
    return [] if messages == ['ok'] else messages


def repairable_issue(message):
    if message in INDEX_ISSUES:
        return True
    lines = str(message).splitlines()
    if not lines or lines[0] != '*** in database main ***':
        return False
    return bool(lines[1:]) and all(
        re.fullmatch(r'Freelist: size is \d+ but should be \d+', line)
        or re.fullmatch(r'Page \d+: never used', line)
        for line in lines[1:]
    )


def backup_database(source):
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup_dir = source.parent / 'backups'
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / (
        f'{source.stem}.before-integrity-repair-{timestamp}{source.suffix}'
    )
    shutil.copy2(source, target)
    return target


def _table_counts(connection):
    result = {}
    for table in ('bukza_orders', 'bukza_order_history'):
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        if exists:
            result[table] = int(connection.execute(
                f'SELECT COUNT(*) FROM "{table}" NOT INDEXED'
            ).fetchone()[0])
    return result


def repair_sqlite_integrity(db_path=DEFAULT_DB_PATH, apply=False):
    db_path = Path(db_path).resolve()
    if not db_path.is_file():
        raise FileNotFoundError(f'База данных не найдена: {db_path}')

    connection = sqlite3.connect(db_path, timeout=2)
    try:
        issues = integrity_issues(connection)
        if not issues:
            return {
                'status': 'healthy',
                'db': str(db_path),
                'applied': False,
                'integrity': 'ok',
            }
        unexpected = [issue for issue in issues if not repairable_issue(issue)]
        if unexpected:
            raise RuntimeError(
                'Обнаружены неизвестные повреждения; автоматический ремонт отменён: '
                + '; '.join(unexpected)
            )
        missing_indexes = [
            index for index in TARGET_INDEXES
            if not connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",
                (index,),
            ).fetchone()
        ]
        if missing_indexes:
            raise RuntimeError(
                'Не найдены ожидаемые индексы: ' + ', '.join(missing_indexes)
            )
        result = {
            'status': 'ready' if not apply else 'repairing',
            'db': str(db_path),
            'applied': False,
            'issues': issues,
            'indexes': list(TARGET_INDEXES),
            'rows_before': _table_counts(connection),
        }
        if not apply:
            return result

        try:
            connection.execute('BEGIN EXCLUSIVE')
            connection.execute('ROLLBACK')
        except sqlite3.OperationalError as error:
            raise RuntimeError(
                'База используется другим процессом. Остановите bot и kpi_web.'
            ) from error

        backup_path = backup_database(db_path)
        for index in TARGET_INDEXES:
            connection.execute(f'REINDEX "{index}"')
        connection.commit()
        connection.execute('VACUUM')
        final_issues = integrity_issues(connection)
        if final_issues:
            raise RuntimeError(
                'После ремонта проверка не пройдена. Используйте резервную копию: '
                f'{backup_path}. Ошибки: ' + '; '.join(final_issues)
            )
        rows_after = _table_counts(connection)
        if rows_after != result['rows_before']:
            raise RuntimeError(
                'Количество строк изменилось. Используйте резервную копию: '
                f'{backup_path}'
            )
        result.update({
            'status': 'repaired',
            'applied': True,
            'integrity': 'ok',
            'backup': str(backup_path),
            'rows_after': rows_after,
        })
        return result
    finally:
        connection.close()


def build_parser():
    parser = argparse.ArgumentParser(
        description='Исправляет известные повреждения индексов SQLite',
    )
    parser.add_argument('--db', type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument(
        '--apply', action='store_true',
        help='Создать резервную копию и применить ремонт',
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        result = repair_sqlite_integrity(args.db, apply=args.apply)
    except (FileNotFoundError, RuntimeError, sqlite3.Error) as error:
        raise SystemExit(f'Ремонт БД не выполнен: {error}') from error
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result['status'] == 'ready':
        print('Изменений нет. Остановите bot и kpi_web, затем повторите с --apply.')


if __name__ == '__main__':
    main()
