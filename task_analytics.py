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
REPORT_MONTH_NAMES = (
    'январь', 'февраль', 'март', 'апрель', 'май', 'июнь',
    'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь',
)


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
    columns = {
        row[1] for row in conn.execute('PRAGMA table_info(task_events)')
    }
    for name in ('actor_chatid', 'actor_login', 'actor_name'):
        if name not in columns:
            conn.execute(f'ALTER TABLE task_events ADD COLUMN {name} TEXT')
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


def task_actor_snapshot(user):
    if not user:
        return None

    def value(key):
        try:
            return user[key]
        except (IndexError, KeyError, TypeError):
            return None

    login = str(value('login') or '').strip()
    name = str(
        value('nick_name')
        or ' '.join(
            part for part in (
                str(value('first_name') or '').strip(),
                str(value('second_name') or '').strip(),
            ) if part
        )
        or login
        or 'Сотрудник'
    ).strip()
    return {
        'chatid': str(value('chatid') or '').strip(),
        'login': login,
        'name': name,
    }


def system_task_actor():
    return {'chatid': '', 'login': '', 'name': 'Система'}


def record_task_event(conn, task_id, event_type, event_at=None, actor=None):
    if event_type not in EVENT_TYPES:
        raise ValueError('Неизвестное событие заявки')
    _create_schema(conn)
    current = event_at or datetime.now(MOSCOW)
    if current.tzinfo is None:
        current = current.replace(tzinfo=MOSCOW)
    actor = actor or {}
    conn.execute(
        '''INSERT INTO task_events(
               task_id, event_type, event_at,
               actor_chatid, actor_login, actor_name
           ) VALUES (?, ?, ?, ?, ?, ?)''',
        (
            task_id,
            event_type,
            current.isoformat(timespec='seconds'),
            str(actor.get('chatid') or '') or None,
            str(actor.get('login') or '') or None,
            str(actor.get('name') or '') or None,
        ),
    )


def task_activity_payload(conn, task_id):
    _create_schema(conn)
    rows = conn.execute(
        '''SELECT event_type, event_at, actor_login, actor_name
           FROM task_events WHERE task_id=? ORDER BY event_at, id''',
        (task_id,),
    ).fetchall()
    return [
        {
            'event_type': row['event_type'],
            'event_at': row['event_at'],
            'actor': (
                {
                    'name': row['actor_name'],
                    'login': row['actor_login'],
                }
                if row['actor_name'] or row['actor_login'] else None
            ),
        }
        for row in rows
    ]


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


def _in_period(value, start, end):
    if value is None:
        return False
    return start is None or start <= value < end


def _report_period_label(mode, start, year):
    if mode == 'month':
        return f'{REPORT_MONTH_NAMES[start.month - 1]} {start.year}'
    if mode == 'year':
        return str(year)
    return 'всё время'


def build_task_report(db_path, mode='month', month=None, year=None, now=None):
    """Builds one dataset for text and Excel management reports."""
    current = now or datetime.now(MOSCOW)
    month = month or current.strftime('%Y-%m')
    year = year or str(current.year)
    start, end, _label = _period(mode, month, year, current)
    initialize_task_analytics_schema(db_path)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        tasks = conn.execute(
            '''SELECT ID, dtrep, type, club, title, desc, status, dtfb,
                      feedback
               FROM tasks ORDER BY date(dtrep), ID'''
        ).fetchall()
        confirmed_rows = conn.execute(
            '''SELECT task_id, MAX(event_at) AS event_at
               FROM task_events WHERE event_type='confirmed'
               GROUP BY task_id'''
        ).fetchall()
    finally:
        conn.close()

    confirmed_at = {
        int(row['task_id']): _parse_event_time(row['event_at'])
        for row in confirmed_rows
    }
    rows = []
    for task in tasks:
        task_id = int(task['ID'])
        created = _parse_date(task['dtrep'])
        status = _canonical_status(task['status'])
        closed_event = confirmed_at.get(task_id)
        closed = (
            closed_event.astimezone(MOSCOW).date()
            if closed_event else _parse_date(task['dtfb'])
        ) if status == 'Выполнено' else None
        is_open = status in STATUS_LABELS[:2]
        created_in_period = _in_period(created, start, end)
        completed_in_period = (
            status == 'Выполнено' and _in_period(closed, start, end)
        )
        if not (is_open or created_in_period or completed_in_period):
            continue
        age_days = None
        if created:
            finish = closed if status == 'Выполнено' and closed else current.date()
            age_days = max((finish - created).days, 0)
        rows.append({
            'id': task_id,
            'date': created.isoformat() if created else '',
            'closed_at': closed.isoformat() if closed else '',
            'type': _canonical_type(task['type']),
            'club': _normalize_legacy_text(task['club']).strip() or 'Без клуба',
            'title': _normalize_legacy_text(task['title']).strip(),
            'description': _normalize_legacy_text(task['desc']).strip(),
            'feedback': _normalize_legacy_text(task['feedback']).strip(),
            'status': status,
            'age_days': age_days,
            'is_backlog': is_open,
            'created_in_period': created_in_period,
            'completed_in_period': completed_in_period,
        })

    backlog = sorted(
        (row for row in rows if row['is_backlog']),
        key=lambda row: (row['club'], row['date'] or '9999-99-99', row['id']),
    )
    club_counts = {}
    for row in backlog:
        club_counts[row['club']] = club_counts.get(row['club'], 0) + 1
    report_rows = sorted(
        rows,
        key=lambda row: (
            not row['is_backlog'], row['club'], row['date'] or '9999-99-99',
            row['id'],
        ),
    )
    return {
        'period': {
            'mode': mode,
            'value': month if mode == 'month' else year,
            'label': _report_period_label(mode, start, year),
        },
        'generated_at': current.isoformat(timespec='seconds'),
        'summary': {
            'created': sum(row['created_in_period'] for row in rows),
            'completed': sum(row['completed_in_period'] for row in rows),
            'work': sum(row['status'] == 'В работе' for row in backlog),
            'review': sum(row['status'] == 'На проверке' for row in backlog),
            'open': len(backlog),
        },
        'clubs': [
            {'label': label, 'open': count}
            for label, count in sorted(
                club_counts.items(), key=lambda item: (-item[1], item[0]),
            )
        ],
        'backlog': backlog,
        'rows': report_rows,
    }


def format_task_report_text(report):
    summary = report['summary']
    lines = [
        '🚩 ОТЧЁТ ПО ДОСКЕ ПРОБЛЕМ',
        f"Период: {report['period']['label']}",
        '',
        f"Создано за период: {summary['created']}",
        f"Выполнено за период: {summary['completed']}",
        f"Сейчас в работе: {summary['work']}",
        f"На проверке: {summary['review']}",
        f"Всего незакрытых: {summary['open']}",
    ]
    if report['clubs']:
        lines.extend(('', 'НЕЗАКРЫТЫЕ ПО КЛУБАМ'))
        lines.extend(
            f"{item['label']} — {item['open']}"
            for item in report['clubs']
        )
    if report['backlog']:
        lines.extend(('', 'НЕЗАКРЫТЫЕ ЗАЯВКИ'))
        current_club = None
        for task in report['backlog']:
            if task['club'] != current_club:
                current_club = task['club']
                lines.extend(('', current_club))
            age = (
                f" — {task['age_days']} дн."
                if task['age_days'] is not None else ''
            )
            lines.append(
                f"#{task['id']} {task['title']} · {task['status']}{age}"
            )
    else:
        lines.extend(('', 'Незакрытых заявок нет.'))
    return '\n'.join(lines)
