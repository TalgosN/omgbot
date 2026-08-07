import calendar
import hashlib
import hmac
import html
import io
import json
import os
import re
import sqlite3
import threading
import time
from datetime import UTC, date, datetime, timedelta
from functools import wraps
from urllib.parse import parse_qsl
from zoneinfo import ZoneInfo

import telebot
from flask import Flask, g, jsonify, request, send_file, send_from_directory

from constants import CHATS, TELEGRAM_API_KEY, extra_tags, get_clubs
from kpi_calculator import (
    add_penalty,
    calculate_daily_kpi_series,
    calculate_monthly_kpi,
    cancel_penalty,
    get_hashtag_entries,
    get_hashtag_summaries,
    get_month_status,
    get_metric_entries,
    get_kpi_freshness,
    get_kpi_settings,
    initialize_kpi_calculation_schema,
    list_penalties,
    save_kpi_settings,
    set_month_status,
    set_monthly_stream,
)
from permissions import (
    ACTIVE_ROLES,
    ROLE_MANAGER,
    ROLE_NAMES,
    ROLE_OWNER,
    ROLE_TECHNICIAN,
    get_user,
)
from repair_catalog import (
    ZONE_COUNTS,
    add_repair_event,
    catalog_payload,
    create_repair_case,
    initialize_repair_schema,
    migration_review_payload,
    repair_payload,
)


DB_PATH = 'db/omgbot.sql'
STATIC_DIR = os.path.join(os.path.dirname(__file__), 'kpi_static')
AUTH_MAX_AGE_SECONDS = int(os.getenv('KPI_WEBAPP_AUTH_MAX_AGE', '86400'))
ANALYTICS_CACHE_SECONDS = 60
_analytics_cache = {}
_analytics_cache_lock = threading.Lock()

app = Flask(__name__, static_folder=None)
app.config['MAX_CONTENT_LENGTH'] = 8 * 1024 * 1024


def _clear_analytics_cache():
    with _analytics_cache_lock:
        _analytics_cache.clear()


def _validate_month(value):
    raw = str(value or '').strip()
    try:
        return datetime.strptime(raw, '%Y-%m').date().replace(day=1).isoformat()
    except ValueError as error:
        raise ValueError('Месяц должен быть в формате YYYY-MM') from error


def _validate_day(value, month):
    raw = str(value or '').strip()
    try:
        selected = datetime.strptime(raw, '%Y-%m-%d').date()
    except ValueError as error:
        raise ValueError('Дата должна быть в формате YYYY-MM-DD') from error
    if selected.strftime('%Y-%m') != month[:7]:
        raise ValueError('Выбранная дата должна относиться к выбранному месяцу')
    return selected.isoformat()


def _default_day(month):
    month_start = datetime.strptime(month, '%Y-%m-%d').date()
    today = date.today()
    if (today.year, today.month) == (month_start.year, month_start.month):
        return today.isoformat()
    return month_start.replace(
        day=calendar.monthrange(month_start.year, month_start.month)[1],
    ).isoformat()


def _shift_month(month, offset):
    month_start = datetime.strptime(month, '%Y-%m-%d').date()
    absolute_month = month_start.year * 12 + month_start.month - 1 + offset
    return date(
        absolute_month // 12,
        absolute_month % 12 + 1,
        1,
    ).isoformat()


def _analytics_employee(row):
    return {
        'login': row['login'],
        'nickname': row['nickname'],
        'kpi': row['total_pct'],
        'weighted_kpi': row['weighted_pct'],
        'rank': row['rank'],
        'shifts': row['shifts'],
        'weighted_shifts': row['weighted_shifts'],
        'reviews': row['reviews'],
        'forms': row['forms'],
        'extensions': row['extensions'],
        'certificates': row['certificates'],
        'subscriptions': row['subscriptions'],
        'initiatives': row['initiatives'],
        'penalties': row['penalties'],
        'stream': row['stream'],
    }


def _analytics_point(label, rows):
    participants = [row for row in rows if row['shifts'] > 0]
    count = len(participants)
    totals = {
        metric: sum(row[metric] for row in participants)
        for metric in (
            'shifts',
            'weighted_shifts',
            'reviews',
            'forms',
            'extensions',
            'certificates',
            'subscriptions',
            'initiatives',
        )
    }
    return {
        'label': label,
        'team': {
            'employees': count,
            'kpi': (
                sum(row['total_pct'] for row in participants) / count
                if count else 0
            ),
            'weighted_kpi': (
                sum(row['weighted_pct'] for row in participants) / count
                if count else 0
            ),
            'zones': {
                zone: sum(row['zone'] == zone for row in participants)
                for zone in ('🟢', '🟡', '🔴')
            },
            **totals,
        },
        'employees': [
            _analytics_employee(row)
            for row in rows
        ],
    }


def _validate_init_data(init_data, bot_token, now=None):
    if not init_data or not bot_token:
        return None
    values = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = values.pop('hash', '')
    if not received_hash:
        return None

    data_check_string = '\n'.join(
        f'{key}={values[key]}'
        for key in sorted(values)
    )
    secret_key = hmac.new(
        b'WebAppData',
        bot_token.encode('utf-8'),
        hashlib.sha256,
    ).digest()
    expected_hash = hmac.new(
        secret_key,
        data_check_string.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(received_hash, expected_hash):
        return None

    try:
        auth_date = int(values.get('auth_date', 0))
        current_time = int(now if now is not None else time.time())
        if auth_date <= 0 or abs(current_time - auth_date) > AUTH_MAX_AGE_SECONDS:
            return None
        user = json.loads(values.get('user', '{}'))
        telegram_id = int(user['id'])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return {
        'telegram_id': telegram_id,
        'telegram_user': user,
        'auth_date': auth_date,
    }


def _request_user():
    auth = _validate_init_data(
        request.headers.get('X-Telegram-Init-Data', ''),
        TELEGRAM_API_KEY,
    )
    if not auth:
        return None
    user = get_user(telegram_id=auth['telegram_id'])
    if not user or user['status'] not in ACTIVE_ROLES:
        return None
    return dict(user)


def require_user(handler):
    @wraps(handler)
    def wrapped(*args, **kwargs):
        user = _request_user()
        if not user:
            return jsonify({'error': 'Откройте приложение через Telegram-бота.'}), 401
        g.kpi_user = user
        return handler(*args, **kwargs)
    return wrapped


def require_manager(handler):
    @wraps(handler)
    @require_user
    def wrapped(*args, **kwargs):
        if int(g.kpi_user['status']) < ROLE_MANAGER:
            return jsonify({'error': 'Действие доступно менеджерам и руководству.'}), 403
        return handler(*args, **kwargs)
    return wrapped


def require_owner(handler):
    @wraps(handler)
    @require_user
    def wrapped(*args, **kwargs):
        if int(g.kpi_user['status']) != ROLE_OWNER:
            return jsonify({
                'error': 'Настройки KPI доступны только руководству.',
            }), 403
        return handler(*args, **kwargs)
    return wrapped


def _active_employee_logins():
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(
            '''
            SELECT lower(login)
            FROM users
            WHERE status IN (0, 1, 2)
              AND login IS NOT NULL
              AND trim(login) <> ''
            ORDER BY ID
            '''
        ).fetchall()
        return [row[0] for row in rows]
    finally:
        conn.close()


def _employee_logins_with_month_shifts(employee_logins, month):
    active_logins = set(employee_logins)
    if not active_logins:
        return []
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(
            '''
            SELECT DISTINCT lower(ns.login)
            FROM shifts sh
            JOIN users ns ON (
                sh.shift_login IS NOT NULL
                AND lower(sh.shift_login)=lower(ns.login)
            ) OR (
                sh.shift_login IS NULL
                AND sh.shift_second_name=ns.second_name
                AND sh.shift_first_name=ns.first_name
            )
            WHERE date(substr(sh.dt_shift, 1, 10)) >= date(?)
              AND date(substr(sh.dt_shift, 1, 10)) < date(?, '+1 month')
            ''',
            (month, month),
        ).fetchall()
        return [
            row[0]
            for row in rows
            if row[0] in active_logins
        ]
    finally:
        conn.close()


def _employee_metadata(employee_logins, month):
    normalized = sorted({
        str(login or '').strip().lower()
        for login in employee_logins
        if str(login or '').strip()
    })
    if not normalized:
        return {}
    placeholders = ','.join('?' for _ in normalized)
    conn = sqlite3.connect(DB_PATH)
    try:
        users = conn.execute(
            f'''
            SELECT lower(login), status
            FROM users
            WHERE lower(login) IN ({placeholders})
            ''',
            normalized,
        ).fetchall()
        clubs = conn.execute(
            f'''
            SELECT DISTINCT lower(ns.login), sh.club
            FROM shifts sh
            JOIN users ns ON (
                sh.shift_login IS NOT NULL
                AND lower(sh.shift_login)=lower(ns.login)
            ) OR (
                sh.shift_login IS NULL
                AND sh.shift_second_name=ns.second_name
                AND sh.shift_first_name=ns.first_name
            )
            WHERE lower(ns.login) IN ({placeholders})
              AND date(substr(sh.dt_shift, 1, 10)) >= date(?)
              AND date(substr(sh.dt_shift, 1, 10)) < date(?, '+1 month')
              AND sh.club IS NOT NULL
              AND trim(sh.club) <> ''
            ORDER BY sh.club
            ''',
            (*normalized, month, month),
        ).fetchall()
    finally:
        conn.close()

    result = {
        login: {
            'role': int(role),
            'role_name': ROLE_NAMES.get(int(role), str(role)),
            'clubs': [],
        }
        for login, role in users
    }
    for login, club in clubs:
        if login in result and club not in result[login]['clubs']:
            result[login]['clubs'].append(club)
    return result


def _actor_login():
    return str(g.kpi_user.get('login') or '').strip().lower()


def _legacy_text_variants(value):
    variants = {value}
    for encoding in ('cp1251', 'utf-8'):
        try:
            variants.add(value.encode(encoding).decode('latin1'))
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return tuple(variants)


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


def _status_sql(statuses):
    values = []
    for status in statuses:
        for variant in _legacy_text_variants(status):
            if variant not in values:
                values.append(variant)
    return ','.join('?' for _ in values), values


def _plain_task_feedback(value):
    without_tags = re.sub(r'</?(?:b|i)>', '', str(value or ''), flags=re.I)
    return html.unescape(without_tags).strip()


def _moscow_today():
    return datetime.now(ZoneInfo('Europe/Moscow')).date()


def _upcoming_shifts(login, limit=3):
    today = _moscow_today().isoformat()
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(
            '''
            SELECT date(substr(sh.dt_shift, 1, 10)), sh.club,
                   ROUND(SUM(COALESCE(sh.dur, 0)), 1)
            FROM shifts sh
            JOIN users employee ON lower(employee.login)=?
             AND (
                (sh.shift_login IS NOT NULL
                 AND lower(sh.shift_login)=lower(employee.login))
                OR
                (sh.shift_login IS NULL
                 AND sh.shift_second_name=employee.second_name
                 AND sh.shift_first_name=employee.first_name)
             )
            WHERE date(substr(sh.dt_shift, 1, 10)) >= date(?)
            GROUP BY date(substr(sh.dt_shift, 1, 10)), sh.club
            ORDER BY date(substr(sh.dt_shift, 1, 10)), sh.club
            LIMIT ?
            ''',
            (login, today, limit),
        ).fetchall()
    finally:
        conn.close()
    return [
        {'date': row[0], 'club': row[1], 'duration': float(row[2] or 0)}
        for row in rows
    ]


def _task_counts():
    placeholders, statuses = _status_sql(('В работе', 'На проверке'))
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(
            f'''SELECT status, COUNT(*) FROM tasks
                WHERE status IN ({placeholders}) GROUP BY status''',
            statuses,
        ).fetchall()
    finally:
        conn.close()
    result = {'work': 0, 'review': 0}
    for status, count in rows:
        normalized = _normalize_legacy_text(status)
        if normalized == 'В работе':
            result['work'] += int(count)
        elif normalized == 'На проверке':
            result['review'] += int(count)
    return result


def _team_snapshot(month, selected_day):
    logins = _active_employee_logins()
    rows = calculate_monthly_kpi(
        month,
        employee_logins=logins,
        period_end=selected_day,
    )
    metadata = _employee_metadata(logins, month)
    for row in rows:
        row.update(metadata.get(row['login'], {'clubs': []}))
    participants = [row for row in rows if float(row.get('shifts', 0) or 0) > 0]
    average = (
        sum(float(row.get('total_pct', 0) or 0) for row in participants)
        / len(participants)
        if participants else 0
    )
    return rows, {
        'participants': len(participants),
        'average_pct': average,
        'red_zone': sum(row.get('zone') == '🔴' for row in participants),
        'active_penalties': sum(
            int(row.get('penalties', 0) or 0) for row in rows
        ),
    }


def _club_dashboard(team_rows, problem_counts):
    today = _moscow_today().isoformat()
    red_by_club = {}
    for row in team_rows:
        if row.get('zone') != '🔴' or float(row.get('shifts', 0) or 0) <= 0:
            continue
        for club in row.get('clubs', []):
            red_by_club[club] = red_by_club.get(club, 0) + 1

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        has_updates = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='club_status_updates'"
        ).fetchone()
        update_join = (
            'LEFT JOIN club_status_updates updates ON updates.club=clubs.club'
            if has_updates else ''
        )
        update_column = 'updates.changed_at' if has_updates else 'NULL'
        clubs = conn.execute(
            f'''SELECT clubs.club, clubs.status, {update_column}
                FROM clubs {update_join} ORDER BY clubs.club COLLATE NOCASE'''
        ).fetchall()
        shifts = conn.execute(
            '''
            SELECT sh.club,
                   COALESCE(NULLIF(employee.nick_name, ''),
                            NULLIF(employee.first_name, ''), employee.login)
            FROM shifts sh
            LEFT JOIN users employee ON (
                sh.shift_login IS NOT NULL
                AND lower(sh.shift_login)=lower(employee.login)
            ) OR (
                sh.shift_login IS NULL
                AND sh.shift_second_name=employee.second_name
                AND sh.shift_first_name=employee.first_name
            )
            WHERE date(substr(sh.dt_shift, 1, 10))=date(?)
            GROUP BY sh.club, employee.login, employee.first_name,
                     employee.nick_name
            ORDER BY sh.club, employee.ID
            ''',
            (today,),
        ).fetchall()
    finally:
        conn.close()

    shift_names = {}
    for club, name in shifts:
        if name:
            shift_names.setdefault(club, []).append(name)
    physical_clubs = {
        name
        for name, settings in get_clubs().items()
        if settings.get('is_physical') is True
    }
    return [
        {
            'club': row['club'],
            'status': _normalize_legacy_text(row['status']),
            'changed_at': row[2],
            'on_shift': shift_names.get(row['club'], []),
            'problems': problem_counts.get(row['club'], {'work': 0, 'review': 0}),
            'red_zone': red_by_club.get(row['club'], 0),
        }
        for row in clubs
        if row['club'] in physical_clubs
    ]


def _problem_counts_by_club():
    placeholders, statuses = _status_sql(('В работе', 'На проверке'))
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(
            f'''SELECT club, status, COUNT(*) FROM tasks
                WHERE status IN ({placeholders})
                GROUP BY club, status''',
            statuses,
        ).fetchall()
    finally:
        conn.close()
    result = {}
    for club, status, count in rows:
        item = result.setdefault(club, {'work': 0, 'review': 0})
        normalized = _normalize_legacy_text(status)
        if normalized == 'В работе':
            item['work'] += int(count)
        elif normalized == 'На проверке':
            item['review'] += int(count)
    return result


def _notification_bot():
    return telebot.TeleBot(TELEGRAM_API_KEY) if TELEGRAM_API_KEY else None


def _problem_mentions(task_type, club):
    club_tag = str(get_clubs().get(club, {}).get('tag') or '').strip()
    if task_type == 'Ремонт':
        repair_tag = extra_tags.get(task_type, '')
        return ' '.join(
            value for value in (repair_tag if club_tag != repair_tag else '', club_tag)
            if value
        )
    if task_type == 'Улучшение бота':
        return extra_tags.get(task_type, '')
    return club_tag


def _send_problem_notification(event, task, message='', photo=None):
    bot = _notification_bot()
    if not bot:
        return
    title = html.escape(str(task['title']))
    task_type = _normalize_legacy_text(task['type'])
    type_low = html.escape(task_type.lower())
    mentions = _problem_mentions(task_type, task['club'])
    try:
        if event == 'created':
            description = html.escape(str(task.get('description') or ''))
            full = (
                f"⚙️ Добавлена новая проблема-{type_low}:\n<b>{title}</b>\n\n"
                f"📝 <b>Описание:</b>\n{description[:800]}"
            )
            short = f"⚙️ Добавлена новая проблема-{type_low}: <b>{title}</b>"
            report_text = f"#задачи\n\n{full} @OMGVR_Admin_Bot"
            if photo and CHATS.get('reports'):
                photo_file = io.BytesIO(photo)
                photo_file.name = 'problem.jpg'
                bot.send_photo(
                    CHATS['reports'], photo_file, caption=report_text,
                    parse_mode='HTML',
                )
            elif CHATS.get('reports'):
                bot.send_message(CHATS['reports'], report_text, parse_mode='HTML')
            if CHATS.get('main_group'):
                bot.send_message(
                    CHATS['main_group'], f"{mentions}\n\n{short}",
                    parse_mode='HTML',
                )
            if task_type == 'Ремонт' and CHATS.get('repair_extra'):
                bot.send_message(
                    CHATS['repair_extra'], f"@RobinKruzo1\n\n{short}",
                    parse_mode='HTML',
                )
        elif event in {'solution', 'returned'}:
            escaped_message = html.escape(message)
            if event == 'solution':
                heading = f"👀 <b>Решение по проблеме-{type_low}:</b>"
                label = 'Ответ'
                tail = '\n\n👉 <b>Проверьте и подтвердите выполнение на доске задач!</b>'
            else:
                heading = f"⚠️ <b>Проблема-{type_low} возвращена в работу:</b>"
                label = 'Причина возврата'
                tail = ''
            full = f"{heading}\n{title}\n\n💬 <b>{label}:</b>\n{escaped_message}"
            short = f"{heading} {title}\n💬 <i>{escaped_message}</i>{tail}"
            if CHATS.get('reports'):
                bot.send_message(
                    CHATS['reports'], f"#задачи\n\n{full}\n\n@OMGVR_Admin_Bot",
                    parse_mode='HTML',
                )
            if CHATS.get('main_group'):
                prefix = f'{mentions}\n\n' if event == 'returned' else ''
                bot.send_message(
                    CHATS['main_group'], f'{prefix}{short}', parse_mode='HTML',
                )
            if task_type == 'Ремонт' and CHATS.get('repair_extra'):
                bot.send_message(
                    CHATS['repair_extra'],
                    f"@RobinKruzo1\n\n{heading} {title}\n💬 <i>{escaped_message}</i>",
                    parse_mode='HTML',
                )
    except Exception as error:
        print(f'Ошибка уведомления Mini App Taskboard: {error}')


def _employee_explanation(row):
    shifts = float(row.get('shifts', 0) or 0)
    metric_specs = (
        ('reviews', 'Отзывы', 1 / 3),
        ('forms', 'Анкеты', 1 / 3),
        ('extensions', 'Продления', 1 / 3),
        ('certificates', 'Сертификаты', 0.10),
        ('subscriptions', 'Абонементы', 0.10),
    )
    metrics = []
    for key, label, weight in metric_specs:
        fact = float(row.get(key, 0) or 0)
        plan_per_shift = float(row.get(f'{key}_plan_per_shift', 0) or 0)
        target = float(
            row.get(f'{key}_target', shifts * plan_per_shift) or 0
        )
        ratio = float(row.get(f'{key}_pct', 0) or 0)
        metrics.append({
            'key': key,
            'label': label,
            'fact': fact,
            'plan_per_shift': plan_per_shift,
            'target': target,
            'needed': max(target - fact, 0),
            'ratio': ratio,
            'weight': weight,
            'contribution_pct': ratio * weight,
        })

    initiative_fact = float(row.get('initiatives', 0) or 0)
    initiative_ratio = float(row.get('initiatives_pct', 0) or 0)
    metrics.append({
        'key': 'initiatives',
        'label': 'Инициативы',
        'fact': initiative_fact,
        'plan_per_shift': None,
        'bonus_per_item': float(
            row.get('initiative_bonus_per_item', 0.10) or 0.10
        ),
        'target': None,
        'needed': 0,
        'ratio': initiative_ratio,
        'weight': 1.0,
        'contribution_pct': initiative_ratio,
    })

    metric_contribution = sum(
        metric['contribution_pct']
        for metric in metrics
    )
    penalty_impact = float(row.get('penalty_impact', 0) or 0)
    stream_bonus = float(row.get('stream_bonus', 0) or 0)
    total_pct = float(row.get('total_pct', 0) or 0)
    average_pct = float(row.get('average_pct', 0) or 0)
    return {
        'metrics': metrics,
        'metric_contribution_pct': metric_contribution,
        'penalty_impact_pct': penalty_impact,
        'stream_bonus_pct': stream_bonus,
        'total_pct': total_pct,
        'pace': {
            'available': shifts > 0,
            'shifts': shifts,
            'projected_pct': total_pct if shifts > 0 else 0,
            'green_threshold_pct': average_pct,
            'gap_to_green_pct': max(average_pct - total_pct, 0),
        },
    }


def _attention_reasons(row):
    reasons = []
    if row.get('zone') == '🔴':
        reasons.append({
            'key': 'red_zone',
            'label': 'Красная зона',
        })
    if int(row.get('penalties', 0) or 0) > 0:
        reasons.append({
            'key': 'penalty',
            'label': 'Активный штраф',
        })
    if float(row.get('kpi_change_7d', 0) or 0) <= -0.10:
        reasons.append({
            'key': 'drop',
            'label': 'Падение KPI за 7 дней',
        })
    metric_total = sum(
        float(row.get(field, 0) or 0)
        for field in (
            'reviews',
            'forms',
            'extensions',
            'certificates',
            'subscriptions',
            'initiatives',
        )
    )
    if float(row.get('shifts', 0) or 0) > 0 and metric_total == 0:
        reasons.append({
            'key': 'no_kpi',
            'label': 'Есть смены, но нет KPI-записей',
        })
    return reasons


def _snapshot_employee(row):
    fields = (
        'login',
        'nickname',
        'role',
        'role_name',
        'clubs',
        'shifts',
        'weighted_shifts',
        'reviews',
        'reviews_pct',
        'forms',
        'forms_pct',
        'extensions',
        'extensions_pct',
        'certificates',
        'certificates_pct',
        'subscriptions',
        'subscriptions_pct',
        'initiatives',
        'initiatives_pct',
        'penalties',
        'penalty_impact',
        'stream',
        'stream_bonus',
        'total_pct',
        'weighted_pct',
        'rank',
        'average_pct',
        'zone',
    )
    return {
        field: row.get(field)
        for field in fields
    }


def _month_close_preview(month):
    selected_day = _default_day(month)
    active_logins = _active_employee_logins()
    rows = calculate_monthly_kpi(
        month,
        employee_logins=active_logins,
        period_end=selected_day,
    )
    metadata = _employee_metadata(active_logins, month)
    for row in rows:
        row.update(metadata.get(row['login'], {
            'role': None,
            'role_name': 'Неизвестно',
            'clubs': [],
        }))

    participants = [row for row in rows if float(row.get('shifts', 0) or 0) > 0]
    no_shifts = [row for row in rows if float(row.get('shifts', 0) or 0) <= 0]
    red_zone = [row for row in participants if row.get('zone') == '🔴']
    penalties = [
        row
        for row in rows
        if int(row.get('penalties', 0) or 0) > 0
    ]
    no_kpi = []
    for row in participants:
        metric_total = sum(
            float(row.get(field, 0) or 0)
            for field in (
                'reviews',
                'forms',
                'extensions',
                'certificates',
                'subscriptions',
                'initiatives',
            )
        )
        if metric_total == 0:
            no_kpi.append(row)

    warning_specs = (
        ('no_shifts', 'Активные сотрудники без смен', no_shifts),
        ('no_kpi', 'Есть смены, но нет KPI-записей', no_kpi),
        ('red_zone', 'Сотрудники в красной зоне', red_zone),
        ('penalties', 'Сотрудники с активными штрафами', penalties),
    )
    warnings = [
        {
            'key': key,
            'label': label,
            'count': len(items),
            'employees': [
                {
                    'login': item.get('login'),
                    'nickname': item.get('nickname'),
                }
                for item in items
            ],
        }
        for key, label, items in warning_specs
        if items
    ]
    average = (
        sum(float(row.get('total_pct', 0) or 0) for row in participants)
        / len(participants)
        if participants else 0
    )
    zones = {
        zone: sum(row.get('zone') == zone for row in participants)
        for zone in ('🟢', '🟡', '🔴')
    }
    summary = {
        'active_employees': len(rows),
        'participants': len(participants),
        'average_pct': average,
        'zones': zones,
        'warnings': sum(warning['count'] for warning in warnings),
    }
    return {
        'period_month': month,
        'date': selected_day,
        'generated_at': datetime.now(UTC).isoformat(),
        'summary': summary,
        'warnings': warnings,
        'employees': [_snapshot_employee(row) for row in rows],
    }


@app.after_request
def add_security_headers(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    return response


@app.errorhandler(ValueError)
def handle_value_error(error):
    return jsonify({'error': str(error)}), 400


@app.errorhandler(413)
def handle_upload_too_large(_error):
    return jsonify({'error': 'Файл слишком большой. Максимум 6 МБ.'}), 413


@app.get('/')
def index():
    return send_from_directory(STATIC_DIR, 'home.html')


@app.get('/kpi')
def kpi_index():
    return send_from_directory(STATIC_DIR, 'index.html')


@app.get('/problems')
def problems_index():
    return send_from_directory(STATIC_DIR, 'problems.html')


@app.get('/static/<path:filename>')
def static_file(filename):
    return send_from_directory(STATIC_DIR, filename)


@app.get('/health')
def health():
    return jsonify({'status': 'ok'})


@app.get('/api/me')
@require_user
def api_me():
    role = int(g.kpi_user['status'])
    return jsonify({
        'telegram_id': g.kpi_user['chatid'],
        'login': g.kpi_user['login'],
        'name': (
            g.kpi_user.get('nick_name')
            or g.kpi_user.get('first_name')
            or g.kpi_user['login']
        ),
        'role': role,
        'role_name': ROLE_NAMES[role],
        'can_manage': role >= ROLE_MANAGER,
        'can_edit_settings': role == ROLE_OWNER,
    })


@app.get('/api/home')
@require_user
def api_home():
    today = _moscow_today()
    month = today.replace(day=1).isoformat()
    selected_day = today.isoformat()
    role = int(g.kpi_user['status'])
    login = _actor_login()
    payload = {
        'date': selected_day,
        'role': role,
        'upcoming_shifts': [],
        'personal_kpi': None,
        'management': None,
        'clubs': [],
    }

    if role != ROLE_OWNER:
        payload['upcoming_shifts'] = _upcoming_shifts(login)
        personal_rows = calculate_monthly_kpi(
            month,
            employee_logins=[login],
            period_end=selected_day,
        )
        if personal_rows:
            row = personal_rows[0]
            payload['personal_kpi'] = {
                'shifts': float(row.get('shifts', 0) or 0),
                'total_pct': float(row.get('total_pct', 0) or 0),
                'zone': row.get('zone'),
            }

    if role >= ROLE_MANAGER:
        team_rows, management = _team_snapshot(month, selected_day)
        management['problems'] = _task_counts()
        payload['management'] = management
        if role == ROLE_OWNER:
            payload['clubs'] = _club_dashboard(
                team_rows,
                _problem_counts_by_club(),
            )
    return jsonify(payload)


def _task_payload(row, include_description=True):
    result = {
        'id': int(row['ID']),
        'date': row['dtrep'],
        'type': _normalize_legacy_text(row['type']),
        'club': row['club'],
        'title': _normalize_legacy_text(row['title']),
        'status': _normalize_legacy_text(row['status']),
        'closed_at': row['dtfb'],
        'has_photo': row['photo'] is not None,
    }
    if include_description:
        result.update({
            'description': _normalize_legacy_text(row['desc']),
            'feedback': _plain_task_feedback(
                _normalize_legacy_text(row['feedback'])
            ),
        })
    return result


@app.get('/api/problems')
@require_user
def api_problems():
    requested_status = str(request.args.get('status') or 'work').strip().lower()
    status_map = {
        'work': ('В работе',),
        'review': ('На проверке',),
        'done': ('Выполнено',),
    }
    if requested_status not in status_map:
        raise ValueError('Неизвестный фильтр статуса')
    placeholders, statuses = _status_sql(status_map[requested_status])
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            f'''SELECT * FROM tasks WHERE status IN ({placeholders})
                ORDER BY date(dtrep) DESC, ID DESC LIMIT 200''',
            statuses,
        ).fetchall()
    finally:
        conn.close()
    return jsonify({
        'status': requested_status,
        'tasks': [_task_payload(row, include_description=False) for row in rows],
        'counts': _task_counts(),
    })


@app.get('/api/problems-meta')
@require_user
def api_problems_meta():
    return jsonify({
        'clubs': list(get_clubs()),
        'types': [
            'Вопрос/жалоба/предложение',
            'Ремонт',
            'Улучшение бота',
        ],
        'can_process': int(g.kpi_user['status']) >= ROLE_TECHNICIAN,
        'can_edit_repair_catalog': int(g.kpi_user['status']) >= ROLE_MANAGER,
        'repair_clubs': list(ZONE_COUNTS),
    })


@app.get('/api/repairs/catalog')
@require_user
def api_repair_catalog():
    club = str(request.args.get('club') or '').strip()
    if club and club not in get_clubs():
        raise ValueError('Выберите клуб')
    include_inactive = (
        request.args.get('include_inactive') == '1'
        and int(g.kpi_user['status']) >= ROLE_MANAGER
    )
    return jsonify(catalog_payload(DB_PATH, club, include_inactive))


@app.post('/api/repairs/catalog/items')
@require_manager
def api_add_repair_item():
    payload = request.get_json(silent=True) or {}
    name = str(payload.get('name') or '').strip()
    if not name or len(name) > 80:
        raise ValueError('Название оборудования должно быть не длиннее 80 символов')
    initialize_repair_schema(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    try:
        with conn:
            cursor = conn.execute(
                '''INSERT INTO repair_item_types(name, sort_order)
                   VALUES (?, (SELECT COALESCE(MAX(sort_order), 0) + 1
                               FROM repair_item_types))''',
                (name,),
            )
    except sqlite3.IntegrityError as error:
        raise ValueError('Такое оборудование уже есть в списке') from error
    finally:
        conn.close()
    return jsonify({'id': cursor.lastrowid, 'name': name}), 201


@app.post('/api/repairs/catalog/details')
@require_manager
def api_add_repair_detail():
    payload = request.get_json(silent=True) or {}
    name = str(payload.get('name') or '').strip()
    try:
        item_id = int(payload.get('item_id'))
    except (TypeError, ValueError) as error:
        raise ValueError('Выберите оборудование') from error
    if not name or len(name) > 80:
        raise ValueError('Уточнение должно быть не длиннее 80 символов')
    initialize_repair_schema(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    try:
        with conn:
            if not conn.execute(
                'SELECT 1 FROM repair_item_types WHERE id=?', (item_id,)
            ).fetchone():
                raise ValueError('Оборудование не найдено')
            cursor = conn.execute(
                '''INSERT INTO repair_item_details(item_type_id, name, sort_order)
                   VALUES (?, ?, (SELECT COALESCE(MAX(sort_order), 0) + 1
                                   FROM repair_item_details
                                   WHERE item_type_id=?))''',
                (item_id, name, item_id),
            )
    except sqlite3.IntegrityError as error:
        raise ValueError('Такое уточнение уже есть в списке') from error
    finally:
        conn.close()
    return jsonify({'id': cursor.lastrowid, 'name': name}), 201


@app.post('/api/repairs/catalog/locations')
@require_manager
def api_add_repair_location():
    payload = request.get_json(silent=True) or {}
    club = str(payload.get('club') or '').strip()
    name = str(payload.get('name') or '').strip()
    if club not in get_clubs():
        raise ValueError('Выберите клуб')
    if not name or len(name) > 80:
        raise ValueError('Название места должно быть не длиннее 80 символов')
    initialize_repair_schema(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    try:
        with conn:
            cursor = conn.execute(
                '''INSERT INTO repair_locations(club, name, kind, sort_order)
                   VALUES (?, ?, 'other',
                           (SELECT COALESCE(MAX(sort_order), 0) + 1
                            FROM repair_locations WHERE club=?))''',
                (club, name, club),
            )
    except sqlite3.IntegrityError as error:
        raise ValueError('Такое место уже есть в клубе') from error
    finally:
        conn.close()
    return jsonify({'id': cursor.lastrowid, 'name': name}), 201


@app.patch('/api/repairs/catalog/<kind>/<int:entry_id>')
@require_manager
def api_toggle_repair_catalog_entry(kind, entry_id):
    table_map = {
        'items': 'repair_item_types',
        'details': 'repair_item_details',
        'locations': 'repair_locations',
    }
    if kind not in table_map:
        raise ValueError('Неизвестный раздел справочника')
    payload = request.get_json(silent=True) or {}
    active = 1 if bool(payload.get('active')) else 0
    initialize_repair_schema(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    try:
        with conn:
            cursor = conn.execute(
                f'UPDATE {table_map[kind]} SET active=? WHERE id=?',
                (active, entry_id),
            )
            if cursor.rowcount != 1:
                return jsonify({'error': 'Запись справочника не найдена.'}), 404
    finally:
        conn.close()
    return jsonify({'id': entry_id, 'active': bool(active)})


@app.get('/api/repairs/migration-review')
@require_manager
def api_repair_migration_review():
    return jsonify(migration_review_payload(DB_PATH))


@app.post('/api/repairs/<int:task_id>/mapping')
@require_manager
def api_map_legacy_repair(task_id):
    payload = request.get_json(silent=True) or {}
    try:
        item_id = int(payload.get('item_id'))
        detail_id = int(payload['detail_id']) if payload.get('detail_id') else None
        location_ids = payload.get('location_ids') or []
        if not isinstance(location_ids, list):
            raise ValueError
    except (TypeError, ValueError) as error:
        raise ValueError('Выберите оборудование и хотя бы одно место') from error
    initialize_repair_schema(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            task = conn.execute(
                '''SELECT ID, club FROM tasks
                   WHERE ID=? AND lower(type)=lower('Ремонт')''',
                (task_id,),
            ).fetchone()
            if not task:
                return jsonify({'error': 'Ремонтная заявка не найдена.'}), 404
            if conn.execute(
                'SELECT 1 FROM repair_cases WHERE task_id=?', (task_id,)
            ).fetchone():
                return jsonify({'error': 'Заявка уже сопоставлена.'}), 409
            create_repair_case(
                conn, task_id, task['club'], item_id, detail_id, location_ids,
            )
            conn.execute(
                "UPDATE repair_cases SET mapping_source='legacy-manual' WHERE task_id=?",
                (task_id,),
            )
            conn.execute('DELETE FROM repair_events WHERE task_id=?', (task_id,))
    finally:
        conn.close()
    return jsonify({'task_id': task_id, 'mapped': True})


@app.get('/api/problems/<int:task_id>')
@require_user
def api_problem(task_id):
    initialize_repair_schema(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute('SELECT * FROM tasks WHERE ID=?', (task_id,)).fetchone()
        repair = repair_payload(conn, task_id) if row else None
    finally:
        conn.close()
    if not row:
        return jsonify({'error': 'Проблема не найдена.'}), 404
    result = _task_payload(row)
    result['repair'] = repair
    return jsonify(result)


@app.get('/api/problems/<int:task_id>/photo')
@require_user
def api_problem_photo(task_id):
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute('SELECT photo FROM tasks WHERE ID=?', (task_id,)).fetchone()
    finally:
        conn.close()
    if not row or row[0] is None:
        return jsonify({'error': 'Фотография не найдена.'}), 404
    photo = row[0]
    if photo.startswith(b'\x89PNG\r\n\x1a\n'):
        mimetype = 'image/png'
    elif photo.startswith(b'RIFF') and photo[8:12] == b'WEBP':
        mimetype = 'image/webp'
    else:
        mimetype = 'image/jpeg'
    return send_file(io.BytesIO(photo), mimetype=mimetype, max_age=0)


@app.post('/api/problems')
@require_user
def api_create_problem():
    task_type = str(request.form.get('type') or '').strip()
    club = str(request.form.get('club') or '').strip()
    title = str(request.form.get('title') or '').strip()
    description = str(request.form.get('description') or '').strip()
    allowed_types = {
        'Вопрос/жалоба/предложение', 'Ремонт', 'Улучшение бота',
    }
    if task_type not in allowed_types:
        raise ValueError('Выберите тип обращения')
    if club not in get_clubs():
        raise ValueError('Выберите клуб')
    if task_type == 'Ремонт':
        title = 'Ремонт'
    if not title or len(title) > 50 or title.isnumeric():
        raise ValueError('Название должно содержать текст и быть не длиннее 50 символов')
    if not description or len(description) > 1000:
        raise ValueError('Описание должно быть не длиннее 1000 символов')
    item_id = detail_id = None
    location_ids = []
    if task_type == 'Ремонт':
        try:
            item_id = int(request.form.get('repair_item_id'))
            detail_id = (
                int(request.form.get('repair_detail_id'))
                if request.form.get('repair_detail_id') else None
            )
            location_ids = json.loads(request.form.get('repair_location_ids') or '[]')
            if not isinstance(location_ids, list):
                raise ValueError
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError('Выберите оборудование и хотя бы одно место') from error
    upload = request.files.get('photo')
    photo = None
    if upload and upload.filename:
        if upload.mimetype not in {'image/jpeg', 'image/png', 'image/webp'}:
            raise ValueError('Фото должно быть в формате JPG, PNG или WebP')
        photo = upload.read(6 * 1024 * 1024 + 1)
        if len(photo) > 6 * 1024 * 1024:
            raise ValueError('Фото должно быть не больше 6 МБ')

    if task_type == 'Ремонт':
        initialize_repair_schema(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            cursor = conn.execute(
                '''INSERT INTO tasks (
                       dtrep, type, club, title, photo, desc, status
                   ) VALUES (?, ?, ?, ?, ?, ?, 'В работе')''',
                (_moscow_today().isoformat(), task_type, club, title, photo, description),
            )
            task_id = cursor.lastrowid
            if task_type == 'Ремонт':
                title = create_repair_case(
                    conn, task_id, club, item_id, detail_id, location_ids,
                )
                conn.execute('UPDATE tasks SET title=? WHERE ID=?', (title, task_id))
    finally:
        conn.close()
    task = {
        'title': title,
        'type': task_type,
        'club': club,
        'description': description,
    }
    _send_problem_notification('created', task, photo=photo)
    return jsonify({'id': task_id, 'status': 'В работе'}), 201


def _change_problem_status(task_id, expected_status, new_status, entry=None):
    initialize_repair_schema(DB_PATH)
    placeholders, statuses = _status_sql((expected_status,))
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            task = conn.execute(
                f'''SELECT * FROM tasks WHERE ID=?
                    AND status IN ({placeholders})''',
                (task_id, *statuses),
            ).fetchone()
            if not task:
                return None
            feedback = str(task['feedback'] or '').strip()
            if entry:
                feedback = f'{feedback}\n\n{entry}'.strip()
            status_date = (
                _moscow_today().isoformat()
                if new_status in {'На проверке', 'Выполнено'} else None
            )
            cursor = conn.execute(
                f'''UPDATE tasks SET status=?, feedback=?, dtfb=?
                    WHERE ID=? AND status IN ({placeholders})''',
                (new_status, feedback, status_date, task_id, *statuses),
            )
            if cursor.rowcount != 1:
                return None
            event_types = {
                'На проверке': 'solution',
                'В работе': 'returned',
                'Выполнено': 'confirmed',
            }
            add_repair_event(
                conn, task_id, event_types.get(new_status, 'status_changed'),
                _plain_task_feedback(entry) if entry else None,
            )
            return dict(task)
    finally:
        conn.close()


@app.post('/api/problems/<int:task_id>/solution')
@require_user
def api_problem_solution(task_id):
    if int(g.kpi_user['status']) < ROLE_TECHNICIAN:
        return jsonify({'error': 'Обрабатывать проблемы может ремонтник или менеджер.'}), 403
    payload = request.get_json(silent=True) or {}
    solution = str(payload.get('message') or '').strip()
    if not solution or len(solution) > 1000:
        raise ValueError('Решение должно быть не длиннее 1000 символов')
    today_short = datetime.now(ZoneInfo('Europe/Moscow')).strftime('%d.%m')
    task = _change_problem_status(
        task_id,
        'В работе',
        'На проверке',
        f'<b>[{today_short}] Админ:</b> {html.escape(solution)}',
    )
    if not task:
        return jsonify({'error': 'Статус проблемы уже изменился.'}), 409
    _send_problem_notification('solution', task, message=solution)
    return jsonify({'status': 'На проверке'})


@app.post('/api/problems/<int:task_id>/confirm')
@require_user
def api_problem_confirm(task_id):
    task = _change_problem_status(
        task_id,
        'На проверке',
        'Выполнено',
    )
    if not task:
        return jsonify({'error': 'Статус проблемы уже изменился.'}), 409
    return jsonify({'status': 'Выполнено'})


@app.post('/api/problems/<int:task_id>/return')
@require_user
def api_problem_return(task_id):
    payload = request.get_json(silent=True) or {}
    reason = str(payload.get('message') or '').strip()
    if not reason or len(reason) > 1000:
        raise ValueError('Укажите причину возврата не длиннее 1000 символов')
    today_short = datetime.now(ZoneInfo('Europe/Moscow')).strftime('%d.%m')
    task = _change_problem_status(
        task_id,
        'На проверке',
        'В работе',
        f'<b>[{today_short}] Сотрудник:</b> {html.escape(reason)}',
    )
    if not task:
        return jsonify({'error': 'Статус проблемы уже изменился.'}), 409
    _send_problem_notification('returned', task, message=reason)
    return jsonify({'status': 'В работе'})


@app.get('/api/kpi')
@require_user
def api_kpi():
    month = _validate_month(
        request.args.get('month') or date.today().strftime('%Y-%m')
    )
    selected_day = _validate_day(
        request.args.get('date') or _default_day(month),
        month,
    )
    active_logins = _active_employee_logins()
    employee_logins = active_logins
    rows = calculate_monthly_kpi(
        month,
        employee_logins=employee_logins,
        period_end=selected_day,
    )
    previous_rows = []
    selected_date = datetime.strptime(selected_day, '%Y-%m-%d').date()
    if selected_date.day > 1:
        previous_rows = calculate_monthly_kpi(
            month,
            employee_logins=employee_logins,
            period_end=selected_date - timedelta(days=1),
        )
    previous_by_login = {row['login']: row for row in previous_rows}
    comparison_date = max(
        datetime.strptime(month, '%Y-%m-%d').date(),
        selected_date - timedelta(days=7),
    )
    week_rows = []
    if comparison_date < selected_date:
        week_rows = calculate_monthly_kpi(
            month,
            employee_logins=employee_logins,
            period_end=comparison_date,
        )
    week_by_login = {row['login']: row for row in week_rows}
    metadata = _employee_metadata(employee_logins, month)
    hashtag_summaries = get_hashtag_summaries(
        employee_logins,
        month,
        period_end=selected_day,
        db_path=DB_PATH,
    )
    for row in rows:
        previous = previous_by_login.get(row['login'])
        week_previous = week_by_login.get(row['login'])
        row['kpi_change'] = (
            row['total_pct'] - previous['total_pct']
            if previous else None
        )
        row['rank_change'] = (
            previous['rank'] - row['rank']
            if (
                previous
                and previous['rank'] is not None
                and row['rank'] is not None
            )
            else None
        )
        row['kpi_change_7d'] = (
            row['total_pct'] - week_previous['total_pct']
            if week_previous else None
        )
        row.update(metadata.get(row['login'], {
            'role': None,
            'role_name': 'Неизвестно',
            'clubs': [],
        }))
        row['attention_reasons'] = _attention_reasons(row)
        row['needs_attention'] = bool(row['attention_reasons'])
        row['extra_hashtags'] = hashtag_summaries.get(row['login'], [])
        row['explanation'] = _employee_explanation(row)
    active_login_set = set(active_logins)
    penalties = [
        item
        for item in list_penalties(month)
        if str(item.get('employee_login') or '').strip().lower()
        in active_login_set
    ]
    current_login = _actor_login()
    current_employee = next(
        (
            row
            for row in rows
            if str(row.get('login') or '').strip().lower() == current_login
        ),
        None,
    )
    if current_employee is None and current_login in set(active_logins):
        personal_rows = calculate_monthly_kpi(
            month,
            employee_logins=[current_login],
            period_end=selected_day,
        )
        if personal_rows:
            current_employee = personal_rows[0]
            current_employee['kpi_change'] = None
            current_employee['rank_change'] = None
            current_employee['extra_hashtags'] = hashtag_summaries.get(
                current_employee['login'],
                [],
            )
            current_employee['explanation'] = _employee_explanation(
                current_employee
            )
    freshness = get_kpi_freshness(month, period_end=selected_day)
    freshness['calculated_at'] = datetime.now(UTC).isoformat()
    return jsonify({
        'month': month[:7],
        'date': selected_day,
        'month_status': get_month_status(month),
        'employees': rows,
        'penalties': penalties,
        'my_kpi': current_employee,
        'freshness': freshness,
    })


@app.get('/api/kpi/details')
@require_user
def api_kpi_details():
    month = _validate_month(request.args.get('month'))
    selected_day = _validate_day(request.args.get('date'), month)
    employee_login = str(request.args.get('employee_login') or '').strip()
    metric = str(request.args.get('metric') or '').strip()
    if employee_login.lower() not in set(_active_employee_logins()):
        raise ValueError('Сотрудник не найден или неактивен')
    if metric.startswith('hashtag:'):
        entries = get_hashtag_entries(
            employee_login,
            month,
            metric.removeprefix('hashtag:'),
            period_end=selected_day,
            db_path=DB_PATH,
        )
    else:
        entries = get_metric_entries(
            employee_login,
            month,
            metric,
            period_end=selected_day,
        )
    return jsonify({
        'employee_login': employee_login.lower(),
        'metric': metric,
        'date': selected_day,
        'entries': entries,
    })


@app.get('/api/kpi/analytics')
@require_user
def api_kpi_analytics():
    month = _validate_month(
        request.args.get('month') or date.today().strftime('%Y-%m')
    )
    mode = str(request.args.get('mode') or 'daily').strip().lower()
    if mode not in {'daily', 'monthly'}:
        raise ValueError('Режим аналитики должен быть daily или monthly')

    initialize_kpi_calculation_schema()
    selected_end = (
        _validate_day(
            request.args.get('date') or _default_day(month),
            month,
        )
        if mode == 'daily' else ''
    )
    cache_key = (
        mode,
        month,
        selected_end,
    )
    with _analytics_cache_lock:
        cached = _analytics_cache.get(cache_key)
    if cached and time.monotonic() - cached['created_at'] < ANALYTICS_CACHE_SECONDS:
        return jsonify(cached['payload'])

    active_logins = _active_employee_logins()
    periods = []
    if mode == 'daily':
        end_date = datetime.strptime(selected_end, '%Y-%m-%d').date()
        periods = [
            (month, date(month_date.year, month_date.month, day).isoformat())
            for month_date in [datetime.strptime(month, '%Y-%m-%d').date()]
            for day in range(1, end_date.day + 1)
        ]
    else:
        periods = [
            (period_month, calendar.monthrange(
                int(period_month[:4]),
                int(period_month[5:7]),
            )[1])
            for period_month in (
                _shift_month(month, offset)
                for offset in range(-11, 1)
            )
        ]
        periods = [
            (
                period_month,
                f'{period_month[:7]}-{month_day:02d}',
            )
            for period_month, month_day in periods
        ]

    points = []
    employees = {}
    if mode == 'daily':
        employee_logins = _employee_logins_with_month_shifts(
            active_logins,
            month,
        )
        daily_series = calculate_daily_kpi_series(
            month,
            periods[-1][1],
            employee_logins=employee_logins,
            ensure_schema=False,
        )
        period_rows = [
            (snapshot['date'], snapshot['employees'])
            for snapshot in daily_series
        ]
    else:
        period_rows = []
        for period_month, period_end in periods:
            employee_logins = _employee_logins_with_month_shifts(
                active_logins,
                period_month,
            )
            rows = calculate_monthly_kpi(
                period_month,
                employee_logins=employee_logins,
                period_end=period_end,
                ensure_schema=False,
            )
            period_rows.append((period_end, rows))

    for period_end, rows in period_rows:
        for row in rows:
            employees[row['login']] = {
                'login': row['login'],
                'nickname': row['nickname'],
            }
        points.append(_analytics_point(period_end, rows))

    payload = {
        'mode': mode,
        'month': month[:7],
        'employees': sorted(
            employees.values(),
            key=lambda employee: employee['nickname'].lower(),
        ),
        'points': points,
    }
    with _analytics_cache_lock:
        expired_keys = [
            key
            for key, value in _analytics_cache.items()
            if time.monotonic() - value['created_at'] >= ANALYTICS_CACHE_SECONDS
        ]
        for key in expired_keys:
            _analytics_cache.pop(key, None)
        _analytics_cache[cache_key] = {
            'created_at': time.monotonic(),
            'payload': payload,
        }
        while len(_analytics_cache) > 12:
            oldest_key = min(
                _analytics_cache,
                key=lambda key: _analytics_cache[key]['created_at'],
            )
            _analytics_cache.pop(oldest_key, None)
    return jsonify(payload)


@app.get('/api/kpi/settings')
@require_owner
def api_kpi_settings():
    month = _validate_month(
        request.args.get('month') or date.today().strftime('%Y-%m')
    )
    return jsonify(get_kpi_settings(month))


@app.put('/api/kpi/settings')
@require_owner
def api_save_kpi_settings():
    payload = request.get_json(silent=True) or {}
    month = _validate_month(payload.get('month'))
    raw_metrics = payload.get('metrics')
    raw_clubs = payload.get('clubs')
    if not isinstance(raw_metrics, dict) or not isinstance(raw_clubs, dict):
        raise ValueError('Передайте нормы показателей и веса клубов')

    metrics = {
        str(metric): value
        for metric, value in raw_metrics.items()
    }
    clubs = {}
    for club, weights in raw_clubs.items():
        if not isinstance(weights, dict):
            raise ValueError('Для каждого клуба нужны веса будней и выходных')
        clubs[str(club)] = (
            weights.get('weekday_weight'),
            weights.get('weekend_weight'),
        )

    settings = save_kpi_settings(
        month,
        metrics,
        clubs,
        _actor_login(),
    )
    _clear_analytics_cache()
    return jsonify(settings)


@app.post('/api/penalties')
@require_manager
def api_add_penalty():
    payload = request.get_json(silent=True) or {}
    month = _validate_month(payload.get('month'))
    employee_login = str(payload.get('employee_login') or '').strip()
    reason = str(payload.get('reason') or '').strip()
    if not employee_login:
        raise ValueError('Выберите сотрудника')
    if employee_login.lower() not in set(_active_employee_logins()):
        raise ValueError('Сотрудник не найден или неактивен')
    penalty_id = add_penalty(
        employee_login,
        month,
        reason,
        _actor_login(),
        source='telegram_mini_app',
    )
    _clear_analytics_cache()
    return jsonify({'id': penalty_id, 'impact_pct': 0.10}), 201


@app.post('/api/penalties/<int:penalty_id>/cancel')
@require_manager
def api_cancel_penalty(penalty_id):
    payload = request.get_json(silent=True) or {}
    reason = str(payload.get('reason') or '').strip()
    if not cancel_penalty(penalty_id, _actor_login(), reason):
        return jsonify({'error': 'Активный штраф не найден.'}), 404
    _clear_analytics_cache()
    return jsonify({'status': 'cancelled'})


@app.post('/api/streams')
@require_manager
def api_set_stream():
    payload = request.get_json(silent=True) or {}
    month = _validate_month(payload.get('month'))
    employee_login = str(payload.get('employee_login') or '').strip()
    if not employee_login:
        raise ValueError('Выберите сотрудника')
    if employee_login.lower() not in set(_active_employee_logins()):
        raise ValueError('Сотрудник не найден или неактивен')
    set_monthly_stream(
        employee_login,
        month,
        bool(payload.get('enabled')),
        _actor_login(),
        source='telegram_mini_app',
    )
    _clear_analytics_cache()
    return jsonify({'status': 'saved'})


@app.post('/api/month-status')
@require_manager
def api_month_status():
    payload = request.get_json(silent=True) or {}
    month = _validate_month(payload.get('month'))
    is_closed = bool(payload.get('is_closed'))
    snapshot = _month_close_preview(month) if is_closed else None
    status = set_month_status(
        month,
        is_closed,
        _actor_login(),
        snapshot=snapshot,
    )
    _clear_analytics_cache()
    return jsonify(status)


@app.get('/api/month-close-check')
@require_manager
def api_month_close_check():
    month = _validate_month(request.args.get('month'))
    preview = _month_close_preview(month)
    return jsonify({
        'period_month': preview['period_month'],
        'date': preview['date'],
        'generated_at': preview['generated_at'],
        'summary': preview['summary'],
        'warnings': preview['warnings'],
    })


def main():
    initialize_kpi_calculation_schema()
    port = int(os.getenv('KPI_WEBAPP_PORT', '8080'))
    app.run(host='0.0.0.0', port=port, debug=False)


if __name__ == '__main__':
    main()
