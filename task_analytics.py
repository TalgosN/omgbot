import html
import re
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
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS task_comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL,
            actor_chatid TEXT,
            actor_login TEXT,
            actor_name TEXT
        )'''
    )
    conn.execute(
        '''CREATE INDEX IF NOT EXISTS idx_task_comments_task
           ON task_comments(task_id, created_at, id)'''
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


def add_task_comment(conn, task_id, message, actor=None, created_at=None):
    _create_schema(conn)
    current = created_at or datetime.now(MOSCOW)
    if current.tzinfo is None:
        current = current.replace(tzinfo=MOSCOW)
    actor = actor or {}
    cursor = conn.execute(
        '''INSERT INTO task_comments(
               task_id, message, created_at,
               actor_chatid, actor_login, actor_name
           ) VALUES (?, ?, ?, ?, ?, ?)''',
        (
            task_id,
            message,
            current.isoformat(timespec='seconds'),
            str(actor.get('chatid') or '') or None,
            str(actor.get('login') or '') or None,
            str(actor.get('name') or '') or None,
        ),
    )
    return cursor.lastrowid


def task_comments_payload(conn, task_id):
    _create_schema(conn)
    rows = conn.execute(
        '''SELECT id, message, created_at, actor_login, actor_name
           FROM task_comments WHERE task_id=? ORDER BY created_at, id''',
        (task_id,),
    ).fetchall()
    return [
        {
            'id': row['id'],
            'message': row['message'],
            'created_at': row['created_at'],
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


def _plain_feedback(value):
    text = re.sub(
        r'<br\s*/?>', '\n', _normalize_legacy_text(value), flags=re.IGNORECASE,
    )
    return html.unescape(re.sub(r'<[^>]+>', '', text)).strip()


def _final_solution(value):
    entries = [
        entry.strip() for entry in re.split(r'\n\s*\n', _plain_feedback(value))
        if entry.strip()
    ]
    for entry in reversed(entries):
        match = re.search(r'(?:Админ|Система):\s*(.+)', entry, flags=re.DOTALL)
        if match:
            return match.group(1).strip()
    return entries[-1] if entries else ''


def _first_feedback_response_date(value, created, latest):
    if not created:
        return None
    plain = _plain_feedback(value)
    matches = re.findall(
        r'\[(\d{1,2})\.(\d{1,2})\]\s*Админ:', plain,
        flags=re.IGNORECASE,
    )
    candidates = []
    last_year = (latest or created).year
    for day_value, month_value in matches:
        for year_value in range(created.year, last_year + 1):
            try:
                candidate = date(
                    year_value, int(month_value), int(day_value),
                )
            except ValueError:
                continue
            if candidate >= created and (latest is None or candidate <= latest):
                candidates.append(candidate)
    return min(candidates, default=None)


def _duration_value(start_time, end_time, start_date=None, end_date=None):
    if start_time and end_time and end_time >= start_time:
        return (end_time - start_time).total_seconds(), 'exact'
    if start_date and end_date and end_date >= start_date:
        return float((end_date - start_date).days * 86400), 'day'
    return None, 'none'


def _time_summary(rows, value_key, precision_key):
    values = [row[value_key] for row in rows if row[value_key] is not None]
    if not values:
        return None, 'none'
    precision = (
        'exact'
        if all(row[precision_key] == 'exact' for row in rows
               if row[value_key] is not None)
        else 'day'
    )
    return sum(values) / len(values), precision


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
        event_rows = conn.execute(
            '''SELECT task_id, event_type, event_at
               FROM task_events
               WHERE event_type IN ('created', 'solution', 'returned', 'confirmed')
               ORDER BY event_at, id'''
        ).fetchall()
    finally:
        conn.close()

    events = {}
    for event in event_rows:
        event_time = _parse_event_time(event['event_at'])
        if event_time:
            events.setdefault(int(event['task_id']), {}).setdefault(
                event['event_type'], [],
            ).append(event_time)

    all_rows = []
    for task in tasks:
        task_id = int(task['ID'])
        created = _parse_date(task['dtrep'])
        status = _canonical_status(task['status'])
        task_events = events.get(task_id, {})
        created_event = min(task_events.get('created', []), default=None)
        first_solution = min(task_events.get('solution', []), default=None)
        closed_event = max(task_events.get('confirmed', []), default=None)
        closed = (
            closed_event.astimezone(MOSCOW).date()
            if closed_event else _parse_date(task['dtfb'])
        ) if status == 'Выполнено' else None
        is_open = status in STATUS_LABELS[:2]
        created_in_period = _in_period(created, start, end)
        completed_in_period = (
            status == 'Выполнено' and _in_period(closed, start, end)
        )
        age_days = None
        if created:
            finish = closed if status == 'Выполнено' and closed else current.date()
            age_days = max((finish - created).days, 0)
        normalized_feedback = _normalize_legacy_text(task['feedback']).strip()
        feedback_response = _first_feedback_response_date(
            normalized_feedback, created, closed or current.date(),
        )
        first_response, first_response_precision = _duration_value(
            created_event,
            first_solution,
            start_date=created,
            end_date=feedback_response,
        )
        resolution, resolution_precision = _duration_value(
            created_event,
            closed_event,
            start_date=created,
            end_date=closed,
        )
        feedback_returns = len(re.findall(
            r'Сотрудник:', _plain_feedback(normalized_feedback), flags=re.IGNORECASE,
        ))
        all_rows.append({
            'id': task_id,
            'date': created.isoformat() if created else '',
            'closed_at': closed.isoformat() if closed else '',
            'type': _canonical_type(task['type']),
            'club': _normalize_legacy_text(task['club']).strip() or 'Без клуба',
            'title': _normalize_legacy_text(task['title']).strip(),
            'description': _normalize_legacy_text(task['desc']).strip(),
            'feedback': _normalize_legacy_text(task['feedback']).strip(),
            'final_solution': _final_solution(normalized_feedback),
            'status': status,
            'age_days': age_days,
            'first_response_seconds': first_response,
            'first_response_precision': first_response_precision,
            'resolution_seconds': resolution,
            'resolution_precision': resolution_precision,
            'return_count': max(
                len(task_events.get('returned', [])), feedback_returns,
            ),
            'is_backlog': is_open,
            'created_in_period': created_in_period,
            'completed_in_period': completed_in_period,
        })

    backlog = sorted(
        (row for row in all_rows if row['is_backlog']),
        key=lambda row: (row['club'], row['date'] or '9999-99-99', row['id']),
    )
    completed = sorted(
        (row for row in all_rows if row['completed_in_period']),
        key=lambda row: (
            row['club'],
            -(date.fromisoformat(row['closed_at']).toordinal()
              if row['closed_at'] else 0),
            -row['id'],
        ),
    )
    open_club_counts = {}
    for row in backlog:
        open_club_counts[row['club']] = open_club_counts.get(row['club'], 0) + 1
    closed_clubs = []
    for club in sorted({row['club'] for row in completed}):
        club_rows = [row for row in completed if row['club'] == club]
        response, response_precision = _time_summary(
            club_rows, 'first_response_seconds', 'first_response_precision',
        )
        resolution, resolution_precision = _time_summary(
            club_rows, 'resolution_seconds', 'resolution_precision',
        )
        closed_clubs.append({
            'label': club,
            'count': len(club_rows),
            'average_first_response_seconds': response,
            'first_response_precision': response_precision,
            'average_resolution_seconds': resolution,
            'resolution_precision': resolution_precision,
        })
    closed_clubs.sort(key=lambda item: (-item['count'], item['label']))
    average_response, response_precision = _time_summary(
        completed, 'first_response_seconds', 'first_response_precision',
    )
    average_resolution, resolution_precision = _time_summary(
        completed, 'resolution_seconds', 'resolution_precision',
    )
    return {
        'period': {
            'mode': mode,
            'value': month if mode == 'month' else year,
            'label': _report_period_label(mode, start, year),
        },
        'generated_at': current.isoformat(timespec='seconds'),
        'summary': {
            'created': sum(row['created_in_period'] for row in all_rows),
            'completed': len(completed),
            'work': sum(row['status'] == 'В работе' for row in backlog),
            'review': sum(row['status'] == 'На проверке' for row in backlog),
            'open': len(backlog),
            'average_first_response_seconds': average_response,
            'first_response_precision': response_precision,
            'average_resolution_seconds': average_resolution,
            'resolution_precision': resolution_precision,
        },
        'open_clubs': [
            {'label': label, 'open': count}
            for label, count in sorted(
                open_club_counts.items(), key=lambda item: (-item[1], item[0]),
            )
        ],
        'closed_clubs': closed_clubs,
        'backlog': backlog,
        'closed': completed,
        'rows': completed,
    }


def _report_duration(seconds, precision):
    if seconds is None:
        return 'нет данных'
    if precision == 'day' and float(seconds) == 0:
        return '≈ в тот же день'
    prefix = '≈ ' if precision == 'day' else ''
    minutes = max(round(seconds / 60), 0)
    if minutes < 1:
        return 'меньше минуты'
    if minutes < 60:
        return f'{prefix}{minutes} мин.'
    hours, remaining_minutes = divmod(minutes, 60)
    if hours < 24:
        return f'{prefix}{hours} ч {remaining_minutes} мин.'
    days, remaining_hours = divmod(hours, 24)
    return f'{prefix}{days} дн. {remaining_hours} ч'


def _html_value(value, limit=700):
    text = str(value or '').strip()
    result = []
    length = 0
    truncated = False
    for character in text:
        token = '<br>' if character == '\n' else html.escape(character)
        if length + len(token) > max(limit - 1, 0):
            truncated = True
            break
        result.append(token)
        length += len(token)
    if truncated:
        result.append('…')
    return ''.join(result)


def _report_date(value):
    try:
        return datetime.strptime(str(value or '')[:10], '%Y-%m-%d').strftime(
            '%d.%m.%Y'
        )
    except ValueError:
        return '—'


def format_task_report_html(report, message_limit=3800):
    summary = report['summary']
    open_clubs = ' · '.join(
        f"{_html_value(item['label'], 80)} — {item['open']}"
        for item in report['open_clubs']
    ) or 'нет'
    intro = (
        '🚩 <b>ОТЧЁТ ПО ДОСКЕ ПРОБЛЕМ</b>\n'
        f"📅 <b>Период:</b> {_html_value(report['period']['label'], 80)}\n\n"
        f"✅ <b>Закрыто:</b> {summary['completed']}\n"
        f"🆕 <b>Создано:</b> {summary['created']}\n"
        f"⚡ <b>Средний первый ответ:</b> "
        f"{_report_duration(summary['average_first_response_seconds'], summary['first_response_precision'])}\n"
        f"⏱ <b>Среднее полное решение:</b> "
        f"{_report_duration(summary['average_resolution_seconds'], summary['resolution_precision'])}\n\n"
        f"⚠️ <b>Осталось незакрытых:</b> {summary['open']} "
        f"· в работе {summary['work']} · на проверке {summary['review']}\n"
        f"📍 <i>{open_clubs}</i>"
    )
    chunks = [intro]
    if not report['closed']:
        chunks.append('✅ <b>ЗАКРЫТЫЕ ЗАЯВКИ</b>\n\nЗа выбранный период заявок нет.')
        return chunks

    tasks_by_club = {}
    for task in report['closed']:
        tasks_by_club.setdefault(task['club'], []).append(task)
    for meta in report['closed_clubs']:
        club = meta['label']
        tasks = tasks_by_club[club]
        heading = (
            f"📍 <b>{_html_value(club, 100)} · закрыто {meta['count']}</b>\n"
            f"<i>Первый ответ: {_report_duration(meta['average_first_response_seconds'], meta['first_response_precision'])} "
            f"· решение: {_report_duration(meta['average_resolution_seconds'], meta['resolution_precision'])}</i>"
        )
        current = heading
        for task in tasks:
            solution = _html_value(task['final_solution'] or 'Итог не записан')
            block = (
                f"\n\n✅ <b>#{task['id']} · {_html_value(task['title'], 120)}</b>\n"
                f"🏷 {_html_value(task['type'], 100)}\n"
                f"🗓 {_report_date(task['date'])} → "
                f"{_report_date(task['closed_at'])}\n"
                f"⚡ Первый ответ: {_report_duration(task['first_response_seconds'], task['first_response_precision'])}\n"
                f"⏱ Решение: {_report_duration(task['resolution_seconds'], task['resolution_precision'])}\n"
                f"↩️ Возвратов: {task['return_count']}\n"
                f"💬 <i>{solution}</i>"
            )
            if len(current) + len(block) > message_limit:
                chunks.append(current)
                current = f"{heading}\n<i>продолжение</i>{block}"
            else:
                current += block
        chunks.append(current)
    return chunks
