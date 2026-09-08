import argparse
import html
import math
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from urllib.parse import urlsplit

import pytz
import requests

from constants import CHATS


BRONIX_BOT_API_URL = os.getenv(
    'BRONIX_BOT_API_URL',
    'https://bronix.omg-vr.ru/api/bot/bookings',
).strip()
BRONIX_BOT_API_TOKEN = os.getenv('BRONIX_BOT_API_TOKEN', '').strip()
BRONIX_HISTORY_START = os.getenv('BRONIX_HISTORY_START', '2019-01-01').strip()
BRONIX_PAGE_LIMIT = 1000
BRONIX_MAX_RANGE_DAYS = 401
BOOKING_SYNC_LOOKBACK_DAYS = 30
BOOKING_SYNC_FUTURE_DAYS = 365
BOOKING_LIVE_SYNC_DAYS = 7
BOOKING_FRESHNESS_MINUTES = 15
BOOKING_MIN_SNAPSHOT_RATIO = 0.5
BOOKING_DB_PATH = 'db/omgbot.sql'
BOOKING_CLUBS = (
    'Ленинский',
    'Марьино',
    'Каширка',
    'Прокшино',
    'Дмитровка',
)
MOSCOW = pytz.timezone('Europe/Moscow')
_sync_lock = threading.Lock()
_last_error_notification_at = 0.0


def notification_period(today):
    current_week_sunday = today + timedelta(days=6 - today.weekday())
    return today, current_week_sunday + timedelta(weeks=2)


def _numeric_value(value):
    try:
        return float(str(value or '0').replace(' ', '').replace(',', '.'))
    except ValueError:
        return 0.0


def _api_numeric_value(value, field):
    try:
        result = float(str(value).strip().replace(' ', '').replace(',', '.'))
    except (TypeError, ValueError) as error:
        raise RuntimeError(f'Bronix передала некорректное поле {field}') from error
    if not math.isfinite(result) or result < 0:
        raise RuntimeError(f'Bronix передала некорректное поле {field}')
    return result


def _parse_datetime(value, field):
    raw = str(value or '').strip()
    if not raw:
        raise RuntimeError(f'Bronix не передала {field}')
    try:
        parsed = datetime.fromisoformat(raw.replace('Z', '+00:00'))
    except ValueError as error:
        raise RuntimeError(f'Bronix передала некорректное поле {field}') from error
    if parsed.tzinfo is None:
        raise RuntimeError(f'Поле Bronix {field} должно содержать часовой пояс')
    return parsed.astimezone(MOSCOW)


def _canonical_booking(row):
    if not isinstance(row, dict):
        raise RuntimeError('Bronix вернула бронь в некорректном формате')
    required = (
        'id',
        'number',
        'startAt',
        'endAt',
        'status',
        'active',
        'club',
        'format',
        'isEvent',
        'participants',
        'paidAmount',
        'adminUrl',
    )
    missing = [field for field in required if field not in row]
    if missing:
        raise RuntimeError(
            'Bronix не передала обязательные поля: ' + ', '.join(missing)
        )

    booking_id = str(row['id'] or '').strip()
    number = str(row['number'] or '').strip()
    status = str(row['status'] or '').strip()
    club = str(row['club'] or '').strip()
    booking_format = str(row['format'] or '').strip()
    admin_url = str(row['adminUrl'] or '').strip()
    if not all((booking_id, number, status, club, booking_format, admin_url)):
        raise RuntimeError('Bronix передала пустое обязательное поле брони')
    if not isinstance(row['active'], bool) or not isinstance(row['isEvent'], bool):
        raise RuntimeError('Поля Bronix active и isEvent должны быть boolean')
    if urlsplit(admin_url).scheme not in {'http', 'https'}:
        raise RuntimeError('Bronix передала некорректный adminUrl')

    start_at = _parse_datetime(row['startAt'], 'startAt')
    end_at = _parse_datetime(row['endAt'], 'endAt')
    if end_at <= start_at:
        raise RuntimeError('Время окончания брони Bronix должно быть позже начала')
    participants = _api_numeric_value(row['participants'], 'participants')
    paid = _api_numeric_value(row['paidAmount'], 'paidAmount')
    if not participants.is_integer():
        raise RuntimeError('Bronix передала дробное количество гостей')

    return {
        'id': booking_id,
        'number': number,
        'reservation_at': start_at.isoformat(),
        'reservation_end_at': end_at.isoformat(),
        'date': start_at.date(),
        'status': status,
        'active': int(row['active']),
        'club': club,
        'booking_format': booking_format,
        'is_event': int(row['isEvent']),
        'participants': participants,
        'paid': paid,
        'admin_url': admin_url,
        'source': 'bronix',
        'source_present': 1,
        'url': admin_url,
    }


def _fetch_page(day_from, day_to, cursor=None):
    params = {
        'dateFrom': day_from.isoformat(),
        'dateTo': day_to.isoformat(),
        'limit': BRONIX_PAGE_LIMIT,
    }
    if cursor:
        params['cursor'] = cursor
    response = requests.get(
        BRONIX_BOT_API_URL,
        params=params,
        headers={
            'Authorization': f'Bearer {BRONIX_BOT_API_TOKEN}',
            'Accept': 'application/json',
        },
        timeout=30,
    )
    if response.status_code in {401, 403}:
        raise RuntimeError('Токен API Bronix отсутствует, неверен или отозван')
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError as error:
        raise RuntimeError('Bronix вернула ответ не в формате JSON') from error
    if not isinstance(payload, dict) or payload.get('ok') is not True:
        message = payload.get('error') if isinstance(payload, dict) else None
        raise RuntimeError(f'Ошибка API Bronix: {message or "некорректный ответ"}')
    rows = payload.get('bookings')
    if not isinstance(rows, list):
        raise RuntimeError('Bronix не вернула список бронирований')
    next_cursor = payload.get('nextCursor')
    if next_cursor is not None and not isinstance(next_cursor, str):
        raise RuntimeError('Bronix вернула некорректный nextCursor')
    return rows, next_cursor


def fetch_bookings_range(day_from, day_to):
    if day_from > day_to:
        raise ValueError('Начало периода Bronix не может быть позже конца')
    if not BRONIX_BOT_API_TOKEN:
        raise RuntimeError('Не задан BRONIX_BOT_API_TOKEN')

    rows_by_id = {}
    chunk_from = day_from
    while chunk_from <= day_to:
        chunk_to = min(
            chunk_from + timedelta(days=BRONIX_MAX_RANGE_DAYS - 1),
            day_to,
        )
        cursor = None
        seen_cursors = set()
        while True:
            rows, next_cursor = _fetch_page(chunk_from, chunk_to, cursor)
            for row in rows:
                booking_id = str(row.get('id') or '').strip()
                if not booking_id:
                    raise RuntimeError('Bronix вернула бронь без id')
                rows_by_id[booking_id] = row
            if not next_cursor:
                break
            if next_cursor in seen_cursors:
                raise RuntimeError('Bronix зациклила cursor пагинации')
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        chunk_from = chunk_to + timedelta(days=1)
    return list(rows_by_id.values())


def _table_exists(conn, table):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def _create_booking_schema(conn):
    conn.execute(
        '''CREATE TABLE IF NOT EXISTS booking_orders (
               booking_id TEXT PRIMARY KEY,
               booking_number TEXT NOT NULL,
               reservation_at TEXT NOT NULL,
               reservation_end_at TEXT NOT NULL,
               status TEXT NOT NULL,
               active INTEGER NOT NULL DEFAULT 1,
               club TEXT NOT NULL,
               booking_format TEXT NOT NULL,
               is_event INTEGER NOT NULL DEFAULT 0,
               participants REAL NOT NULL DEFAULT 0,
               paid REAL NOT NULL DEFAULT 0,
               admin_url TEXT NOT NULL,
               source TEXT NOT NULL DEFAULT 'bronix',
               source_present INTEGER NOT NULL DEFAULT 1,
               first_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
               last_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
               last_changed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
           )'''
    )
    conn.execute(
        '''CREATE INDEX IF NOT EXISTS idx_booking_orders_reservation_at
           ON booking_orders(reservation_at)'''
    )
    conn.execute(
        '''CREATE INDEX IF NOT EXISTS idx_booking_orders_payment_status
           ON booking_orders(paid, active)'''
    )
    conn.execute(
        '''CREATE INDEX IF NOT EXISTS idx_booking_orders_club_date
           ON booking_orders(club, reservation_at)'''
    )
    conn.execute(
        '''CREATE TABLE IF NOT EXISTS booking_order_history (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               booking_id TEXT NOT NULL,
               changed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
               field TEXT NOT NULL,
               old_value TEXT,
               new_value TEXT,
               FOREIGN KEY(booking_id) REFERENCES booking_orders(booking_id)
           )'''
    )
    conn.execute(
        '''CREATE INDEX IF NOT EXISTS idx_booking_order_history_booking
           ON booking_order_history(booking_id, changed_at)'''
    )
    conn.execute(
        '''CREATE TABLE IF NOT EXISTS booking_sync_state (
               key TEXT PRIMARY KEY,
               value TEXT NOT NULL,
               updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
           )'''
    )


def _migrate_legacy_tables(conn):
    if not _table_exists(conn, 'bukza_orders'):
        return
    if _sync_state(conn, 'legacy_booking_tables_migrated') == '1':
        return

    conn.execute(
        '''INSERT OR IGNORE INTO booking_orders (
               booking_id, booking_number, reservation_at,
               reservation_end_at, status, active, club, booking_format,
               is_event, participants, paid, admin_url, source,
               source_present, first_seen_at, last_seen_at, last_changed_at
           )
           SELECT 'legacy:' || order_id, order_number, reservation_at,
                  COALESCE(reservation_end_at, reservation_at), status,
                  CASE
                      WHEN source_present=0
                        OR status IN ('Техничка', 'Не пришел', 'Отменено')
                      THEN 0 ELSE 1
                  END,
                  COALESCE(club, ''), COALESCE(booking_format, resource, ''),
                  CASE
                      WHEN COALESCE(booking_format, resource, '') LIKE '%Мероприят%'
                      THEN 1 ELSE 0
                  END,
                  participants, paid,
                  'https://my.bukza.com/#/tables/order/' || order_id,
                  'legacy', source_present,
                  first_seen_at, last_seen_at, last_changed_at
           FROM bukza_orders
           WHERE reservation_at IS NOT NULL'''
    )
    if _table_exists(conn, 'bukza_order_history'):
        conn.execute(
            '''INSERT INTO booking_order_history (
                   booking_id, changed_at, field, old_value, new_value
               )
               SELECT 'legacy:' || order_id, changed_at, field,
                      old_value, new_value
               FROM bukza_order_history
               WHERE EXISTS (
                   SELECT 1 FROM booking_orders orders
                   WHERE orders.booking_id='legacy:' || bukza_order_history.order_id
               )'''
        )
    if _table_exists(conn, 'bukza_sync_state'):
        conn.execute(
            '''INSERT OR IGNORE INTO booking_sync_state (key, value, updated_at)
               SELECT key, value, updated_at
               FROM bukza_sync_state
               WHERE key IN (
                   'last_success_at', 'last_live_success_at',
                   'last_daily_success_at', 'last_full_success_at',
                   'last_range_from', 'last_range_to'
               )'''
        )
    _set_sync_state(conn, 'legacy_booking_tables_migrated', '1')
    if _table_exists(conn, 'bukza_order_history'):
        conn.execute('DROP TABLE bukza_order_history')
    if _table_exists(conn, 'bukza_orders'):
        conn.execute('DROP TABLE bukza_orders')
    if _table_exists(conn, 'bukza_sync_state'):
        conn.execute('DROP TABLE bukza_sync_state')


def initialize_booking_schema(db_path=BOOKING_DB_PATH):
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        conn.execute('BEGIN IMMEDIATE')
        _create_booking_schema(conn)
        _migrate_legacy_tables(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _sync_state(conn, key):
    row = conn.execute(
        'SELECT value FROM booking_sync_state WHERE key=?',
        (key,),
    ).fetchone()
    return row[0] if row else None


def _set_sync_state(conn, key, value):
    conn.execute(
        '''INSERT INTO booking_sync_state (key, value, updated_at)
           VALUES (?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(key) DO UPDATE SET
               value=excluded.value,
               updated_at=CURRENT_TIMESTAMP''',
        (key, str(value)),
    )


def _history_value(value):
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _store_bookings(
    bookings,
    day_from,
    day_to,
    initial_backfill,
    db_path=BOOKING_DB_PATH,
    sync_kind='daily',
    mark_missing=True,
):
    initialize_booking_schema(db_path)
    fields = (
        ('booking_number', 'number'),
        ('reservation_at', 'reservation_at'),
        ('reservation_end_at', 'reservation_end_at'),
        ('status', 'status'),
        ('active', 'active'),
        ('club', 'club'),
        ('booking_format', 'booking_format'),
        ('is_event', 'is_event'),
        ('participants', 'participants'),
        ('paid', 'paid'),
        ('admin_url', 'admin_url'),
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
            existing_in_range = conn.execute(
                '''SELECT COUNT(*) FROM booking_orders
                   WHERE date(reservation_at) BETWEEN ? AND ?
                     AND source='bronix' AND source_present=1''',
                (day_from.isoformat(), day_to.isoformat()),
            ).fetchone()[0]
            if (
                mark_missing
                and existing_in_range >= 10
                and len(bookings)
                < existing_in_range * BOOKING_MIN_SNAPSHOT_RATIO
            ):
                raise RuntimeError(
                    'Bronix вернула подозрительно неполный снимок: '
                    f'{len(bookings)} из ожидаемых примерно {existing_in_range}'
                )

            for booking in bookings:
                existing = conn.execute(
                    '''SELECT booking_number, reservation_at,
                              reservation_end_at, status, active, club,
                              booking_format, is_event, participants, paid,
                              admin_url, source_present
                       FROM booking_orders
                       WHERE booking_id=? AND source='bronix' ''',
                    (booking['id'],),
                ).fetchone()
                if existing is None:
                    conn.execute(
                        '''INSERT INTO booking_orders (
                               booking_id, booking_number, reservation_at,
                               reservation_end_at, status, active, club,
                               booking_format, is_event, participants, paid,
                               admin_url, source, source_present
                           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'bronix', ?)''',
                        (
                            booking['id'],
                            booking['number'],
                            booking['reservation_at'],
                            booking['reservation_end_at'],
                            booking['status'],
                            booking['active'],
                            booking['club'],
                            booking['booking_format'],
                            booking['is_event'],
                            booking['participants'],
                            booking['paid'],
                            booking['admin_url'],
                            booking['source_present'],
                        ),
                    )
                    conn.execute(
                        '''INSERT INTO booking_order_history (
                               booking_id, field, old_value, new_value
                           ) VALUES (?, 'created', NULL, ?)''',
                        (booking['id'], booking['number']),
                    )
                    inserted += 1
                    changes += 1
                    continue

                changed_fields = []
                for column, key in fields:
                    old_value = existing[column]
                    new_value = booking[key]
                    if old_value != new_value:
                        changed_fields.append((column, old_value, new_value))
                conn.execute(
                    '''UPDATE booking_orders
                       SET booking_number=?, reservation_at=?,
                           reservation_end_at=?, status=?, active=?, club=?,
                           booking_format=?, is_event=?, participants=?,
                           paid=?, admin_url=?, source_present=?,
                           last_seen_at=CURRENT_TIMESTAMP,
                           last_changed_at=CASE
                               WHEN ? THEN CURRENT_TIMESTAMP
                               ELSE last_changed_at
                           END
                       WHERE booking_id=? AND source='bronix' ''',
                    (
                        booking['number'],
                        booking['reservation_at'],
                        booking['reservation_end_at'],
                        booking['status'],
                        booking['active'],
                        booking['club'],
                        booking['booking_format'],
                        booking['is_event'],
                        booking['participants'],
                        booking['paid'],
                        booking['admin_url'],
                        booking['source_present'],
                        bool(changed_fields),
                        booking['id'],
                    ),
                )
                if not changed_fields:
                    unchanged += 1
                    continue
                conn.executemany(
                    '''INSERT INTO booking_order_history (
                           booking_id, field, old_value, new_value
                       ) VALUES (?, ?, ?, ?)''',
                    (
                        (
                            booking['id'],
                            field,
                            _history_value(old_value),
                            _history_value(new_value),
                        )
                        for field, old_value, new_value in changed_fields
                    ),
                )
                updated += 1
                changes += len(changed_fields)

            if mark_missing:
                conn.execute(
                    'CREATE TEMP TABLE IF NOT EXISTS booking_seen_ids '
                    '(booking_id TEXT PRIMARY KEY)'
                )
                conn.execute('DELETE FROM booking_seen_ids')
                conn.executemany(
                    'INSERT OR IGNORE INTO booking_seen_ids (booking_id) VALUES (?)',
                    ((booking['id'],) for booking in bookings),
                )
                missing = conn.execute(
                    '''SELECT COUNT(*) FROM booking_orders orders
                       WHERE date(orders.reservation_at) BETWEEN ? AND ?
                         AND orders.source='bronix'
                         AND orders.source_present=1
                         AND NOT EXISTS (
                             SELECT 1 FROM booking_seen_ids seen
                             WHERE seen.booking_id=orders.booking_id
                         )''',
                    (day_from.isoformat(), day_to.isoformat()),
                ).fetchone()[0]
                if missing:
                    conn.execute(
                        '''INSERT INTO booking_order_history (
                               booking_id, field, old_value, new_value
                           )
                           SELECT orders.booking_id, 'source_present', '1', '0'
                           FROM booking_orders orders
                           WHERE date(orders.reservation_at) BETWEEN ? AND ?
                             AND orders.source='bronix'
                             AND orders.source_present=1
                             AND NOT EXISTS (
                                 SELECT 1 FROM booking_seen_ids seen
                                 WHERE seen.booking_id=orders.booking_id
                             )''',
                        (day_from.isoformat(), day_to.isoformat()),
                    )
                    conn.execute(
                        '''UPDATE booking_orders
                           SET source_present=0, active=0,
                               last_changed_at=CURRENT_TIMESTAMP
                           WHERE date(reservation_at) BETWEEN ? AND ?
                             AND source='bronix' AND source_present=1
                             AND NOT EXISTS (
                                 SELECT 1 FROM booking_seen_ids seen
                                 WHERE seen.booking_id=booking_orders.booking_id
                             )''',
                        (day_from.isoformat(), day_to.isoformat()),
                    )
                    changes += missing

            if initial_backfill:
                conn.execute(
                    '''UPDATE booking_orders
                       SET active=0, source_present=0,
                           last_changed_at=CURRENT_TIMESTAMP
                       WHERE source='legacy' AND source_present=1'''
                )
            synced_at = datetime.now(MOSCOW).isoformat()
            _set_sync_state(conn, 'last_success_at', synced_at)
            _set_sync_state(conn, f'last_{sync_kind}_success_at', synced_at)
            _set_sync_state(conn, 'last_range_from', day_from.isoformat())
            _set_sync_state(conn, 'last_range_to', day_to.isoformat())
            _set_sync_state(conn, 'last_received_bookings', len(bookings))
            if initial_backfill:
                _set_sync_state(conn, 'initial_backfill_complete', '1')
    finally:
        conn.close()

    return {
        'received': len(bookings),
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


def sync_bronix_bookings(
    today=None,
    db_path=BOOKING_DB_PATH,
    mode='daily',
    force_full=False,
):
    today = today or datetime.now(MOSCOW).date()
    initialize_booking_schema(db_path)
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        initial_backfill = _sync_state(
            conn,
            'initial_backfill_complete',
        ) != '1'
    finally:
        conn.close()

    if mode not in {'daily', 'live'}:
        raise ValueError('Неизвестный режим синхронизации Bronix')
    if initial_backfill or force_full:
        try:
            day_from = datetime.strptime(
                BRONIX_HISTORY_START,
                '%Y-%m-%d',
            ).date()
        except ValueError as error:
            raise RuntimeError(
                'BRONIX_HISTORY_START должен иметь формат YYYY-MM-DD'
            ) from error
        sync_kind = 'full'
    elif mode == 'live':
        day_from = today
        sync_kind = 'live'
    else:
        day_from = today - timedelta(days=BOOKING_SYNC_LOOKBACK_DAYS)
        sync_kind = 'daily'
    day_to = today + timedelta(
        days=(
            BOOKING_LIVE_SYNC_DAYS
            if mode == 'live' and not initial_backfill and not force_full
            else BOOKING_SYNC_FUTURE_DAYS
        )
    )

    rows = fetch_bookings_range(day_from, day_to)
    bookings = [_canonical_booking(row) for row in rows]
    outside_range = [
        booking['id']
        for booking in bookings
        if not day_from <= booking['date'] <= day_to
    ]
    if outside_range:
        raise RuntimeError(
            'Bronix вернула брони вне запрошенного периода: '
            + ', '.join(outside_range[:5])
        )
    return _store_bookings(
        bookings,
        day_from,
        day_to,
        initial_backfill,
        db_path,
        sync_kind=sync_kind,
    )


def load_bookings(day_from, day_to, db_path=BOOKING_DB_PATH):
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            '''SELECT booking_id, booking_number, reservation_at,
                      reservation_end_at, status, active, club,
                      booking_format, is_event, participants, paid,
                      admin_url, source, source_present
               FROM booking_orders
               WHERE date(reservation_at) BETWEEN ? AND ?
               ORDER BY reservation_at, booking_number''',
            (day_from.isoformat(), day_to.isoformat()),
        ).fetchall()
    finally:
        conn.close()
    result = []
    for row in rows:
        reservation_at = datetime.fromisoformat(row['reservation_at'])
        result.append({
            'id': row['booking_id'],
            'number': row['booking_number'],
            'reservation_at': row['reservation_at'],
            'reservation_end_at': row['reservation_end_at'],
            'date': reservation_at.date(),
            'status': row['status'],
            'active': bool(row['active']),
            'club': row['club'],
            'booking_format': row['booking_format'],
            'is_event': bool(row['is_event']),
            'participants': row['participants'],
            'paid': row['paid'],
            'source': row['source'],
            'source_present': bool(row['source_present']),
            'url': row['admin_url'],
        })
    return result


def _active_booking(booking):
    return bool(booking.get('source_present', True) and booking.get('active'))


def active_bookings_for_day(day, clubs=None, db_path=BOOKING_DB_PATH):
    allowed_clubs = None if clubs is None else set(clubs)
    return [
        booking
        for booking in load_bookings(day, day, db_path)
        if _active_booking(booking)
        and booking.get('club')
        and (
            allowed_clubs is None
            or booking.get('club') in allowed_clubs
        )
    ]


def _moscow_datetime(value):
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        return MOSCOW.localize(parsed)
    return parsed.astimezone(MOSCOW)


def upcoming_unpaid_bookings(now=None, days=21, db_path=BOOKING_DB_PATH):
    now = now or datetime.now(MOSCOW)
    if now.tzinfo is None:
        now = MOSCOW.localize(now)
    day_to = now.date() + timedelta(days=days - 1)
    result = []
    for booking in load_bookings(now.date(), day_to, db_path):
        if not _active_booking(booking) or _numeric_value(booking['paid']) != 0:
            continue
        if _moscow_datetime(booking['reservation_at']) < now:
            continue
        result.append(booking)
    return result


def booking_freshness(now=None, db_path=BOOKING_DB_PATH):
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
        return {'last_synced_at': None, 'age_minutes': None, 'stale': True}
    last_synced_at = max(parsed_values)
    age_minutes = max(0, int((now - last_synced_at).total_seconds() // 60))
    return {
        'last_synced_at': last_synced_at.isoformat(),
        'age_minutes': age_minutes,
        'stale': age_minutes > BOOKING_FRESHNESS_MINUTES,
    }


def unpaid_weekend_bookings(bookings, day_from=None, day_to=None):
    result = {}
    for booking in bookings:
        if not _active_booking(booking):
            continue
        if _numeric_value(booking.get('paid')) != 0:
            continue
        reservation_day = booking.get('date')
        if reservation_day is None or reservation_day.weekday() not in {5, 6}:
            continue
        if day_from and reservation_day < day_from:
            continue
        if day_to and reservation_day > day_to:
            continue
        participants = _numeric_value(booking.get('participants'))
        if not booking.get('is_event') and participants < 5:
            continue
        booking_id = str(booking.get('id') or '').strip()
        if not booking_id:
            continue
        result[booking_id] = {
            'id': booking_id,
            'number': str(booking.get('number') or booking_id).strip(),
            'date': reservation_day,
            'url': booking.get('url'),
        }
    return sorted(
        result.values(),
        key=lambda item: (item['date'], item['number'], item['id']),
    )


def format_notification(bookings, day_from, day_to):
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
        for item in bookings
    )
    lines.extend([
        '',
        f'<i>Проверен период: {day_from:%d.%m.%Y} - {day_to:%d.%m.%Y}</i>',
    ])
    return '\n'.join(lines)


def _notify_sync_error(bot, error, always=False):
    global _last_error_notification_at
    print(f'Ошибка синхронизации Bronix: {error}')
    now = time.monotonic()
    if not always and now - _last_error_notification_at < 3600:
        return
    _last_error_notification_at = now
    try:
        bot.send_message(CHATS['me'], f'Ошибка синхронизации Bronix: {error}')
    except Exception as notification_error:
        print(f'Не удалось отправить ошибку Bronix владельцу: {notification_error}')


def run_bronix_sync(bot, mode='live', force_full=False):
    if not BRONIX_BOT_API_TOKEN:
        print('Синхронизация Bronix пропущена: не задан API-токен')
        return None
    if not _sync_lock.acquire(blocking=False):
        return False
    try:
        result = sync_bronix_bookings(mode=mode, force_full=force_full)
        print(
            f'Синхронизация Bronix завершена: режим={result["sync_kind"]}, '
            f'получено={result["received"]}, добавлено={result["inserted"]}, '
            f'обновлено={result["updated"]}, изменений={result["changes"]}'
        )
        return result
    except Exception as error:
        _notify_sync_error(bot, error, always=(mode == 'daily'))
        return None
    finally:
        _sync_lock.release()


def start_bronix_sync(bot, mode='live', force_full=False):
    thread = threading.Thread(
        target=run_bronix_sync,
        args=(bot, mode, force_full),
        name=f'bronix-{mode}-sync',
        daemon=True,
    )
    thread.start()
    return thread


def start_live_sync_if_active(bot, now=None):
    now = now or datetime.now(MOSCOW)
    if 8 <= now.hour <= 23:
        return start_bronix_sync(bot, mode='live')
    return None


def send_daily_notification(bot, today=None):
    if not CHATS.get('callcenter'):
        print('Проверка Bronix пропущена: не задан CHAT_CALLCENTER')
        return 0
    today = today or datetime.now(MOSCOW).date()
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
        bookings = unpaid_weekend_bookings(
            load_bookings(day_from, day_to),
            day_from,
            day_to,
        )
        if not bookings:
            return 0
        bot.send_message(
            CHATS['callcenter'],
            format_notification(bookings, day_from, day_to),
            parse_mode='HTML',
            disable_web_page_preview=True,
        )
        return len(bookings)
    except Exception as error:
        print(f'Ошибка ежедневной проверки Bronix: {error}')
        try:
            bot.send_message(
                CHATS['me'],
                f'Ошибка ежедневной проверки Bronix: {error}',
            )
        except Exception as notification_error:
            print(
                'Не удалось отправить ошибку Bronix владельцу: '
                f'{notification_error}'
            )
        return None


def send_test_notification(message, bot):
    result = send_daily_notification(bot)
    if result is None:
        bot.send_message(
            message.chat.id,
            '❌ Не удалось сформировать отчёт Bronix. Ошибка отправлена руководству.',
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
    if not BRONIX_BOT_API_TOKEN:
        raise SystemExit('Не задан BRONIX_BOT_API_TOKEN')
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--full',
        action='store_true',
        help='повторно загрузить всю доступную историю',
    )
    arguments = parser.parse_args()
    result = sync_bronix_bookings(force_full=arguments.full)
    print(
        f'Bronix sync complete mode={result["sync_kind"]} '
        f'received={result["received"]} inserted={result["inserted"]} '
        f'updated={result["updated"]} unchanged={result["unchanged"]} '
        f'changes={result["changes"]}'
    )
