import html
import os
import sqlite3
from datetime import datetime, timedelta

import pytz
import requests

from constants import CHATS


BUKZA_EMAIL = os.getenv('BUKZA_EMAIL', '').strip()
BUKZA_PASSWORD = os.getenv('BUKZA_PASSWORD', '').strip()
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
BUKZA_DB_PATH = 'db/omgbot.sql'


def notification_period(today):
    current_week_sunday = today + timedelta(days=6 - today.weekday())
    return today, current_week_sunday + timedelta(weeks=2)


def _login():
    response = requests.post(
        f'{BUKZA_PUBLIC_SERVER}/api/account/signin',
        json={'email': BUKZA_EMAIL, 'password': BUKZA_PASSWORD},
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    token = str(payload.get('token') or '').strip()
    server_url = str(payload.get('serverUrl') or '').rstrip('/')
    if not token or not server_url:
        raise RuntimeError('Bukza не вернула token или serverUrl')
    return server_url, token


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


def _booking_datetime(values):
    formatted = str(values.get('bukza_start_date__formatted') or '').strip()
    for pattern in ('%d.%m.%Y %H:%M:%S', '%d.%m.%Y %H:%M', '%d.%m.%Y'):
        try:
            return datetime.strptime(formatted, pattern)
        except ValueError:
            pass

    raw = str(values.get('bukza_start_date') or '').strip()
    if raw:
        try:
            return datetime.fromisoformat(raw.replace('Z', '+00:00'))
        except ValueError:
            pass
    return None


def _booking_date(values):
    booking_at = _booking_datetime(values)
    return booking_at.date() if booking_at else None


def _canonical_order(row):
    values = _row_values(row)
    booking_at = _booking_datetime(values)
    order_id = str(values.get('_order_id') or '').strip()
    if not order_id:
        return None
    number = str(values.get('bukza_order_number') or order_id).strip()
    return {
        'id': order_id,
        'number': number,
        'reservation_at': booking_at.isoformat() if booking_at else None,
        'date': booking_at.date() if booking_at else None,
        'status': str(values.get('bukza_reservation_status') or '').strip(),
        'resource': str(values.get('bukza_resource_system_name') or '').strip(),
        'participants': _numeric_value(values.get('bukza_shares')),
        'paid': _numeric_value(values.get('bukza_paid')),
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
                    status TEXT NOT NULL DEFAULT '',
                    resource TEXT NOT NULL DEFAULT '',
                    participants REAL NOT NULL DEFAULT 0,
                    paid REAL NOT NULL DEFAULT 0,
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


def _store_orders(orders, day_from, day_to, initial_backfill, db_path=BUKZA_DB_PATH):
    initialize_bukza_schema(db_path)
    fields = (
        ('order_number', 'number'),
        ('reservation_at', 'reservation_at'),
        ('status', 'status'),
        ('resource', 'resource'),
        ('participants', 'participants'),
        ('paid', 'paid'),
    )
    inserted = 0
    updated = 0
    unchanged = 0
    changes = 0

    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            for order in orders:
                existing = conn.execute(
                    '''
                    SELECT order_number, reservation_at, status, resource,
                           participants, paid
                    FROM bukza_orders
                    WHERE order_id=?
                    ''',
                    (order['id'],),
                ).fetchone()
                if existing is None:
                    conn.execute(
                        '''
                        INSERT INTO bukza_orders (
                            order_id, order_number, reservation_at, status,
                            resource, participants, paid
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        ''',
                        (
                            order['id'],
                            order['number'],
                            order['reservation_at'],
                            order['status'],
                            order['resource'],
                            order['participants'],
                            order['paid'],
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
                    SET order_number=?, reservation_at=?, status=?, resource=?,
                        participants=?, paid=?, last_seen_at=CURRENT_TIMESTAMP,
                        last_changed_at=CASE
                            WHEN ? THEN CURRENT_TIMESTAMP
                            ELSE last_changed_at
                        END
                    WHERE order_id=?
                    ''',
                    (
                        order['number'],
                        order['reservation_at'],
                        order['status'],
                        order['resource'],
                        order['participants'],
                        order['paid'],
                        bool(changed_fields),
                        order['id'],
                    ),
                )
                if not changed_fields:
                    unchanged += 1
                    continue
                for field, old_value, new_value in changed_fields:
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

            _set_sync_state(conn, 'last_success_at', datetime.now().isoformat())
            _set_sync_state(conn, 'last_range_from', day_from.isoformat())
            _set_sync_state(conn, 'last_range_to', day_to.isoformat())
            _set_sync_state(conn, 'last_received_orders', len(orders))
            if initial_backfill:
                _set_sync_state(conn, 'initial_backfill_complete', '1')
    finally:
        conn.close()

    return {
        'received': len(orders),
        'inserted': inserted,
        'updated': updated,
        'unchanged': unchanged,
        'changes': changes,
        'initial_backfill': initial_backfill,
        'date_from': day_from,
        'date_to': day_to,
    }


def sync_bukza_orders(today=None, db_path=BUKZA_DB_PATH):
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

    if initial_backfill:
        try:
            day_from = datetime.strptime(
                BUKZA_HISTORY_START,
                '%Y-%m-%d',
            ).date()
        except ValueError as error:
            raise RuntimeError(
                'BUKZA_HISTORY_START должен иметь формат YYYY-MM-DD'
            ) from error
    else:
        day_from = today - timedelta(days=BUKZA_SYNC_LOOKBACK_DAYS)
    day_to = today + timedelta(days=BUKZA_SYNC_FUTURE_DAYS)

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
    )


def load_orders(day_from, day_to, db_path=BUKZA_DB_PATH):
    initialize_bukza_schema(db_path)
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            '''
            SELECT order_id, order_number, reservation_at, status, resource,
                   participants, paid
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
            'date': reservation_at.date() if reservation_at else None,
            'status': row['status'],
            'resource': row['resource'],
            'participants': row['participants'],
            'paid': row['paid'],
            'url': f'https://my.bukza.com/#/tables/order/{row["order_id"]}',
        })
    return orders


def unpaid_weekend_orders(rows, day_from=None, day_to=None):
    orders = {}
    for row in rows:
        order = _canonical_order(row) if 'cells' in row else row
        if not order:
            continue
        status = _normalized_text(order.get('status'))
        if 'технич' in status or 'отмен' in status or 'не пришел' in status:
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


def send_daily_notification(bot, today=None):
    if not BUKZA_EMAIL or not BUKZA_PASSWORD or not CHATS.get('callcenter'):
        print(
            'Проверка Bukza пропущена: не заданы BUKZA_EMAIL, '
            'BUKZA_PASSWORD или CHAT_CALLCENTER'
        )
        return 0

    today = today or datetime.now(
        pytz.timezone('Europe/Moscow')
    ).date()
    try:
        sync_bukza_orders(today)
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


if __name__ == '__main__':
    if not BUKZA_EMAIL or not BUKZA_PASSWORD:
        raise SystemExit('Не заданы BUKZA_EMAIL и BUKZA_PASSWORD')
    result = sync_bukza_orders()
    mode = 'initial' if result['initial_backfill'] else 'incremental'
    print(
        f'Bukza sync complete mode={mode} received={result["received"]} '
        f'inserted={result["inserted"]} updated={result["updated"]} '
        f'unchanged={result["unchanged"]} changes={result["changes"]}'
    )
