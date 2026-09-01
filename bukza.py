import argparse
import html
import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from urllib.parse import urlsplit, urlunsplit

import pytz
import requests

from constants import CHATS


BUKZA_EMAIL = os.getenv('BUKZA_EMAIL', '').strip()
BUKZA_PASSWORD = os.getenv('BUKZA_PASSWORD', '').strip()
BUKZA_ACCESS_TOKEN = os.getenv('BUKZA_ACCESS_TOKEN', '').strip()
BUKZA_SERVER_URL = os.getenv('BUKZA_SERVER_URL', '').strip().rstrip('/')
BUKZA_SIGNIN_FILE = os.getenv(
    'BUKZA_SIGNIN_FILE',
    'key/bukza-signin.private.json',
).strip()
BUKZA_TABLE_ID = int(os.getenv('BUKZA_TABLE_ID', '154891'))
BUKZA_PUBLIC_SERVER = os.getenv(
    'BUKZA_PUBLIC_SERVER',
    'https://public.bukza.com',
).rstrip('/')
BUKZA_HISTORY_START = os.getenv('BUKZA_HISTORY_START', '2019-01-01').strip()
BUKZA_API_ROW_LIMIT = 5000
BUKZA_SYNC_CHUNK_DAYS = 93
BUKZA_SYNC_LOOKBACK_DAYS = 30
BUKZA_SYNC_FUTURE_DAYS = 365
BUKZA_LIVE_SYNC_DAYS = 7
BUKZA_FRESHNESS_MINUTES = 15
BUKZA_MIN_SNAPSHOT_RATIO = 0.5
BUKZA_DB_PATH = 'db/omgbot.sql'
BUKZA_CLUB_CODES = {
    'ЛЕН': 'Ленинский',
    'МАР': 'Марьино',
    'КАШ': 'Каширка',
    'ПРО': 'Прокшино',
    'ДМИ': 'Дмитровка',
}
INACTIVE_STATUS_PARTS = ('технич', 'отмен', 'не пришел')
MOSCOW = pytz.timezone('Europe/Moscow')
_sync_lock = threading.Lock()
_last_error_notification_at = 0.0


def notification_period(today):
    current_week_sunday = today + timedelta(days=6 - today.weekday())
    return today, current_week_sunday + timedelta(weeks=2)


def _normalize_server_url(value):
    raw = str(value or '').strip()
    parsed = urlsplit(raw)
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
        raise RuntimeError('Bukza вернула некорректный serverUrl')
    path = parsed.path.rstrip('/')
    api_marker = path.casefold().find('/api/')
    if api_marker >= 0:
        path = path[:api_marker]
    return urlunsplit((parsed.scheme, parsed.netloc, path, '', '')).rstrip('/')


def _session_values(payload, source):
    if not isinstance(payload, dict):
        raise RuntimeError(f'{source} должен содержать JSON-объект')
    token = str(payload.get('token') or '').strip()
    server_url = str(payload.get('serverUrl') or '').strip()
    if not token or not server_url:
        raise RuntimeError(f'{source} должен содержать token и serverUrl')
    if token.lower().startswith('bearer '):
        token = token[7:].strip()
    return _normalize_server_url(server_url), token


def _file_session():
    if not BUKZA_SIGNIN_FILE or not os.path.isfile(BUKZA_SIGNIN_FILE):
        return None
    try:
        with open(BUKZA_SIGNIN_FILE, encoding='utf-8') as source:
            payload = json.load(source)
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(
            f'Не удалось прочитать файл сессии Bukza {BUKZA_SIGNIN_FILE}'
        ) from error
    return _session_values(payload, 'Файл сессии Bukza')


def _has_credentials():
    return bool(
        BUKZA_ACCESS_TOKEN
        or BUKZA_SERVER_URL
        or (BUKZA_SIGNIN_FILE and os.path.isfile(BUKZA_SIGNIN_FILE))
        or BUKZA_EMAIL
        or BUKZA_PASSWORD
    )


def _login():
    if BUKZA_ACCESS_TOKEN or BUKZA_SERVER_URL:
        if not BUKZA_ACCESS_TOKEN or not BUKZA_SERVER_URL:
            raise RuntimeError(
                'Для ручной сессии Bukza одновременно нужны '
                'BUKZA_ACCESS_TOKEN и BUKZA_SERVER_URL'
            )
        return _session_values({
            'token': BUKZA_ACCESS_TOKEN,
            'serverUrl': BUKZA_SERVER_URL,
        }, 'Ручная сессия Bukza')
    file_session = _file_session()
    if file_session:
        return file_session
    if not BUKZA_EMAIL or not BUKZA_PASSWORD:
        raise RuntimeError(
            'Для входа Bukza одновременно нужны BUKZA_EMAIL и BUKZA_PASSWORD'
        )

    response = requests.post(
        f'{BUKZA_PUBLIC_SERVER}/api/account/signin',
        json={'email': BUKZA_EMAIL, 'password': BUKZA_PASSWORD},
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    return _session_values(payload, 'Ответ авторизации Bukza')


def _fetch_reservations(server_url, token, day_from, day_to):
    response = requests.post(
        f'{server_url}/api/reservation-tables/data',
        json={
            'reservationTableId': BUKZA_TABLE_ID,
            'isLimited': False,
            'isTotalsLimited': False,
            'criteria': None,
            'from': f'{day_from.isoformat()}T00:00:00.000Z',
            'till': f'{(day_to + timedelta(days=1)).isoformat()}T00:00:00.000Z',
            'sortColumn': 'bukza_start_date',
            'isDescending': False,
        },
        headers={'Authorization': f'Bearer {token}'},
        timeout=30,
    )
    if response.status_code in {401, 403}:
        raise RuntimeError(
            'Сессия Bukza истекла или была отозвана. Войдите в Bukza '
            'вручную и обновите файл сессии или переменные '
            'BUKZA_ACCESS_TOKEN и BUKZA_SERVER_URL'
        )
    response.raise_for_status()
    rows = response.json().get('rows')
    if not isinstance(rows, list):
        raise RuntimeError('Bukza не вернула список бронирований')
    return rows


def fetch_reservations(day_from, day_to):
    server_url, token = _login()
    return _fetch_reservations(server_url, token, day_from, day_to)


def fetch_reservations_range(day_from, day_to):
    if day_from > day_to:
        raise ValueError('Начало периода Bukza не может быть позже конца')

    server_url, token = _login()
    rows_by_order = {}

    def fetch_chunk(chunk_from, chunk_to):
        rows = _fetch_reservations(
            server_url,
            token,
            chunk_from,
            chunk_to,
        )
        if len(rows) >= BUKZA_API_ROW_LIMIT and chunk_from < chunk_to:
            middle = chunk_from + (chunk_to - chunk_from) // 2
            fetch_chunk(chunk_from, middle)
            fetch_chunk(middle + timedelta(days=1), chunk_to)
            return
        if len(rows) >= BUKZA_API_ROW_LIMIT:
            raise RuntimeError(
                f'Лимит Bukza достигнут за один день: {chunk_from:%d.%m.%Y}'
            )
        for row in rows:
            order_id = str(row.get('orderId') or '').strip()
            if order_id:
                rows_by_order[order_id] = row

    chunk_from = day_from
    while chunk_from <= day_to:
        chunk_to = min(
            chunk_from + timedelta(days=BUKZA_SYNC_CHUNK_DAYS - 1),
            day_to,
        )
        fetch_chunk(chunk_from, chunk_to)
        chunk_from = chunk_to + timedelta(days=1)

    return list(rows_by_order.values())


def _row_values(row):
    values = {'_order_id': row.get('orderId')}
    for cell in row.get('cells') or []:
        key = str(cell.get('type') or '')
        values[key] = cell.get('value')
        values[f'{key}__formatted'] = cell.get('formatted')
    return values


def _normalized_text(value):
    return ' '.join(str(value or '').casefold().replace('ё', 'е').split())


def _numeric_value(value):
    try:
        return float(str(value or '0').replace(' ', '').replace(',', '.'))
    except ValueError:
        return 0.0


def _reservation_datetime(values, field):
    formatted = str(values.get(f'{field}__formatted') or '').strip()
    for pattern in ('%d.%m.%Y %H:%M:%S', '%d.%m.%Y %H:%M', '%d.%m.%Y'):
        try:
            return datetime.strptime(formatted, pattern)
        except ValueError:
            pass

    raw = str(values.get(field) or '').strip()
    if raw:
        try:
            return datetime.fromisoformat(raw.replace('Z', '+00:00'))
        except ValueError:
            pass
    return None


def _booking_date(values):
    booking_at = _reservation_datetime(values, 'bukza_start_date')
    return booking_at.date() if booking_at else None


def _resource_parts(resource):
    prefix, separator, booking_format = str(resource or '').partition('>')
    code = prefix.strip().upper()
    return (
        code,
        BUKZA_CLUB_CODES.get(code),
        booking_format.strip() if separator else str(resource or '').strip(),
    )


def _canonical_order(row):
    values = _row_values(row)
    booking_at = _reservation_datetime(values, 'bukza_start_date')
    booking_end_at = _reservation_datetime(values, 'bukza_end_date')
    order_id = str(values.get('_order_id') or '').strip()
    if not order_id:
        return None
    number = str(values.get('bukza_order_number') or order_id).strip()
    resource = str(values.get('bukza_resource_system_name') or '').strip()
    club_code, club, booking_format = _resource_parts(resource)
    return {
        'id': order_id,
        'number': number,
        'reservation_at': booking_at.isoformat() if booking_at else None,
        'reservation_end_at': (
            booking_end_at.isoformat() if booking_end_at else None
        ),
        'date': booking_at.date() if booking_at else None,
        'status': str(values.get('bukza_reservation_status') or '').strip(),
        'resource': resource,
        'club_code': club_code,
        'club': club,
        'booking_format': booking_format,
        'participants': _numeric_value(values.get('bukza_shares')),
        'paid': _numeric_value(values.get('bukza_paid')),
        'source_present': 1,
        'url': f'https://my.bukza.com/#/tables/order/{order_id}',
    }


def initialize_bukza_schema(db_path=BUKZA_DB_PATH):
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        with conn:
            conn.executescript(
                '''
                CREATE TABLE IF NOT EXISTS bukza_orders (
                    order_id TEXT PRIMARY KEY,
                    order_number TEXT NOT NULL,
                    reservation_at TEXT,
                    reservation_end_at TEXT,
                    status TEXT NOT NULL DEFAULT '',
                    resource TEXT NOT NULL DEFAULT '',
                    club_code TEXT,
                    club TEXT,
                    booking_format TEXT NOT NULL DEFAULT '',
                    participants REAL NOT NULL DEFAULT 0,
                    paid REAL NOT NULL DEFAULT 0,
                    source_present INTEGER NOT NULL DEFAULT 1,
                    first_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    last_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    last_changed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_bukza_orders_reservation_at
                    ON bukza_orders(reservation_at);
                CREATE INDEX IF NOT EXISTS idx_bukza_orders_payment_status
                    ON bukza_orders(paid, status);

                CREATE TABLE IF NOT EXISTS bukza_order_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id TEXT NOT NULL,
                    changed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    field TEXT NOT NULL,
                    old_value TEXT,
                    new_value TEXT,
                    FOREIGN KEY(order_id) REFERENCES bukza_orders(order_id)
                );
                CREATE INDEX IF NOT EXISTS idx_bukza_order_history_order
                    ON bukza_order_history(order_id, changed_at);

                CREATE TABLE IF NOT EXISTS bukza_sync_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                '''
            )
            columns = {
                row[1]
                for row in conn.execute('PRAGMA table_info(bukza_orders)')
            }
            migrations = {
                'reservation_end_at': 'TEXT',
                'club_code': 'TEXT',
                'club': 'TEXT',
                'booking_format': "TEXT NOT NULL DEFAULT ''",
                'source_present': 'INTEGER NOT NULL DEFAULT 1',
            }
            for column, definition in migrations.items():
                if column not in columns:
                    conn.execute(
                        f'ALTER TABLE bukza_orders ADD COLUMN {column} {definition}'
                    )
            conn.execute(
                '''CREATE INDEX IF NOT EXISTS idx_bukza_orders_club_date
                   ON bukza_orders(club, reservation_at)'''
            )
    finally:
        conn.close()


def _sync_state(conn, key):
    row = conn.execute(
        'SELECT value FROM bukza_sync_state WHERE key=?',
        (key,),
    ).fetchone()
    return row[0] if row else None


def _set_sync_state(conn, key, value):
    conn.execute(
        '''
        INSERT INTO bukza_sync_state (key, value, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE SET
            value=excluded.value,
            updated_at=CURRENT_TIMESTAMP
        ''',
        (key, str(value)),
    )


def _history_value(value):
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _store_orders(
    orders,
    day_from,
    day_to,
    initial_backfill,
    db_path=BUKZA_DB_PATH,
    sync_kind='daily',
    mark_missing=True,
):
    initialize_bukza_schema(db_path)
    fields = (
        ('order_number', 'number'),
        ('reservation_at', 'reservation_at'),
        ('reservation_end_at', 'reservation_end_at'),
        ('status', 'status'),
        ('resource', 'resource'),
        ('club_code', 'club_code'),
        ('club', 'club'),
        ('booking_format', 'booking_format'),
        ('participants', 'participants'),
        ('paid', 'paid'),
        ('source_present', 'source_present'),
    )
    inserted = 0
    updated = 0
    unchanged = 0
    changes = 0
    missing = 0

    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            operational_backfill = (
                sync_kind == 'full'
                and _sync_state(conn, 'operational_fields_backfilled') != '1'
            )
            existing_in_range = conn.execute(
                '''
                SELECT COUNT(*) FROM bukza_orders
                WHERE date(reservation_at) BETWEEN ? AND ?
                  AND source_present=1
                ''',
                (day_from.isoformat(), day_to.isoformat()),
            ).fetchone()[0]
            if (
                mark_missing
                and existing_in_range >= 10
                and len(orders) < existing_in_range * BUKZA_MIN_SNAPSHOT_RATIO
            ):
                raise RuntimeError(
                    'Bukza вернула подозрительно неполный снимок: '
                    f'{len(orders)} из ожидаемых примерно {existing_in_range}'
                )
            for order in orders:
                existing = conn.execute(
                    '''
                    SELECT order_number, reservation_at, reservation_end_at,
                           status, resource, club_code, club, booking_format,
                           participants, paid, source_present
                    FROM bukza_orders
                    WHERE order_id=?
                    ''',
                    (order['id'],),
                ).fetchone()
                if existing is None:
                    conn.execute(
                        '''
                        INSERT INTO bukza_orders (
                            order_id, order_number, reservation_at,
                            reservation_end_at, status, resource, club_code,
                            club, booking_format, participants, paid,
                            source_present
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''',
                        (
                            order['id'],
                            order['number'],
                            order['reservation_at'],
                            order['reservation_end_at'],
                            order['status'],
                            order['resource'],
                            order['club_code'],
                            order['club'],
                            order['booking_format'],
                            order['participants'],
                            order['paid'],
                            order['source_present'],
                        ),
                    )
                    conn.execute(
                        '''
                        INSERT INTO bukza_order_history (
                            order_id, field, old_value, new_value
                        ) VALUES (?, 'created', NULL, ?)
                        ''',
                        (order['id'], order['number']),
                    )
                    inserted += 1
                    changes += 1
                    continue

                changed_fields = []
                for column, key in fields:
                    old_value = existing[column]
                    new_value = order[key]
                    if old_value == new_value:
                        continue
                    changed_fields.append((column, old_value, new_value))

                conn.execute(
                    '''
                    UPDATE bukza_orders
                    SET order_number=?, reservation_at=?, reservation_end_at=?,
                        status=?, resource=?, club_code=?, club=?,
                        booking_format=?, participants=?, paid=?,
                        source_present=?, last_seen_at=CURRENT_TIMESTAMP,
                        last_changed_at=CASE
                            WHEN ? THEN CURRENT_TIMESTAMP
                            ELSE last_changed_at
                        END
                    WHERE order_id=?
                    ''',
                    (
                        order['number'],
                        order['reservation_at'],
                        order['reservation_end_at'],
                        order['status'],
                        order['resource'],
                        order['club_code'],
                        order['club'],
                        order['booking_format'],
                        order['participants'],
                        order['paid'],
                        order['source_present'],
                        bool(changed_fields),
                        order['id'],
                    ),
                )
                if not changed_fields:
                    unchanged += 1
                    continue
                for field, old_value, new_value in changed_fields:
                    if (
                        operational_backfill
                        and field in {
                            'reservation_end_at',
                            'club_code',
                            'club',
                            'booking_format',
                        }
                        and old_value in (None, '')
                    ):
                        continue
                    conn.execute(
                        '''
                        INSERT INTO bukza_order_history (
                            order_id, field, old_value, new_value
                        ) VALUES (?, ?, ?, ?)
                        ''',
                        (
                            order['id'],
                            field,
                            _history_value(old_value),
                            _history_value(new_value),
                        ),
                    )
                updated += 1
                changes += len(changed_fields)

            if mark_missing:
                conn.execute(
                    'CREATE TEMP TABLE IF NOT EXISTS bukza_seen_ids '
                    '(order_id TEXT PRIMARY KEY)'
                )
                conn.execute('DELETE FROM bukza_seen_ids')
                conn.executemany(
                    'INSERT OR IGNORE INTO bukza_seen_ids (order_id) VALUES (?)',
                    ((order['id'],) for order in orders),
                )
                missing = conn.execute(
                    '''
                    SELECT COUNT(*)
                    FROM bukza_orders orders
                    WHERE date(orders.reservation_at) BETWEEN ? AND ?
                      AND orders.source_present=1
                      AND NOT EXISTS (
                          SELECT 1 FROM bukza_seen_ids seen
                          WHERE seen.order_id=orders.order_id
                      )
                    ''',
                    (day_from.isoformat(), day_to.isoformat()),
                ).fetchone()[0]
                if missing:
                    conn.execute(
                        '''
                        INSERT INTO bukza_order_history (
                            order_id, field, old_value, new_value
                        )
                        SELECT orders.order_id, 'source_present', '1', '0'
                        FROM bukza_orders orders
                        WHERE date(orders.reservation_at) BETWEEN ? AND ?
                          AND orders.source_present=1
                          AND NOT EXISTS (
                              SELECT 1 FROM bukza_seen_ids seen
                              WHERE seen.order_id=orders.order_id
                          )
                        ''',
                        (day_from.isoformat(), day_to.isoformat()),
                    )
                    conn.execute(
                        '''
                        UPDATE bukza_orders
                        SET source_present=0,
                            last_changed_at=CURRENT_TIMESTAMP
                        WHERE date(reservation_at) BETWEEN ? AND ?
                          AND source_present=1
                          AND NOT EXISTS (
                              SELECT 1 FROM bukza_seen_ids seen
                              WHERE seen.order_id=bukza_orders.order_id
                          )
                        ''',
                        (day_from.isoformat(), day_to.isoformat()),
                    )
                    changes += missing

            synced_at = datetime.now(MOSCOW).isoformat()
            _set_sync_state(conn, 'last_success_at', synced_at)
            _set_sync_state(conn, f'last_{sync_kind}_success_at', synced_at)
            _set_sync_state(conn, 'last_range_from', day_from.isoformat())
            _set_sync_state(conn, 'last_range_to', day_to.isoformat())
            _set_sync_state(conn, 'last_received_orders', len(orders))
            if initial_backfill:
                _set_sync_state(conn, 'initial_backfill_complete', '1')
            if sync_kind == 'full':
                _set_sync_state(conn, 'operational_fields_backfilled', '1')
    finally:
        conn.close()

    return {
        'received': len(orders),
        'inserted': inserted,
        'updated': updated,
        'unchanged': unchanged,
        'changes': changes,
        'missing': missing,
        'initial_backfill': initial_backfill,
        'sync_kind': sync_kind,
        'date_from': day_from,
        'date_to': day_to,
    }


def sync_bukza_orders(
    today=None,
    db_path=BUKZA_DB_PATH,
    mode='daily',
    force_full=False,
):
    today = today or datetime.now(
        pytz.timezone('Europe/Moscow')
    ).date()
    initialize_bukza_schema(db_path)
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        initial_backfill = _sync_state(
            conn,
            'initial_backfill_complete',
        ) != '1'
    finally:
        conn.close()

    if mode not in {'daily', 'live'}:
        raise ValueError('Неизвестный режим синхронизации Bukza')

    if initial_backfill or force_full:
        try:
            day_from = datetime.strptime(
                BUKZA_HISTORY_START,
                '%Y-%m-%d',
            ).date()
        except ValueError as error:
            raise RuntimeError(
                'BUKZA_HISTORY_START должен иметь формат YYYY-MM-DD'
            ) from error
        sync_kind = 'full'
    elif mode == 'live':
        day_from = today
        sync_kind = 'live'
    else:
        day_from = today - timedelta(days=BUKZA_SYNC_LOOKBACK_DAYS)
        sync_kind = 'daily'
    day_to = today + timedelta(
        days=(
            BUKZA_LIVE_SYNC_DAYS
            if mode == 'live' and not initial_backfill and not force_full
            else BUKZA_SYNC_FUTURE_DAYS
        )
    )

    rows = fetch_reservations_range(day_from, day_to)
    orders_by_id = {}
    for row in rows:
        order = _canonical_order(row)
        if order:
            orders_by_id[order['id']] = order
    return _store_orders(
        list(orders_by_id.values()),
        day_from,
        day_to,
        initial_backfill,
        db_path,
        sync_kind=sync_kind,
    )


def load_orders(day_from, day_to, db_path=BUKZA_DB_PATH):
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            '''
            SELECT order_id, order_number, reservation_at,
                   reservation_end_at, status, resource, club_code, club,
                   booking_format, participants, paid, source_present
            FROM bukza_orders
            WHERE date(reservation_at) BETWEEN ? AND ?
            ORDER BY reservation_at, order_number
            ''',
            (day_from.isoformat(), day_to.isoformat()),
        ).fetchall()
    finally:
        conn.close()

    orders = []
    for row in rows:
        reservation_at = (
            datetime.fromisoformat(row['reservation_at'])
            if row['reservation_at']
            else None
        )
        orders.append({
            'id': row['order_id'],
            'number': row['order_number'],
            'reservation_at': row['reservation_at'],
            'reservation_end_at': row['reservation_end_at'],
            'date': reservation_at.date() if reservation_at else None,
            'status': row['status'],
            'resource': row['resource'],
            'club_code': row['club_code'],
            'club': row['club'],
            'booking_format': row['booking_format'],
            'participants': row['participants'],
            'paid': row['paid'],
            'source_present': bool(row['source_present']),
            'url': f'https://my.bukza.com/#/tables/order/{row["order_id"]}',
        })
    return orders


def _active_order(order):
    if not order.get('source_present', True):
        return False
    status = _normalized_text(order.get('status'))
    return not any(part in status for part in INACTIVE_STATUS_PARTS)


def active_orders_for_day(day, clubs=None, db_path=BUKZA_DB_PATH):
    allowed_clubs = None if clubs is None else set(clubs)
    return [
        order
        for order in load_orders(day, day, db_path)
        if _active_order(order)
        and order.get('club')
        and (allowed_clubs is None or order.get('club') in allowed_clubs)
    ]


def _moscow_datetime(value):
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        return MOSCOW.localize(parsed)
    return parsed.astimezone(MOSCOW)


def upcoming_unpaid_orders(now=None, days=21, db_path=BUKZA_DB_PATH):
    now = now or datetime.now(MOSCOW)
    if now.tzinfo is None:
        now = MOSCOW.localize(now)
    day_to = now.date() + timedelta(days=days - 1)
    result = []
    for order in load_orders(now.date(), day_to, db_path):
        if not _active_order(order) or _numeric_value(order.get('paid')) != 0:
            continue
        if not order.get('reservation_at'):
            continue
        if _moscow_datetime(order['reservation_at']) < now:
            continue
        result.append(order)
    return result


def booking_freshness(now=None, db_path=BUKZA_DB_PATH):
    now = now or datetime.now(MOSCOW)
    if now.tzinfo is None:
        now = MOSCOW.localize(now)
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        values = [
            _sync_state(conn, key)
            for key in (
                'last_live_success_at',
                'last_daily_success_at',
                'last_full_success_at',
            )
        ]
    finally:
        conn.close()

    parsed_values = []
    for value in values:
        if not value:
            continue
        try:
            parsed_values.append(_moscow_datetime(value))
        except ValueError:
            continue
    if not parsed_values:
        return {
            'last_synced_at': None,
            'age_minutes': None,
            'stale': True,
        }
    last_synced_at = max(parsed_values)
    age_minutes = max(0, int((now - last_synced_at).total_seconds() // 60))
    return {
        'last_synced_at': last_synced_at.isoformat(),
        'age_minutes': age_minutes,
        'stale': age_minutes > BUKZA_FRESHNESS_MINUTES,
    }


def unpaid_weekend_orders(rows, day_from=None, day_to=None):
    orders = {}
    for row in rows:
        order = _canonical_order(row) if 'cells' in row else row
        if not order:
            continue
        if not order.get('source_present', True):
            continue
        status = _normalized_text(order.get('status'))
        if any(part in status for part in INACTIVE_STATUS_PARTS):
            continue
        if _numeric_value(order.get('paid')) != 0:
            continue

        reservation_day = order.get('date')
        if reservation_day is None or reservation_day.weekday() not in {5, 6}:
            continue
        if day_from and reservation_day < day_from:
            continue
        if day_to and reservation_day > day_to:
            continue

        resource = _normalized_text(order.get('resource'))
        participants = _numeric_value(order.get('participants'))
        if 'мероприятие' not in resource and participants < 5:
            continue

        order_id = str(order.get('id') or '').strip()
        if not order_id:
            continue
        number = str(order.get('number') or order_id).strip()
        orders[order_id] = {
            'id': order_id,
            'number': number,
            'date': reservation_day,
            'url': f'https://my.bukza.com/#/tables/order/{order_id}',
        }

    return sorted(
        orders.values(),
        key=lambda item: (item['date'], item['number'], item['id']),
    )


def format_notification(orders, day_from, day_to):
    lines = [
        '⚠️ <b>Брони без предоплаты</b>',
        '',
        'Обратите внимание на следующие брони выходного дня:',
        '',
    ]
    lines.extend(
        f'• {item["date"]:%d.%m} | '
        f'<a href="{html.escape(item["url"], quote=True)}">'
        f'заказ №{html.escape(item["number"])}</a>'
        for item in orders
    )
    lines.extend([
        '',
        f'<i>Проверен период: {day_from:%d.%m.%Y} - {day_to:%d.%m.%Y}</i>',
    ])
    return '\n'.join(lines)


def _notify_sync_error(bot, error, always=False):
    global _last_error_notification_at
    print(f'Ошибка синхронизации Bukza: {error}')
    now = time.monotonic()
    if not always and now - _last_error_notification_at < 3600:
        return
    _last_error_notification_at = now
    try:
        bot.send_message(CHATS['me'], f'Ошибка синхронизации Bukza: {error}')
    except Exception as notification_error:
        print(f'Не удалось отправить ошибку Bukza владельцу: {notification_error}')


def run_bukza_sync(bot, mode='live', force_full=False):
    if not _has_credentials():
        print('Синхронизация Bukza пропущена: не заданы реквизиты')
        return None
    if not _sync_lock.acquire(blocking=False):
        return False
    try:
        return sync_bukza_orders(mode=mode, force_full=force_full)
    except Exception as error:
        _notify_sync_error(bot, error, always=(mode == 'daily'))
        return None
    finally:
        _sync_lock.release()


def start_bukza_sync(bot, mode='live', force_full=False):
    thread = threading.Thread(
        target=run_bukza_sync,
        args=(bot, mode, force_full),
        name=f'bukza-{mode}-sync',
        daemon=True,
    )
    thread.start()
    return thread


def start_live_sync_if_active(bot, now=None):
    now = now or datetime.now(MOSCOW)
    if 8 <= now.hour <= 23:
        return start_bukza_sync(bot, mode='live')
    return None


def send_daily_notification(bot, today=None):
    if not CHATS.get('callcenter'):
        print('Проверка Bukza пропущена: не задан CHAT_CALLCENTER')
        return 0

    today = today or datetime.now(
        MOSCOW
    ).date()
    try:
        freshness = booking_freshness()
        if freshness['stale']:
            age = freshness['age_minutes']
            details = (
                'успешной синхронизации ещё не было'
                if age is None
                else f'последнее обновление было {age} мин. назад'
            )
            raise RuntimeError(
                f'уведомление КЦ не отправлено: данные устарели, {details}'
            )
        day_from, day_to = notification_period(today)
        rows = load_orders(day_from, day_to)
        orders = unpaid_weekend_orders(rows, day_from, day_to)
        if not orders:
            return 0
        bot.send_message(
            CHATS['callcenter'],
            format_notification(orders, day_from, day_to),
            parse_mode='HTML',
            disable_web_page_preview=True,
        )
        return len(orders)
    except Exception as error:
        print(f'Ошибка ежедневной проверки Bukza: {error}')
        try:
            bot.send_message(
                CHATS['me'],
                f'Ошибка ежедневной проверки Bukza: {error}',
            )
        except Exception as notification_error:
            print(f'Не удалось отправить ошибку Bukza владельцу: {notification_error}')
        return None


def send_test_notification(message, bot):
    result = send_daily_notification(bot)
    if result is None:
        bot.send_message(
            message.chat.id,
            '❌ Не удалось сформировать отчёт Bukza. Ошибка отправлена руководству.',
        )
    elif result == 0:
        bot.send_message(
            message.chat.id,
            '✅ Проверка завершена. Подходящих броней без предоплаты нет.',
        )
    elif str(message.chat.id) != str(CHATS['callcenter']):
        bot.send_message(
            message.chat.id,
            f'✅ Отчёт отправлен в чат Коллцентра. Броней: {result}.',
        )
    return result


if __name__ == '__main__':
    if not _has_credentials():
        raise SystemExit('Не заданы реквизиты или ручная сессия Bukza')
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--full',
        action='store_true',
        help='повторно загрузить всю доступную историю',
    )
    arguments = parser.parse_args()
    result = sync_bukza_orders(force_full=arguments.full)
    mode = result['sync_kind']
    print(
        f'Bukza sync complete mode={mode} received={result["received"]} '
        f'inserted={result["inserted"]} updated={result["updated"]} '
        f'unchanged={result["unchanged"]} changes={result["changes"]}'
    )
