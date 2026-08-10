import sqlite3
from datetime import date, datetime
from statistics import median
from zoneinfo import ZoneInfo

from task_notifications import (
    BOT_TASK_TYPE,
    GENERAL_TASK_TYPE,
    LEGACY_GENERAL_TASK_TYPE,
    REPAIR_TASK_TYPE,
)


MOSCOW = ZoneInfo('Europe/Moscow')
EVENT_TYPES = {'created', 'solution', 'returned', 'confirmed'}
STATUS_LABELS = ('В работе', 'На проверке', 'Выполнено')
TYPE_ORDER = (REPAIR_TASK_TYPE, GENERAL_TASK_TYPE, BOT_TASK_TYPE)


def _create_schema(conn):
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS task_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            event_at TEXT NOT NULL
        )'''
    )
    conn.execute(
        '''CREATE INDEX IF NOT EXISTS idx_task_events_task
           ON task_events(task_id, event_type, event_at)'''
    )


def initialize_task_analytics_schema(db_path='db/omgbot.sql'):
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            _create_schema(conn)
    finally:
        conn.close()


def record_task_event(conn, task_id, event_type, event_at=None):
    if event_type not in EVENT_TYPES:
        raise ValueError('Неизвестное событие заявки')
    _create_schema(conn)
    current = event_at or datetime.now(MOSCOW)
    if current.tzinfo is None:
        current = current.replace(tzinfo=MOSCOW)
    conn.execute(
        '''INSERT INTO task_events(task_id, event_type, event_at)
           VALUES (?, ?, ?)''',
        (task_id, event_type, current.isoformat(timespec='seconds')),
    )


def _normalize_legacy_text(value):
    raw = str(value or '')
    for source, target in (('latin1', 'cp1251'), ('latin1', 'utf-8')):
        try:
            decoded = raw.encode(source).decode(target)
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if any('А' <= char <= 'я' or char == 'ё' for char in decoded):
            return decoded
    return raw


def _canonical_type(value):
    normalized = _normalize_legacy_text(value).strip()
    if normalized == LEGACY_GENERAL_TASK_TYPE:
        return GENERAL_TASK_TYPE
    return normalized or 'Без типа'


def _canonical_status(value):
    normalized = _normalize_legacy_text(value).strip()
    return 'Выполнено' if normalized == 'Архив' else normalized


def _parse_date(value):
    try:
        return date.fromisoformat(str(value or '')[:10])
    except ValueError:
        return None


def _parse_event_time(value):
    try:
        result = datetime.fromisoformat(str(value or ''))
    except ValueError:
        return None
    return result.replace(tzinfo=result.tzinfo or MOSCOW)


def _period(mode, month, year, now):
    if mode == 'month':
        try:
            start = datetime.strptime(month, '%Y-%m').date().replace(day=1)
        except (TypeError, ValueError) as error:
            raise ValueError('Месяц должен быть в формате YYYY-MM') from error
        next_month = (
            start.replace(year=start.year + 1, month=1)
            if start.month == 12 else start.replace(month=start.month + 1)
        )
        return start, next_month, start.strftime('%m.%Y')
    if mode == 'year':
        try:
            selected_year = int(year)
        except (TypeError, ValueError) as error:
            raise ValueError('Укажите корректный год') from error
        if selected_year < 2000 or selected_year > now.year + 1:
            raise ValueError('Укажите корректный год')
        return date(selected_year, 1, 1), date(selected_year + 1, 1, 1), str(selected_year)
    if mode == 'all':
        return None, None, 'Всё время'
    raise ValueError('Неизвестный период аналитики')


def _duration(task, events):
    created_event = events.get((task['ID'], 'created'))
    confirmed_event = events.get((task['ID'], 'confirmed'))
    if created_event and confirmed_event and confirmed_event >= created_event:
        return (confirmed_event - created_event).total_seconds(), True
    created_date = _parse_date(task['dtrep'])
    closed_date = _parse_date(task['dtfb'])
    if created_date and closed_date and closed_date >= created_date:
        return float((closed_date - created_date).days * 86400), False
    return None, False


def _duration_summary(items):
    values = [item[0] for item in items if item[0] is not None]
    if not values:
        return {
            'average_seconds': None,
            'median_seconds': None,
            'precision': 'none',
            'count': 0,
        }
    exact = all(item[1] for item in items if item[0] is not None)
    return {
        'average_seconds': sum(values) / len(values),
        'median_seconds': median(values),
        'precision': 'exact' if exact else 'day',
        'count': len(values),
    }


def _breakdown(rows, key, total):
    grouped = {}
    for item in rows:
        label = item[key]
        group = grouped.setdefault(label, {
            'label': label,
            'count': 0,
            'completed': 0,
            'open': 0,
            'durations': [],
        })
        group['count'] += 1
        if item['status'] == 'Выполнено':
            group['completed'] += 1
            group['durations'].append(item['duration'])
        else:
            group['open'] += 1
    result = []
    for group in grouped.values():
        duration = _duration_summary(group.pop('durations'))
        group.update({
            'share': group['count'] / total if total else 0,
            'average_seconds': duration['average_seconds'],
            'precision': duration['precision'],
        })
        result.append(group)
    return sorted(result, key=lambda item: (-item['count'], item['label']))


def build_task_analytics(db_path, mode='month', month=None, year=None, now=None):
    current = now or datetime.now(MOSCOW)
    month = month or current.strftime('%Y-%m')
    year = year or str(current.year)
    start, end, label = _period(mode, month, year, current)
    initialize_task_analytics_schema(db_path)

    where = ''
    params = []
    if start is not None:
        where = 'WHERE date(dtrep) >= date(?) AND date(dtrep) < date(?)'
        params = [start.isoformat(), end.isoformat()]
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        tasks = conn.execute(
            f'''SELECT ID, dtrep, type, club, title, status, dtfb
                FROM tasks {where} ORDER BY date(dtrep), ID''',
            params,
        ).fetchall()
        event_rows = conn.execute(
            f'''SELECT events.task_id, events.event_type, events.event_at
                FROM task_events events
                JOIN tasks ON tasks.ID=events.task_id
                {where}
                ORDER BY events.event_at''',
            params,
        ).fetchall()
    finally:
        conn.close()

    events = {}
    for event in event_rows:
        event_time = _parse_event_time(event['event_at'])
        if not event_time:
            continue
        key = (event['task_id'], event['event_type'])
        if event['event_type'] == 'created':
            events.setdefault(key, event_time)
        else:
            events[key] = event_time

    normalized = []
    for task in tasks:
        status = _canonical_status(task['status'])
        duration = _duration(task, events) if status == 'Выполнено' else (None, False)
        normalized.append({
            'id': int(task['ID']),
            'date': str(task['dtrep'] or '')[:10],
            'type': _canonical_type(task['type']),
            'club': _normalize_legacy_text(task['club']).strip() or 'Без клуба',
            'title': _normalize_legacy_text(task['title']).strip(),
            'status': status,
            'duration': duration,
        })

    total = len(normalized)
    status_counts = {
        status: sum(item['status'] == status for item in normalized)
        for status in STATUS_LABELS
    }
    completed = status_counts['Выполнено']
    durations = _duration_summary([
        item['duration'] for item in normalized if item['status'] == 'Выполнено'
    ])
    open_tasks = [item for item in normalized if item['status'] != 'Выполнено']
    dated_open = [
        (item, _parse_date(item['date'])) for item in open_tasks
        if _parse_date(item['date'])
    ]
    oldest = None
    if dated_open:
        item, opened = min(dated_open, key=lambda pair: pair[1])
        oldest = {
            'id': item['id'],
            'title': item['title'],
            'club': item['club'],
            'date': item['date'],
            'age_days': max((current.date() - opened).days, 0),
        }

    types = _breakdown(normalized, 'type', total)
    type_position = {label: index for index, label in enumerate(TYPE_ORDER)}
    types.sort(key=lambda item: (
        type_position.get(item['label'], len(type_position)), -item['count'],
    ))
    trend = []
    if mode == 'year':
        selected_year = int(year)
        for number in range(1, 13):
            monthly = [
                item for item in normalized
                if item['date'].startswith(f'{selected_year:04d}-{number:02d}')
            ]
            trend.append({
                'month': number,
                'created': len(monthly),
                'completed': sum(item['status'] == 'Выполнено' for item in monthly),
            })

    return {
        'period': {'mode': mode, 'value': month if mode == 'month' else year, 'label': label},
        'summary': {
            'created': total,
            'completed': completed,
            'completion_rate': completed / total if total else 0,
            'open': total - completed,
            **durations,
        },
        'statuses': [
            {
                'key': key,
                'label': label,
                'count': status_counts[label],
                'share': status_counts[label] / total if total else 0,
            }
            for key, label in (
                ('work', 'В работе'),
                ('review', 'На проверке'),
                ('done', 'Выполнено'),
            )
        ],
        'types': types,
        'clubs': _breakdown(normalized, 'club', total),
        'oldest_open': oldest,
        'trend': trend,
    }
