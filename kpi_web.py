import calendar
import hashlib
import hmac
import json
import os
import sqlite3
import threading
import time
from datetime import UTC, date, datetime, timedelta
from functools import wraps
from urllib.parse import parse_qsl

from flask import Flask, g, jsonify, request, send_from_directory

from constants import TELEGRAM_API_KEY
from kpi_calculator import (
    add_penalty,
    calculate_daily_kpi_series,
    calculate_monthly_kpi,
    cancel_penalty,
    get_month_status,
    get_metric_entries,
    get_kpi_freshness,
    initialize_kpi_calculation_schema,
    list_penalties,
    set_month_status,
    set_monthly_stream,
)
from permissions import (
    ACTIVE_ROLES,
    ROLE_MANAGER,
    ROLE_NAMES,
    get_user,
)


DB_PATH = 'db/omgbot.sql'
STATIC_DIR = os.path.join(os.path.dirname(__file__), 'kpi_static')
AUTH_MAX_AGE_SECONDS = int(os.getenv('KPI_WEBAPP_AUTH_MAX_AGE', '86400'))
ANALYTICS_CACHE_SECONDS = 60
_analytics_cache = {}
_analytics_cache_lock = threading.Lock()

app = Flask(__name__, static_folder=None)
app.config['MAX_CONTENT_LENGTH'] = 32 * 1024


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


def _active_employee_logins():
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(
            '''
            SELECT lower(login)
            FROM users
            WHERE status IN (0, 1, 2, 3)
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


def _actor_login():
    return str(g.kpi_user.get('login') or '').strip().lower()


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
        'target': None,
        'needed': 0,
        'ratio': initiative_ratio,
        'weight': 0.10,
        'contribution_pct': initiative_ratio * 0.10,
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


@app.after_request
def add_security_headers(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    return response


@app.errorhandler(ValueError)
def handle_value_error(error):
    return jsonify({'error': str(error)}), 400


@app.get('/')
def index():
    return send_from_directory(STATIC_DIR, 'index.html')


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
    })


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
    employee_logins = _employee_logins_with_month_shifts(
        active_logins,
        month,
    )
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
    for row in rows:
        previous = previous_by_login.get(row['login'])
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
        row['explanation'] = _employee_explanation(row)
    penalties = list_penalties(month)
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
    status = set_month_status(
        month,
        bool(payload.get('is_closed')),
        _actor_login(),
    )
    return jsonify(status)


def main():
    initialize_kpi_calculation_schema()
    port = int(os.getenv('KPI_WEBAPP_PORT', '8080'))
    app.run(host='0.0.0.0', port=port, debug=False)


if __name__ == '__main__':
    main()
