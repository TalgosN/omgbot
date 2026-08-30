import html
import json
import sqlite3
import threading
from collections import defaultdict
from datetime import date, datetime
from zoneinfo import ZoneInfo

from kpi_calculator import (
    PHYSICAL_KPI_CLUBS,
    calculate_monthly_kpi,
    initialize_kpi_calculation_schema,
)


DB_PATH = 'db/omgbot.sql'
MOSCOW = ZoneInfo('Europe/Moscow')
ACTIVE_STATUSES = {0, 1, 2, 3}
CATALOG_VERSION = '2'
CACHE_MAX_AGE_SECONDS = 15 * 60
_refresh_lock = threading.Lock()
TIERS = (
    {'level': 1, 'key': 'bronze', 'label': 'Бронза', 'icon': '🥉'},
    {'level': 2, 'key': 'silver', 'label': 'Серебро', 'icon': '🥈'},
    {'level': 3, 'key': 'gold', 'label': 'Золото', 'icon': '🥇'},
    {'level': 4, 'key': 'diamond', 'label': 'Алмаз', 'icon': '💎'},
)
TOURING_REQUIREMENTS = (
    {'clubs': 2, 'shifts': 3, 'label': '2 клуба · по 3 смены'},
    {'clubs': 3, 'shifts': 5, 'label': '3 клуба · по 5 смен'},
    {'clubs': 4, 'shifts': 10, 'label': '4 клуба · по 10 смен'},
    {'clubs': 5, 'shifts': 20, 'label': '5 клубов · по 20 смен'},
)
CATEGORIES = (
    ('career', 'Смены и стаж'),
    ('kpi', 'KPI и рейтинг'),
    ('metrics', 'Показатели KPI'),
    ('taskboard', 'Taskboard и ремонт'),
    ('shift', 'OMG Shift'),
    ('meta', 'Особая коллекция'),
)


def _achievement(key, category, title, description, thresholds, kind='count'):
    return {
        'key': key,
        'category': category,
        'title': title,
        'description': description,
        'thresholds': tuple(float(value) for value in thresholds),
        'kind': kind,
    }


ACHIEVEMENTS = (
    _achievement('shifts', 'career', 'Человек-смена', 'Смены за всё время', (50, 200, 500, 1000), 'shifts'),
    _achievement('hours', 'career', 'Счётчик моточасов', 'Часы на сменах', (300, 1200, 3000, 6000), 'hours'),
    _achievement('active_months', 'career', 'Старожил', 'Месяцы хотя бы с одной сменой', (6, 12, 18, 36), 'months'),
    _achievement('clubs', 'career', 'Гастролёр', 'Опыт работы в разных физических клубах', (2, 3, 4, 5), 'clubs'),
    _achievement('weekend_shifts', 'career', 'Уикенд-команда', 'Смены по субботам и воскресеньям', (30, 100, 200, 400), 'shifts'),
    _achievement('kpi_peak', 'kpi', 'Соточка', 'Лучший KPI за завершённый месяц', (1, 1.2, 1.5, 3), 'percent'),
    _achievement('kpi_streak', 'kpi', 'Стабильная машина', 'Месяцы подряд с KPI от 100%', (2, 3, 6, 12), 'months'),
    _achievement('podiums', 'kpi', 'На пьедестале', 'Завершённые месяцы в топ-3', (1, 3, 6, 12), 'months'),
    _achievement('first_places', 'kpi', 'Главный герой месяца', 'Первые места за завершённый месяц', (1, 3, 5, 10), 'months'),
    _achievement('universal_metrics', 'kpi', 'Швейцарский нож', 'Разные основные показатели в одном месяце', (3, 4, 5, 6), 'metrics'),
    _achievement('clean_streak', 'kpi', 'Чистая игра', 'Месяцы подряд без активных штрафов', (6, 12, 18, 36), 'months'),
    _achievement('stream_months', 'kpi', 'В эфире', 'Завершённые месяцы со стримом', (1, 3, 6, 12), 'months'),
    _achievement('custom_goals', 'kpi', 'Побочная миссия', 'Полностью выполненные дополнительные KPI-цели', (1, 3, 6, 12), 'count'),
    _achievement('reviews', 'metrics', 'Пятизвёздочный', 'Полученные отзывы', (10, 50, 100, 200), 'count'),
    _achievement('forms', 'metrics', 'Анкетолог', 'Зачётные анкеты по формуле KPI', (25, 100, 250, 500), 'count'),
    _achievement('extensions', 'metrics', 'Ещё часик?', 'Продления игр', (10, 50, 100, 200), 'count'),
    _achievement('certificates', 'metrics', 'Даритель впечатлений', 'Оформленные сертификаты', (5, 25, 75, 150), 'count'),
    _achievement('subscriptions', 'metrics', 'Абонемент на успех', 'Оформленные абонементы', (1, 5, 10, 20), 'count'),
    _achievement('initiatives', 'metrics', 'Есть идейка', 'Принятые инициативы', (1, 5, 10, 20), 'count'),
    _achievement('birthdays', 'metrics', 'Король праздника', 'Проведённые дни рождения', (10, 50, 100, 200), 'count'),
    _achievement('solved_tasks', 'taskboard', 'Решала', 'Заявки, решение которых подтвердили', (5, 25, 100, 200), 'count'),
    _achievement('solved_repairs', 'taskboard', 'Я починил', 'Подтверждённые решения ремонтов', (1, 10, 50, 100), 'count'),
    _achievement('first_try', 'taskboard', 'С первого раза', 'Решения без возврата в работу', (5, 25, 100, 200), 'count'),
    _achievement('fast_solutions', 'taskboard', 'До завтра', 'Решения в течение 24 часов', (1, 10, 50, 100), 'count'),
    _achievement('replacements', 'taskboard', 'Обновочка', 'Замены оборудования на новое', (1, 5, 15, 30), 'count'),
    _achievement('created_repairs', 'taskboard', 'Не прошёл мимо', 'Созданные ремонтные заявки', (1, 10, 50, 100), 'count'),
    _achievement('useful_repairs', 'taskboard', 'Точно в цель', 'Созданные ремонты, которые были выполнены', (1, 10, 30, 60), 'count'),
    _achievement('shift_reports', 'shift', 'По протоколу', 'Отправленные отчёты открытия и закрытия', (10, 50, 100, 200), 'count'),
    _achievement('punctual_opens', 'shift', 'Минута в минуту', 'Открытия без опоздания больше пяти минут', (10, 50, 100, 200), 'count'),
    _achievement('collector', 'meta', 'Коллекционер', 'Разные семейства с полученной бронзой', (8, 16, 24, 29), 'count'),
    _achievement('record_holder', 'meta', 'Рекордсмен', 'Категории, в которых установлен рекорд', (1, 3, 5, 10), 'count'),
)
ACHIEVEMENTS_BY_KEY = {item['key']: item for item in ACHIEVEMENTS}

RECORDS = (
    ('shifts', 'Больше всего смен', 'shifts'),
    ('monthly_shifts_peak', 'Больше всего смен за месяц', 'shifts'),
    ('hours', 'Больше всего часов', 'hours'),
    ('active_months', 'Больше всего активных месяцев', 'months'),
    ('clubs', 'Больше всего клубов', 'clubs'),
    ('weekend_shifts', 'Больше всего смен в выходные', 'shifts'),
    ('kpi_peak', 'Самый высокий KPI за месяц', 'percent'),
    ('first_places', 'Больше всего первых мест', 'months'),
    ('reviews', 'Больше всего отзывов', 'count'),
    ('forms', 'Больше всего зачётных анкет', 'count'),
    ('extensions', 'Больше всего продлений', 'count'),
    ('certificates', 'Больше всего сертификатов', 'count'),
    ('subscriptions', 'Больше всего абонементов', 'count'),
    ('initiatives', 'Больше всего инициатив', 'count'),
    ('birthdays', 'Больше всего дней рождения', 'count'),
    ('custom_goals', 'Больше всего выполненных допцелей', 'count'),
    ('solved_tasks', 'Больше всего решённых заявок', 'count'),
    ('solved_repairs', 'Больше всего решённых ремонтов', 'count'),
    ('created_repairs', 'Больше всего замеченных ремонтов', 'count'),
    ('useful_repairs', 'Больше всего полезных ремонтных заявок', 'count'),
    ('replacements', 'Больше всего замен оборудования', 'count'),
    ('shift_reports', 'Больше всего отчётов смены', 'count'),
    ('punctual_opens', 'Больше всего своевременных открытий', 'count'),
)


def initialize_records_schema(db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            conn.executescript(
                '''
                CREATE TABLE IF NOT EXISTS records_achievement_unlocks (
                    employee_login TEXT NOT NULL,
                    achievement_key TEXT NOT NULL,
                    tier INTEGER NOT NULL,
                    value REAL NOT NULL,
                    unlocked_at TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'progress',
                    notified_at TEXT,
                    PRIMARY KEY(employee_login, achievement_key, tier)
                );
                CREATE INDEX IF NOT EXISTS idx_records_unlock_notifications
                    ON records_achievement_unlocks(notified_at, unlocked_at);
                CREATE TABLE IF NOT EXISTS records_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS records_cache (
                    key TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                '''
            )
    finally:
        conn.close()


def _normalize_login(value):
    login = str(value or '').strip().lower()
    if login and not login.startswith('@'):
        login = f'@{login}'
    return login


def _table_exists(conn, table):
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone())


def _number(value):
    return float(value or 0)


def _display_number(value):
    numeric = float(value or 0)
    if abs(numeric - round(numeric)) < 0.005:
        return str(int(round(numeric)))
    return f'{numeric:.1f}'.rstrip('0').rstrip('.')


def format_value(value, kind):
    if kind == 'percent':
        return f'{round(float(value or 0) * 100)}%'
    suffixes = {
        'shifts': ' смен',
        'hours': ' ч',
        'months': ' мес.',
        'clubs': ' клубов',
        'metrics': ' показателей',
    }
    return f'{_display_number(value)}{suffixes.get(kind, "")}'


def _people(conn):
    result = {}
    name_logins = {}
    if not _table_exists(conn, 'users'):
        return result, name_logins
    rows = conn.execute(
        '''SELECT ID, login, first_name, second_name, nick_name, status
           FROM users WHERE login IS NOT NULL AND trim(login)<>''
           ORDER BY ID'''
    ).fetchall()
    for user_id, raw_login, first_name, second_name, nickname, status in rows:
        login = _normalize_login(raw_login)
        if not login:
            continue
        current = result.get(login)
        active = status in ACTIVE_STATUSES
        if current and current['active'] and not active:
            continue
        name = str(
            nickname
            or ' '.join(part for part in (first_name, second_name) if part)
            or raw_login
        ).strip()
        result[login] = {
            'login': login,
            'name': name,
            'status': status,
            'active': active,
            'user_id': user_id,
        }
        key = (
            str(second_name or '').strip().casefold(),
            str(first_name or '').strip().casefold(),
        )
        if any(key):
            previous = name_logins.get(key)
            if not previous or active:
                name_logins[key] = login
    return result, name_logins


def _new_stats():
    result = {item['key']: 0.0 for item in ACHIEVEMENTS}
    result['monthly_shifts_peak'] = 0.0
    result['club_shift_units'] = {}
    result['contexts'] = {}
    result['_active_months'] = set()
    result['_clubs'] = set()
    return result


def _ensure_person(people, stats, login):
    login = _normalize_login(login)
    if not login:
        return ''
    if login not in people:
        people[login] = {
            'login': login,
            'name': login,
            'status': None,
            'active': False,
            'user_id': None,
        }
    stats.setdefault(login, _new_stats())
    return login


def _add(stats, people, login, key, value=1):
    login = _ensure_person(people, stats, login)
    if login:
        stats[login][key] += _number(value)


def _collect_shifts(conn, people, name_logins, stats):
    if not _table_exists(conn, 'shifts'):
        return None
    rows = conn.execute(
        '''SELECT shift_login, shift_second_name, shift_first_name,
                  date(substr(dt_shift, 1, 10)), club, dur
           FROM shifts
           WHERE date(substr(dt_shift, 1, 10)) IS NOT NULL
             AND date(substr(dt_shift, 1, 10))<=date(?)''',
        (datetime.now(MOSCOW).date().isoformat(),),
    ).fetchall()
    earliest = None
    for raw_login, second_name, first_name, raw_date, club, duration in rows:
        login = _normalize_login(raw_login) or name_logins.get((
            str(second_name or '').strip().casefold(),
            str(first_name or '').strip().casefold(),
        ), '')
        login = _ensure_person(people, stats, login)
        if not login:
            continue
        try:
            shift_date = date.fromisoformat(raw_date)
        except (TypeError, ValueError):
            continue
        hours = _number(duration)
        units = hours / 6.0
        stats[login]['hours'] += hours
        stats[login]['shifts'] += units
        stats[login]['_active_months'].add(raw_date[:7])
        if club in PHYSICAL_KPI_CLUBS:
            stats[login]['_clubs'].add(club)
            stats[login]['club_shift_units'][club] = (
                stats[login]['club_shift_units'].get(club, 0.0) + units
            )
        if shift_date.weekday() >= 5:
            stats[login]['weekend_shifts'] += units
        earliest = min(earliest, shift_date) if earliest else shift_date
    for values in stats.values():
        values['active_months'] = float(len(values['_active_months']))
        values['clubs'] = float(len(values['_clubs']))
    return earliest


def _collect_direct_metrics(conn, people, stats):
    specs = (
        ('reviews', 'reviews', 'who', 'SUM(COALESCE(amount, 0))', ''),
        ('afterparty', 'extensions', 'who', 'COUNT(DISTINCT ID)', "WHERE COALESCE(status, '')<>'Отклонено'"),
        ('sert', 'certificates', 'who', 'COUNT(*)', ''),
        ('abik', 'subscriptions', 'who', 'COUNT(*)', ''),
        ('initiative', 'initiatives', 'who', 'COUNT(DISTINCT ID)', "WHERE COALESCE(status, '')<>'Отклонено'"),
        ('birthday', 'birthdays', 'who', 'COUNT(DISTINCT ID)', "WHERE COALESCE(status, '')<>'Отклонено'"),
    )
    for table, key, login_column, expression, where in specs:
        if not _table_exists(conn, table):
            continue
        rows = conn.execute(
            f'''SELECT lower({login_column}), {expression}
                FROM {table} {where} GROUP BY lower({login_column})'''
        ).fetchall()
        for login, value in rows:
            _add(stats, people, login, key, value)

    required = {'shifts', 'users', 'anketi'}
    if not all(_table_exists(conn, table) for table in required):
        return
    rows = conn.execute(
        '''WITH daily_staff AS (
               SELECT date(substr(sh.dt_shift, 1, 10)) shift_date,
                      sh.club,
                      COUNT(DISTINCT COALESCE(
                          NULLIF(lower(employee.login), ''),
                          NULLIF(lower(sh.shift_login), ''),
                          lower(sh.shift_second_name || ' ' || sh.shift_first_name)
                      )) staff_count
               FROM shifts sh
               LEFT JOIN users employee ON (
                   sh.shift_login IS NOT NULL
                   AND lower(sh.shift_login)=lower(employee.login)
               ) OR (
                   sh.shift_login IS NULL
                   AND sh.shift_second_name=employee.second_name
                   AND sh.shift_first_name=employee.first_name
               )
               GROUP BY date(substr(sh.dt_shift, 1, 10)), sh.club
           ), employee_days AS (
               SELECT lower(employee.login) login,
                      date(substr(sh.dt_shift, 1, 10)) shift_date,
                      sh.club
               FROM shifts sh
               JOIN users employee ON (
                   sh.shift_login IS NOT NULL
                   AND lower(sh.shift_login)=lower(employee.login)
               ) OR (
                   sh.shift_login IS NULL
                   AND sh.shift_second_name=employee.second_name
                   AND sh.shift_first_name=employee.first_name
               )
               GROUP BY lower(employee.login),
                        date(substr(sh.dt_shift, 1, 10)), sh.club
           )
           SELECT days.login,
                  SUM(COALESCE(forms.form_count, 0) * 1.0 / staff.staff_count)
           FROM employee_days days
           JOIN daily_staff staff
             ON staff.shift_date=days.shift_date AND staff.club=days.club
           LEFT JOIN (
               SELECT date(substr(dt_ank, 1, 10)) form_date,
                      club_ank, COUNT(DISTINCT ID) form_count
               FROM anketi
               GROUP BY date(substr(dt_ank, 1, 10)), club_ank
           ) forms ON forms.form_date=days.shift_date
                  AND forms.club_ank=days.club
           GROUP BY days.login'''
    ).fetchall()
    for login, value in rows:
        _add(stats, people, login, 'forms', value)


def _collect_taskboard(conn, people, stats):
    if not (_table_exists(conn, 'tasks') and _table_exists(conn, 'task_events')):
        return
    rows = conn.execute(
        '''SELECT tasks.ID, tasks.type, tasks.status,
                  events.event_type, events.event_at, events.actor_login
           FROM tasks
           JOIN task_events events ON events.task_id=tasks.ID
           ORDER BY tasks.ID, events.event_at, events.id'''
    ).fetchall()
    grouped = defaultdict(list)
    task_meta = {}
    for task_id, task_type, status, event_type, event_at, actor_login in rows:
        task_meta[task_id] = (task_type, status)
        grouped[task_id].append({
            'type': event_type,
            'at': event_at,
            'login': _normalize_login(actor_login),
        })
    for task_id, events in grouped.items():
        task_type, status = task_meta[task_id]
        created = next((event for event in events if event['type'] == 'created'), None)
        if task_type == 'Ремонт' and created and created['login']:
            _add(stats, people, created['login'], 'created_repairs')
            if status == 'Выполнено':
                _add(stats, people, created['login'], 'useful_repairs')
        if status != 'Выполнено':
            continue
        solutions = [event for event in events if event['type'] == 'solution' and event['login']]
        if not solutions:
            continue
        solution = solutions[-1]
        _add(stats, people, solution['login'], 'solved_tasks')
        if task_type == 'Ремонт':
            _add(stats, people, solution['login'], 'solved_repairs')
        if not any(event['type'] == 'returned' for event in events):
            _add(stats, people, solution['login'], 'first_try')
        if created:
            try:
                created_at = datetime.fromisoformat(str(created['at']))
                solved_at = datetime.fromisoformat(str(solution['at']))
            except ValueError:
                continue
            if 0 <= (solved_at - created_at).total_seconds() <= 86400:
                _add(stats, people, solution['login'], 'fast_solutions')

    if _table_exists(conn, 'equipment_events'):
        for login, count in conn.execute(
            '''SELECT lower(actor_login), COUNT(*) FROM equipment_events
               WHERE event_type='replaced' AND actor_login IS NOT NULL
               GROUP BY lower(actor_login)'''
        ):
            _add(stats, people, login, 'replacements', count)


def _collect_shift_reports(conn, people, stats):
    if not _table_exists(conn, 'shift_webapp_runs'):
        return
    rows = conn.execute(
        '''SELECT lower(login),
                  COUNT(*) reports,
                  SUM(CASE WHEN action='open' AND warning_sent_at IS NULL
                           THEN 1 ELSE 0 END) punctual
           FROM shift_webapp_runs
           WHERE completed_at IS NOT NULL
           GROUP BY lower(login)'''
    ).fetchall()
    for login, reports, punctual in rows:
        _add(stats, people, login, 'shift_reports', reports)
        _add(stats, people, login, 'punctual_opens', punctual)


def _previous_month_start(today=None):
    today = today or datetime.now(MOSCOW).date()
    current = today.replace(day=1)
    return date(current.year - 1, 12, 1) if current.month == 1 else date(
        current.year, current.month - 1, 1,
    )


def _next_month(value):
    return date(value.year + 1, 1, 1) if value.month == 12 else date(
        value.year, value.month + 1, 1,
    )


def _custom_goal_completed(goal):
    if goal.get('ratio') is not None:
        return float(goal.get('ratio') or 0) >= 1
    maximum = goal.get('max_contribution_pct')
    if maximum is not None:
        return float(goal.get('base_contribution_pct') or 0) >= float(maximum)
    return float(goal.get('fact') or 0) > 0


def _collect_monthly_kpi(db_path, people, stats, earliest):
    if not earliest or not people:
        return
    initialize_kpi_calculation_schema(db_path)
    month = earliest.replace(day=1)
    end = _previous_month_start()
    if month > end:
        return
    logins = list(people)
    kpi_streaks = defaultdict(int)
    clean_streaks = defaultdict(int)
    while month <= end:
        rows = calculate_monthly_kpi(
            month,
            db_path=db_path,
            employee_logins=logins,
            ensure_schema=False,
        )
        by_login = {row['login']: row for row in rows}
        eligible = [row for row in rows if float(row.get('shifts') or 0) >= 5]
        ranked = sorted(eligible, key=lambda row: float(row.get('total_pct') or 0), reverse=True)
        ranks = {}
        previous_value = None
        previous_rank = 0
        for index, row in enumerate(ranked, 1):
            value = float(row.get('total_pct') or 0)
            if previous_value is None or value != previous_value:
                previous_rank = index
                previous_value = value
            ranks[row['login']] = previous_rank
        for login in logins:
            row = by_login.get(login)
            qualifies = row and float(row.get('shifts') or 0) >= 5
            if not qualifies:
                kpi_streaks[login] = 0
                clean_streaks[login] = 0
                continue
            value = float(row.get('total_pct') or 0)
            monthly_shifts = float(row.get('shifts') or 0)
            if monthly_shifts > stats[login]['monthly_shifts_peak']:
                stats[login]['monthly_shifts_peak'] = monthly_shifts
                stats[login]['contexts']['monthly_shifts_peak'] = month.isoformat()[:7]
            if value > stats[login]['kpi_peak']:
                stats[login]['kpi_peak'] = value
                stats[login]['contexts']['kpi_peak'] = month.isoformat()[:7]
            kpi_streaks[login] = kpi_streaks[login] + 1 if value >= 1 else 0
            stats[login]['kpi_streak'] = max(
                stats[login]['kpi_streak'], kpi_streaks[login],
            )
            clean_streaks[login] = (
                clean_streaks[login] + 1
                if int(row.get('penalties') or 0) == 0 else 0
            )
            stats[login]['clean_streak'] = max(
                stats[login]['clean_streak'], clean_streaks[login],
            )
            rank = ranks.get(login)
            if rank and rank <= 3:
                stats[login]['podiums'] += 1
            if rank == 1:
                stats[login]['first_places'] += 1
            metric_count = sum(
                float(row.get(field) or 0) > 0
                for field in (
                    'reviews', 'forms', 'extensions',
                    'certificates', 'subscriptions', 'initiatives',
                )
            )
            stats[login]['universal_metrics'] = max(
                stats[login]['universal_metrics'], metric_count,
            )
            if row.get('stream'):
                stats[login]['stream_months'] += 1
            stats[login]['custom_goals'] += sum(
                _custom_goal_completed(goal)
                for goal in row.get('custom_goals', [])
            )
        month = _next_month(month)


def _level(value, thresholds):
    result = 0
    for index, threshold in enumerate(thresholds, 1):
        if float(value or 0) >= threshold:
            result = index
    return result


def _touring_level(values):
    shift_units = values.get('club_shift_units') or {}
    result = 0
    for index, requirement in enumerate(TOURING_REQUIREMENTS, 1):
        qualified = sum(
            float(units or 0) >= requirement['shifts']
            for units in shift_units.values()
        )
        if qualified >= requirement['clubs']:
            result = index
    return result


def _achievement_level(item, values):
    if item['key'] == 'clubs':
        return _touring_level(values)
    return _level(values[item['key']], item['thresholds'])


def _record_payloads(people, stats, active):
    records = []
    for key, title, kind in RECORDS:
        candidates = [
            (login, values[key]) for login, values in stats.items()
            if people[login]['active'] is active and float(values[key] or 0) > 0
        ]
        if not candidates:
            continue
        best = max(value for _login, value in candidates)
        holders = [
            {
                'login': login,
                'name': people[login]['name'],
            }
            for login, value in candidates
            if abs(float(value) - float(best)) < 0.0001
        ]
        context = stats[holders[0]['login']]['contexts'].get(key) if holders else None
        records.append({
            'key': key,
            'title': title,
            'value': best,
            'value_label': format_value(best, kind),
            'kind': kind,
            'context': context,
            'holders': holders,
        })
    return records


def calculate_records_state(db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    try:
        people, name_logins = _people(conn)
        stats = {login: _new_stats() for login in people}
        earliest = _collect_shifts(conn, people, name_logins, stats)
        _collect_direct_metrics(conn, people, stats)
        _collect_taskboard(conn, people, stats)
        _collect_shift_reports(conn, people, stats)
    finally:
        conn.close()
    _collect_monthly_kpi(db_path, people, stats, earliest)
    active_records = _record_payloads(people, stats, True)
    archive_records = _record_payloads(people, stats, False)
    for records in (active_records, archive_records):
        for record in records:
            for holder in record['holders']:
                stats[holder['login']]['record_holder'] += 1
    for values in stats.values():
        values['collector'] = sum(
            _achievement_level(item, values) > 0
            for item in ACHIEVEMENTS
            if item['category'] != 'meta'
        )
    return {
        'people': people,
        'stats': stats,
        'records': active_records,
        'archive_records': archive_records,
    }


def _achievement_levels(state):
    return {
        (login, item['key']): (
            _achievement_level(item, values),
            values[item['key']],
        )
        for login, values in state['stats'].items()
        for item in ACHIEVEMENTS
    }


def _cache_payload(state):
    return {
        'people': state['people'],
        'stats': {
            login: {
                key: value for key, value in values.items()
                if not key.startswith('_')
            }
            for login, values in state['stats'].items()
        },
        'records': state['records'],
        'archive_records': state['archive_records'],
    }


def _cached_records_state(db_path, max_age=CACHE_MAX_AGE_SECONDS):
    initialize_records_schema(db_path)
    conn = sqlite3.connect(db_path)
    try:
        version = conn.execute(
            "SELECT value FROM records_meta WHERE key='catalog_version'"
        ).fetchone()
        row = conn.execute(
            "SELECT payload_json, updated_at FROM records_cache WHERE key='dashboard'"
        ).fetchone()
    finally:
        conn.close()
    if not version or version[0] != CATALOG_VERSION or not row:
        return None
    try:
        updated_at = datetime.fromisoformat(row[1])
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=MOSCOW)
        if (datetime.now(MOSCOW) - updated_at).total_seconds() > max_age:
            return None
        return json.loads(row[0])
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _tier(level):
    return TIERS[level - 1] if 0 < level <= len(TIERS) else None


def _achievement_value_label(achievement, value, tier_number):
    if achievement['key'] == 'clubs' and 0 < tier_number <= len(
        TOURING_REQUIREMENTS
    ):
        return TOURING_REQUIREMENTS[tier_number - 1]['label']
    return format_value(value, achievement['kind'])


def _notification_text(items, state):
    lines = ['🏆 <b>OMG RECORDS · новое достижение</b>', '']
    for login, key, tier_number, value in items:
        person = state['people'][login]
        achievement = ACHIEVEMENTS_BY_KEY[key]
        tier = _tier(tier_number)
        identity = html.escape(person['name'])
        if login and login != person['name'].lower():
            identity += f' · {html.escape(login)}'
        lines.extend((
            f'{tier["icon"]} <b>{identity}</b>',
            f'{html.escape(achievement["title"])} · {tier["label"].lower()}',
            html.escape(_achievement_value_label(
                achievement, value, tier_number,
            )),
            '',
        ))
    return '\n'.join(lines).strip()


def refresh_records_achievements(
    db_path=DB_PATH,
    bot=None,
    main_chat_id=None,
):
    initialize_records_schema(db_path)
    state = calculate_records_state(db_path)
    levels = _achievement_levels(state)
    now = datetime.now(MOSCOW).isoformat(timespec='seconds')
    conn = sqlite3.connect(db_path)
    try:
        conn.execute('BEGIN IMMEDIATE')
        current_version = conn.execute(
            "SELECT value FROM records_meta WHERE key='catalog_version'"
        ).fetchone()
        baseline = not current_version or current_version[0] != CATALOG_VERSION
        if baseline:
            conn.execute('DELETE FROM records_achievement_unlocks')
        conn.execute(
            '''INSERT INTO records_cache(key, payload_json, updated_at)
               VALUES ('dashboard', ?, ?)
               ON CONFLICT(key) DO UPDATE SET
                   payload_json=excluded.payload_json,
                   updated_at=excluded.updated_at''',
            (json.dumps(_cache_payload(state), ensure_ascii=False), now),
        )
        for (login, key), (level, value) in levels.items():
            active = state['people'][login]['active']
            for tier_number in range(1, level + 1):
                conn.execute(
                    '''INSERT OR IGNORE INTO records_achievement_unlocks(
                           employee_login, achievement_key, tier, value,
                           unlocked_at, source, notified_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?)''',
                    (
                        login, key, tier_number, value, now,
                        'baseline' if baseline else 'progress',
                        now if baseline or not active else None,
                    ),
                )
        conn.execute(
            '''INSERT INTO records_meta(key, value, updated_at)
               VALUES ('catalog_version', ?, ?)
               ON CONFLICT(key) DO UPDATE SET
                   value=excluded.value, updated_at=excluded.updated_at''',
            (CATALOG_VERSION, now),
        )
        conn.commit()

        if baseline or not bot or not main_chat_id:
            return state
        pending_rows = conn.execute(
            '''SELECT employee_login, achievement_key, tier, value
               FROM records_achievement_unlocks
               WHERE notified_at IS NULL
               ORDER BY unlocked_at, employee_login, achievement_key, tier
               LIMIT 500'''
        ).fetchall()
        pending = {}
        for login, key, tier_number, value in pending_rows:
            if login not in state['people'] or not state['people'][login]['active']:
                continue
            current = pending.get((login, key))
            if not current or tier_number > current[0]:
                pending[(login, key)] = (tier_number, value)
        selected = [
            (login, key, tier_number, value)
            for (login, key), (tier_number, value) in list(pending.items())[:30]
        ]
        if not selected:
            return state
        bot.send_message(
            main_chat_id,
            _notification_text(selected, state),
            parse_mode='HTML',
        )
        with conn:
            for login, key, tier_number, _value in selected:
                conn.execute(
                    '''UPDATE records_achievement_unlocks SET notified_at=?
                       WHERE employee_login=? AND achievement_key=?
                         AND tier<=? AND notified_at IS NULL''',
                    (now, login, key, tier_number),
                )
    finally:
        conn.close()
    return state


def start_records_refresh(db_path=DB_PATH, bot=None, main_chat_id=None):
    if not _refresh_lock.acquire(blocking=False):
        return False

    def worker():
        try:
            refresh_records_achievements(
                db_path=db_path,
                bot=bot,
                main_chat_id=main_chat_id,
            )
        except Exception as error:
            print(f'Ошибка расчёта OMG Records: {error}')
        finally:
            _refresh_lock.release()

    threading.Thread(
        target=worker,
        name='omg-records-refresh',
        daemon=True,
    ).start()
    return True


def _achievement_payload(item, values, unlocked_level):
    value = values[item['key']]
    computed_level = _achievement_level(item, values)
    level = min(len(TIERS), max(unlocked_level, computed_level))
    next_threshold = (
        item['thresholds'][level] if level < len(TIERS) else None
    )
    if item['key'] == 'clubs':
        requirements = TOURING_REQUIREMENTS
        next_requirement = requirements[level] if level < len(TIERS) else None
        current_requirement = next_requirement or requirements[-1]
        shift_units = sorted(
            (
                float(units or 0)
                for units in (values.get('club_shift_units') or {}).values()
            ),
            reverse=True,
        )[:current_requirement['clubs']]
        qualified = sum(
            units >= current_requirement['shifts'] for units in shift_units
        )
        value_label = (
            f'{qualified} из {current_requirement["clubs"]} клубов · '
            f'по {current_requirement["shifts"]} смен'
        )
        next_label = next_requirement['label'] if next_requirement else None
        progress = 1.0 if next_requirement is None else sum(
            min(units / next_requirement['shifts'], 1.0)
            for units in shift_units
        ) / next_requirement['clubs']
        threshold_payload = [
            {
                **tier,
                'value': requirement['clubs'],
                'value_label': requirement['label'],
            }
            for tier, requirement in zip(TIERS, requirements)
        ]
    else:
        previous_threshold = item['thresholds'][level - 1] if level else 0
        if next_threshold is None:
            progress = 1.0
        elif next_threshold == previous_threshold:
            progress = 0.0
        else:
            progress = max(0.0, min(
                1.0,
                (float(value or 0) - previous_threshold)
                / (next_threshold - previous_threshold),
            ))
        value_label = format_value(value, item['kind'])
        next_label = (
            format_value(next_threshold, item['kind'])
            if next_threshold is not None else None
        )
        threshold_payload = [
            {
                **tier,
                'value': item['thresholds'][tier['level'] - 1],
                'value_label': format_value(
                    item['thresholds'][tier['level'] - 1], item['kind'],
                ),
            }
            for tier in TIERS
        ]
    return {
        **item,
        'thresholds': threshold_payload,
        'value': value,
        'value_label': value_label,
        'level': level,
        'tier': _tier(level),
        'next_threshold': next_threshold,
        'next_label': next_label,
        'progress': progress,
    }


def _team_member_payload(login, person, values):
    levels = [
        _achievement_level(item, values) for item in ACHIEVEMENTS
    ]
    return {
        'login': login,
        'name': person['name'],
        'status': person['status'],
        'active': person['active'],
        'earned': sum(level > 0 for level in levels),
        'total': len(levels),
        'score': sum(levels),
        'bronze': sum(level == 1 for level in levels),
        'silver': sum(level == 2 for level in levels),
        'gold': sum(level == 3 for level in levels),
        'diamond': sum(level == 4 for level in levels),
    }


def build_records_dashboard(
    db_path,
    current_login,
    viewer_login=None,
    can_manage=False,
):
    state = _cached_records_state(db_path)
    if state is None:
        state = refresh_records_achievements(db_path=db_path)
    login = _normalize_login(current_login)
    viewer_login = _normalize_login(viewer_login) or login
    person = state['people'].get(login, {
        'login': login, 'name': login or 'Сотрудник', 'active': True,
    })
    viewer = state['people'].get(viewer_login, {
        'login': viewer_login,
        'name': viewer_login or 'Сотрудник',
        'active': True,
    })
    values = state['stats'].get(login, _new_stats())
    conn = sqlite3.connect(db_path)
    try:
        unlocked = {
            key: level for key, level in conn.execute(
                '''SELECT achievement_key, MAX(tier)
                   FROM records_achievement_unlocks
                   WHERE employee_login=? GROUP BY achievement_key''',
                (login,),
            )
        }
    finally:
        conn.close()
    payload_by_category = []
    all_payloads = []
    for category_key, category_title in CATEGORIES:
        achievements = [
            _achievement_payload(
                item,
                values,
                int(unlocked.get(item['key'], 0)),
            )
            for item in ACHIEVEMENTS
            if item['category'] == category_key
        ]
        all_payloads.extend(achievements)
        payload_by_category.append({
            'key': category_key,
            'title': category_title,
            'achievements': achievements,
        })
    return {
        'generated_at': datetime.now(MOSCOW).isoformat(timespec='seconds'),
        'user': person,
        'viewer': viewer,
        'can_manage': bool(can_manage),
        'summary': {
            'earned': sum(item['level'] > 0 for item in all_payloads),
            'total': len(all_payloads),
            'bronze': sum(item['level'] == 1 for item in all_payloads),
            'silver': sum(item['level'] == 2 for item in all_payloads),
            'gold': sum(item['level'] == 3 for item in all_payloads),
            'diamond': sum(item['level'] == 4 for item in all_payloads),
        },
        'records': state['records'],
        'archive_records': state['archive_records'],
        'categories': payload_by_category,
        'team': (
            [
                _team_member_payload(
                    employee_login,
                    employee,
                    state['stats'][employee_login],
                )
                for employee_login, employee in state['people'].items()
                if employee.get('user_id') is not None
            ]
            if can_manage else None
        ),
    }
