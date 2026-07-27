import hashlib
import hmac
import json
import os
import sqlite3
import time
from datetime import date, datetime
from functools import wraps
from urllib.parse import parse_qsl

from flask import Flask, g, jsonify, request, send_from_directory

from constants import TELEGRAM_API_KEY
from kpi_calculator import (
    add_penalty,
    calculate_monthly_kpi,
    cancel_penalty,
    get_month_status,
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

app = Flask(__name__, static_folder=None)
app.config['MAX_CONTENT_LENGTH'] = 32 * 1024


def _validate_month(value):
    raw = str(value or '').strip()
    try:
        return datetime.strptime(raw, '%Y-%m').date().replace(day=1).isoformat()
    except ValueError as error:
        raise ValueError('Месяц должен быть в формате YYYY-MM') from error


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


def _actor_login():
    return str(g.kpi_user.get('login') or '').strip().lower()


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
    rows = calculate_monthly_kpi(
        month,
        employee_logins=_active_employee_logins(),
    )
    penalties = list_penalties(month)
    return jsonify({
        'month': month[:7],
        'month_status': get_month_status(month),
        'employees': rows,
        'penalties': penalties,
    })


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
    return jsonify({'id': penalty_id, 'impact_pct': 0.10}), 201


@app.post('/api/penalties/<int:penalty_id>/cancel')
@require_manager
def api_cancel_penalty(penalty_id):
    payload = request.get_json(silent=True) or {}
    reason = str(payload.get('reason') or '').strip()
    if not cancel_penalty(penalty_id, _actor_login(), reason):
        return jsonify({'error': 'Активный штраф не найден.'}), 404
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
