import sqlite3


DB_PATH = 'db/omgbot.sql'
TABLE_NAMES_VERSION = 1

USERS_COLUMNS = {
    'ID', 'login', 'first_name', 'second_name', 'nick_name',
    'bday', 'phone', 'email', 'status', 'chatid',
}
BROADCASTS_COLUMNS = {
    'id', 'text', 'photo', 'time', 'freq_type', 'freq_days', 'status',
}


def _tables(conn):
    return {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }


def _columns(conn, table):
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def _require_schema(conn, table, required):
    missing = required - _columns(conn, table)
    if missing:
        raise RuntimeError(
            f'Таблица {table} имеет неожиданную схему, отсутствуют: '
            f'{", ".join(sorted(missing))}'
        )


def migrate_table_names(db_path=DB_PATH):
    """Один раз заменяет legacy-таблицы актуальными таблицами без суффикса new."""
    conn = sqlite3.connect(db_path)
    try:
        version = conn.execute('PRAGMA user_version').fetchone()[0]
        if version >= TABLE_NAMES_VERSION:
            return []

        tables = _tables(conn)
        if 'users_new' in tables:
            _require_schema(conn, 'users_new', USERS_COLUMNS)
        elif 'users' in tables:
            _require_schema(conn, 'users', USERS_COLUMNS)

        if 'broadcasts_new' in tables:
            _require_schema(conn, 'broadcasts_new', BROADCASTS_COLUMNS)
        elif 'broadcasts' in tables:
            _require_schema(conn, 'broadcasts', BROADCASTS_COLUMNS)

        actions = []
        with conn:
            if 'users_new' in tables:
                if 'users' in tables:
                    conn.execute('DROP TABLE users')
                    actions.append('удалена legacy-таблица users')
                conn.execute('ALTER TABLE users_new RENAME TO users')
                actions.append('users_new переименована в users')

            if 'broadcasts_new' in tables:
                if 'broadcasts' in tables:
                    conn.execute('DROP TABLE broadcasts')
                    actions.append('удалена legacy-таблица broadcasts')
                conn.execute('ALTER TABLE broadcasts_new RENAME TO broadcasts')
                actions.append('broadcasts_new переименована в broadcasts')

            for table in ('admins', 'schema_migrations'):
                if table in tables:
                    conn.execute(f'DROP TABLE "{table}"')
                    actions.append(f'удалена legacy-таблица {table}')

            conn.execute(f'PRAGMA user_version = {TABLE_NAMES_VERSION}')
        return actions
    finally:
        conn.close()
