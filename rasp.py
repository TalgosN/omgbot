from telebot import *
from constants import *
from sheets import *
import html
import requests
import json
import os
import random
from datetime import datetime, timedelta
import locale
import sqlite3
import threading
import pytz
from weather import get_weather
from permissions import ROLE_EMPLOYEE, require_role

locale.setlocale(locale.LC_ALL, 'ru_RU.UTF-8')

emojis = ['💀', '🤖', '🍓', '😎', '🤓', '🙄', '👽', '👻', '😈', '😇', '😅', '🤑', '😉', '🐯', '🌝', '🌚', '🥟']

shifton_chat_sync_lock = threading.Lock()
shifton_notifications_lock = threading.Lock()
shifton_employee_sync_lock = threading.Lock()
SHIFTON_DB_PATH = 'db/omgbot.sql'
OMG_SHIFT_RATE_SOURCES = ('omg_shift:employee', 'omg_shift:position')
SHIFTON_SCHEDULE_MAX_DAYS = 93
shifton_runtime_status = {
    "last_notification_check": None,
    "last_notification_sent": None,
    "last_notification_error": None,
    "last_chat_sync": None,
    "last_chat_sync_result": None,
    "last_employee_sync": None,
    "last_employee_sync_result": None,
    "last_employee_sync_error": None,
}

def moscow_timestamp():
    return datetime.now(pytz.timezone('Europe/Moscow')).strftime('%Y-%m-%d %H:%M:%S')

funclist_rasp=('📄 Расписание на сегодня','📑 Расписание на неделю', '⬅️ Вернуться')
funclist_rasp_week=('👨🏻‍💻 По сотрудникам','🗓 По датам', '🔴 По клубам','⬅️ Вернуться')

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def initialize_shifton_employee_schema():
    """Создаёт постоянную схему связи users с карточками OMG Shift."""
    conn = sqlite3.connect(SHIFTON_DB_PATH)
    try:
        with conn:
            user_columns = {row[1] for row in conn.execute('PRAGMA table_info(users)')}
            if 'omg_shift_employee_id' not in user_columns:
                conn.execute('ALTER TABLE users ADD COLUMN omg_shift_employee_id TEXT')
            conn.execute(
                '''CREATE UNIQUE INDEX IF NOT EXISTS idx_users_omg_shift_employee
                   ON users(omg_shift_employee_id)
                   WHERE omg_shift_employee_id IS NOT NULL'''
            )

            rate_columns = {row[1] for row in conn.execute('PRAGMA table_info(payroll_rates)')}
            if 'omg_shift_employee_id' not in rate_columns:
                conn.execute('ALTER TABLE payroll_rates ADD COLUMN omg_shift_employee_id TEXT')
            conn.execute(
                '''CREATE INDEX IF NOT EXISTS idx_payroll_rates_omg_employee_dates
                   ON payroll_rates(omg_shift_employee_id, valid_from, valid_to)'''
            )

            conn.execute(
                '''CREATE TABLE IF NOT EXISTS omg_shift_employees (
                    employee_id TEXT PRIMARY KEY,
                    telegram TEXT,
                    full_name TEXT NOT NULL,
                    phone TEXT,
                    start_date DATE,
                    end_date DATE,
                    archived INTEGER NOT NULL DEFAULT 0,
                    position_id TEXT,
                    position_title TEXT,
                    current_rate REAL,
                    rate_source TEXT,
                    manual_rate REAL,
                    booking_percent_enabled INTEGER NOT NULL DEFAULT 0,
                    booking_percent REAL,
                    raw_payload TEXT NOT NULL,
                    synced_at DATETIME NOT NULL
                )'''
            )
            conn.execute(
                '''CREATE INDEX IF NOT EXISTS idx_omg_shift_employees_telegram
                   ON omg_shift_employees(telegram)'''
            )
    finally:
        conn.close()


def _normalize_telegram(value):
    login = str(value or '').strip()
    if not login:
        return None
    return login if login.startswith('@') else f'@{login}'


def _parse_api_date(value, field, required=False):
    raw = str(value or '').strip()
    if not raw:
        if required:
            raise ValueError(f'OMG Shift не вернул {field}')
        return None
    try:
        return datetime.strptime(raw, '%Y-%m-%d').date()
    except ValueError as exc:
        raise ValueError(f'OMG Shift вернул некорректный {field}: {raw}') from exc


def _api_rate(value, field):
    try:
        rate = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'OMG Shift вернул некорректную ставку {field}') from exc
    if rate < 0:
        raise ValueError(f'OMG Shift вернул отрицательную ставку {field}')
    return rate


def fetch_shifton_employees(include_archived=True):
    """Получает и полностью проверяет каталог сотрудников до изменения БД."""
    response = requests.get(
        f"{SHIFTON_API_URL}/api/bot/employees",
        params={"includeArchived": "true"} if include_archived else None,
        headers={"Authorization": f"Bearer {SHIFTON_API_TOKEN}"},
        timeout=15,
    )
    payload = response.json()
    if not isinstance(payload, dict) or not payload.get('ok'):
        error = payload.get('error', 'invalid_response') if isinstance(payload, dict) else 'invalid_response'
        raise RuntimeError(f'OMG Shift не вернул сотрудников: {error}')
    employees = payload.get('employees')
    if not isinstance(employees, list):
        raise RuntimeError('OMG Shift вернул некорректный список сотрудников')

    validated = []
    employee_ids = set()
    active_telegrams = set()
    for source in employees:
        if not isinstance(source, dict):
            raise RuntimeError('OMG Shift вернул некорректную карточку сотрудника')
        employee = dict(source)
        employee_id = str(employee.get('id') or '').strip()
        full_name = str(employee.get('name') or '').strip()
        if not employee_id or not full_name:
            raise RuntimeError('OMG Shift вернул сотрудника без ID или ФИО')
        if employee_id in employee_ids:
            raise RuntimeError(f'OMG Shift вернул повторяющийся employee ID: {employee_id}')
        employee_ids.add(employee_id)

        employee['id'] = employee_id
        employee['name'] = full_name
        employee['telegram'] = _normalize_telegram(employee.get('telegram'))
        employee['archived'] = bool(employee.get('archived'))
        _parse_api_date(employee.get('startDate'), 'дату начала работы', required=True)
        _parse_api_date(employee.get('endDate'), 'дату окончания работы')
        if not employee['archived'] and employee['telegram']:
            telegram_key = employee['telegram'].lower()
            if telegram_key in active_telegrams:
                raise RuntimeError(f'OMG Shift вернул повторяющийся Telegram: {employee["telegram"]}')
            active_telegrams.add(telegram_key)
        validated.append(employee)
    return validated


def _rate_periods(employee, cutover_date):
    """Строит непересекающиеся интервалы ставки начиная с перехода на OMG Shift."""
    start_date = _parse_api_date(employee.get('startDate'), 'дату начала работы', required=True)
    end_date = _parse_api_date(employee.get('endDate'), 'дату окончания работы')
    period_start = max(start_date, cutover_date)
    if end_date and period_start > end_date:
        return []

    rate = employee.get('rate') or {}
    manual_rate = rate.get('manualOverride')
    if manual_rate not in (None, ''):
        return [(
            period_start,
            end_date,
            _api_rate(manual_rate, 'сотрудника'),
            'omg_shift:employee',
        )]

    position = employee.get('position') or {}
    history = []
    for item in position.get('rateHistory') or []:
        if not isinstance(item, dict):
            raise ValueError('OMG Shift вернул некорректную историю ставки должности')
        history.append((
            _parse_api_date(item.get('startDate'), 'дату ставки должности', required=True),
            _api_rate(item.get('rate'), 'должности'),
        ))
    history.sort(key=lambda item: item[0])

    effective_rate = None
    for history_date, history_rate in history:
        if history_date <= period_start:
            effective_rate = history_rate
    if effective_rate is None:
        fallback = position.get('baseRate')
        if fallback in (None, ''):
            fallback = rate.get('current')
        if fallback not in (None, ''):
            effective_rate = _api_rate(fallback, 'должности')
    if effective_rate is None:
        return []

    timeline = [(period_start, effective_rate)]
    timeline.extend(
        (history_date, history_rate)
        for history_date, history_rate in history
        if history_date > period_start and (not end_date or history_date <= end_date)
    )
    deduplicated = {}
    for history_date, history_rate in timeline:
        deduplicated[history_date] = history_rate
    timeline = sorted(deduplicated.items())

    periods = []
    for index, (valid_from, hourly_rate) in enumerate(timeline):
        next_start = timeline[index + 1][0] if index + 1 < len(timeline) else None
        valid_to = next_start - timedelta(days=1) if next_start else end_date
        if end_date and (valid_to is None or valid_to > end_date):
            valid_to = end_date
        periods.append((valid_from, valid_to, hourly_rate, 'omg_shift:position'))
    return periods


def _replace_shifton_employee_snapshot(conn, employees, synced_at):
    conn.execute('DELETE FROM omg_shift_employees')
    for employee in employees:
        position = employee.get('position') or {}
        rate = employee.get('rate') or {}
        booking = employee.get('bookingPercent') or {}
        current_rate = rate.get('current')
        manual_rate = rate.get('manualOverride')
        conn.execute(
            '''INSERT INTO omg_shift_employees (
                   employee_id, telegram, full_name, phone, start_date, end_date,
                   archived, position_id, position_title, current_rate,
                   rate_source, manual_rate, booking_percent_enabled,
                   booking_percent, raw_payload, synced_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (
                employee['id'], employee.get('telegram'), employee['name'],
                employee.get('phone'), employee.get('startDate'),
                employee.get('endDate'), int(employee.get('archived', False)),
                position.get('id'), position.get('title'),
                float(current_rate) if current_rate not in (None, '') else None,
                rate.get('source'),
                float(manual_rate) if manual_rate not in (None, '') else None,
                int(bool(booking.get('enabled'))), booking.get('percent'),
                json.dumps(employee, ensure_ascii=False), synced_at,
            ),
        )


def _sync_shifton_payroll_rates(conn, employees, today):
    conn.execute(
        "DELETE FROM payroll_rates WHERE source IN (?, ?)",
        OMG_SHIFT_RATE_SOURCES,
    )
    cutovers = {
        str(login or '').strip().lower(): datetime.strptime(first_date, '%Y-%m-%d').date()
        for login, first_date in conn.execute(
            '''SELECT shift_login, MIN(date(dt_shift))
               FROM shifts
               WHERE source='omg_shift' AND shift_login IS NOT NULL
               GROUP BY lower(shift_login)'''
        )
        if login and first_date
    }

    inserted = 0
    conflicts = []
    seen_telegrams = set()
    ordered = sorted(employees, key=lambda item: bool(item.get('archived')))
    for employee in ordered:
        login = _normalize_telegram(employee.get('telegram'))
        if not login or login.lower() in seen_telegrams:
            continue
        seen_telegrams.add(login.lower())
        cutover_date = cutovers.get(login.lower(), today)
        for valid_from, valid_to, hourly_rate, source in _rate_periods(employee, cutover_date):
            existing = conn.execute(
                '''SELECT source FROM payroll_rates
                   WHERE lower(login)=lower(?) AND club='*' AND date(valid_from)=date(?)''',
                (login, valid_from.isoformat()),
            ).fetchone()
            if existing:
                conflicts.append({
                    'login': login,
                    'valid_from': valid_from.isoformat(),
                    'source': existing[0],
                })
                continue
            conn.execute(
                '''INSERT INTO payroll_rates (
                       login, club, hourly_rate, valid_from, valid_to, source,
                       omg_shift_employee_id
                   ) VALUES (?, '*', ?, ?, ?, ?, ?)''',
                (
                    login, hourly_rate, valid_from.isoformat(),
                    valid_to.isoformat() if valid_to else None,
                    source, employee['id'],
                ),
            )
            inserted += 1
    return inserted, conflicts


def _sync_shifton_employees():
    """Выполняет одну атомарную синхронизацию сотрудников и ставок."""
    employees = fetch_shifton_employees(include_archived=True)
    initialize_shifton_employee_schema()
    synced_at = moscow_timestamp()
    today = datetime.now(pytz.timezone('Europe/Moscow')).date()
    active = [employee for employee in employees if not employee.get('archived')]
    linked = 0
    changed = 0
    unlinked = []
    identity_conflicts = []
    access_mismatches = []

    conn = sqlite3.connect(SHIFTON_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            _replace_shifton_employee_snapshot(conn, employees, synced_at)
            from account import apply_omg_employee_identity
            for employee in active:
                conn.execute('SAVEPOINT omg_employee_identity')
                try:
                    result = apply_omg_employee_identity(conn, employee)
                except (ValueError, sqlite3.IntegrityError) as error:
                    conn.execute('ROLLBACK TO omg_employee_identity')
                    conn.execute('RELEASE omg_employee_identity')
                    identity_conflicts.append({
                        'employee_id': employee['id'],
                        'telegram': employee.get('telegram'),
                        'error': str(error),
                    })
                    continue
                conn.execute('RELEASE omg_employee_identity')
                if result['status'] == 'linked':
                    linked += 1
                    changed += int(result['changed'])
                else:
                    unlinked.append({
                        'employee_id': employee['id'],
                        'telegram': employee.get('telegram'),
                        'name': employee['name'],
                    })

            access_rows = conn.execute(
                '''SELECT employee.employee_id, employee.telegram,
                          employee.archived, user.status
                   FROM omg_shift_employees AS employee
                   JOIN users AS user
                     ON user.omg_shift_employee_id=employee.employee_id
                   WHERE (employee.archived=0 AND user.status=-1)
                      OR (employee.archived=1 AND COALESCE(user.status, -1)<>-1)'''
            ).fetchall()
            access_mismatches = [
                {
                    'employee_id': row['employee_id'],
                    'telegram': row['telegram'],
                    'error': (
                        'архивирован в OMG Shift, но имеет доступ к боту'
                        if row['archived']
                        else 'активен в OMG Shift, но заблокирован в боте'
                    ),
                }
                for row in access_rows
            ]

            rate_rows, rate_conflicts = _sync_shifton_payroll_rates(
                conn, employees, today
            )
    finally:
        conn.close()

    google_errors = []
    if changed:
        from account import sync_google_dependencies
        google_errors = sync_google_dependencies(full=True)

    result = {
        'total': len(employees),
        'active': len(active),
        'archived': len(employees) - len(active),
        'linked': linked,
        'changed': changed,
        'unlinked': unlinked,
        'identity_conflicts': identity_conflicts,
        'access_mismatches': access_mismatches,
        'rate_rows': rate_rows,
        'rate_conflicts': rate_conflicts,
        'google_errors': google_errors,
    }
    shifton_runtime_status['last_employee_sync'] = synced_at
    shifton_runtime_status['last_employee_sync_error'] = None
    shifton_runtime_status['last_employee_sync_result'] = (
        f"{linked}/{len(active)} связаны, {rate_rows} периодов ставок, "
        f"{len(unlinked) + len(identity_conflicts) + len(access_mismatches)} "
        f"требуют внимания"
    )
    return result


def sync_shifton_employees():
    """Не допускает параллельных синхронизаций и сохраняет результат проверки."""
    if not shifton_employee_sync_lock.acquire(blocking=False):
        raise RuntimeError('Синхронизация сотрудников OMG Shift уже выполняется')
    try:
        return _sync_shifton_employees()
    except Exception as error:
        shifton_runtime_status['last_employee_sync'] = moscow_timestamp()
        shifton_runtime_status['last_employee_sync_result'] = None
        shifton_runtime_status['last_employee_sync_error'] = str(error)
        raise
    finally:
        shifton_employee_sync_lock.release()

def fetch_schedule_from_api(date_iso):
    """Делает GET запрос к новому API на конкретную дату (YYYY-MM-DD)"""
    url = f"{SHIFTON_API_URL}/api/bot/schedule?date={date_iso}"
    headers = {"Authorization": f"Bearer {SHIFTON_API_TOKEN}"}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        return response.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}


def fetch_schedule_range_from_api(date_from, date_to):
    """Получает включительный диапазон, разбивая периоды длиннее 93 дней."""
    try:
        start_date = datetime.strptime(str(date_from), '%Y-%m-%d').date()
        end_date = datetime.strptime(str(date_to), '%Y-%m-%d').date()
    except ValueError:
        return {"ok": False, "error": "bad_date"}
    if end_date < start_date:
        return {"ok": False, "error": "date_range_reversed"}

    headers = {"Authorization": f"Bearer {SHIFTON_API_TOKEN}"}
    days = []
    seen_dates = set()
    chunk_start = start_date
    try:
        while chunk_start <= end_date:
            chunk_end = min(
                chunk_start + timedelta(days=SHIFTON_SCHEDULE_MAX_DAYS - 1),
                end_date,
            )
            response = requests.get(
                f"{SHIFTON_API_URL}/api/bot/schedule",
                params={
                    "dateFrom": chunk_start.isoformat(),
                    "dateTo": chunk_end.isoformat(),
                },
                headers=headers,
                timeout=15,
            )
            payload = response.json()
            if not isinstance(payload, dict) or not payload.get('ok'):
                error = payload.get('error', 'invalid_response') if isinstance(payload, dict) else 'invalid_response'
                return {"ok": False, "error": error}
            chunk_days = payload.get('days')
            if not isinstance(chunk_days, list):
                return {"ok": False, "error": "invalid_response"}
            for day in chunk_days:
                if not isinstance(day, dict):
                    return {"ok": False, "error": "invalid_response"}
                day_date = str(day.get('date') or '')
                if day_date in seen_dates:
                    continue
                try:
                    parsed_date = datetime.strptime(day_date, '%Y-%m-%d').date()
                except ValueError:
                    return {"ok": False, "error": "invalid_response"}
                if start_date <= parsed_date <= end_date:
                    seen_dates.add(day_date)
                    days.append(day)
            chunk_start = chunk_end + timedelta(days=1)
    except Exception as e:
        return {"ok": False, "error": str(e)}

    days.sort(key=lambda day: day['date'])
    return {
        "ok": True,
        "dateFrom": start_date.isoformat(),
        "dateTo": end_date.isoformat(),
        "days": days,
    }


def format_shifton_notification(text):
    """Безопасно оформляет полученный от OMG Shift обычный текст."""
    notification_text = str(text or '').strip() or 'Расписание изменилось.'
    return (
        '🔔 <b>Уведомление OMG Shift</b>\n\n'
        f'<blockquote>{html.escape(notification_text)}</blockquote>\n\n'
        '📅 <i>Проверь актуальное расписание перед сменой.</i>'
    )

def register_shifton_chat(telegram_tag, chat_id):
    """Привязывает личный Telegram-чат сотрудника к его карточке в OMG Shift."""
    url = f"{SHIFTON_API_URL}/api/bot/register-chat"
    headers = {
        "Authorization": f"Bearer {SHIFTON_API_TOKEN}",
        "Content-Type": "application/json"
    }
    try:
        response = requests.post(
            url,
            json={"telegram": telegram_tag, "chatId": chat_id},
            headers=headers,
            timeout=10
        )
        return response.json()
    except Exception as e:
        return {"ok": False, "error": "request_failed", "details": str(e)}

def sync_shifton_notification_chats():
    """Передаёт в OMG Shift Telegram chatid всех действующих сотрудников."""
    try:
        conn = sqlite3.connect('db/omgbot.sql')
        cur = conn.cursor()
        cur.execute("""
            SELECT login, chatid
            FROM users
            WHERE COALESCE(status, 0) <> -1
              AND login IS NOT NULL AND login <> ''
              AND chatid IS NOT NULL AND chatid <> ''
        """)
        users = cur.fetchall()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Ошибка чтения чатов для OMG Shift: {e}")
        return

    synced = 0
    errors = 0
    identity_changed = False
    for login, chat_id in users:
        telegram_tag = login if str(login).startswith('@') else f"@{login}"
        try:
            chat_id = int(chat_id)
        except (TypeError, ValueError):
            errors += 1
            print(f"Некорректный chatid для {telegram_tag}: {chat_id}")
            continue

        result = register_shifton_chat(telegram_tag, chat_id)
        if result.get("ok"):
            synced += 1
            try:
                from account import apply_omg_identity
                identity = apply_omg_identity(chat_id, telegram_tag, result.get("employee"))
                identity_changed = identity_changed or identity["changed"]
            except Exception as e:
                errors += 1
                print(f"Ошибка синхронизации ФИО OMG Shift для {telegram_tag}: {e}")
        else:
            errors += 1
            print(f"Ошибка регистрации чата OMG Shift для {telegram_tag}: {result.get('error', 'unknown_error')}")

    if identity_changed:
        from account import sync_google_dependencies
        google_errors = sync_google_dependencies(full=True)
        errors += len(google_errors)
        for error in google_errors:
            print(f"Ошибка синхронизации профиля с Google Sheets: {error}")

    print(f"Синхронизация чатов OMG Shift завершена: {synced} успешно, {errors} ошибок")
    shifton_runtime_status["last_chat_sync"] = moscow_timestamp()
    shifton_runtime_status["last_chat_sync_result"] = f"{synced} успешно, {errors} ошибок"

def start_shifton_chat_sync():
    """Запускает синхронизацию сотрудников и чатов в фоновом потоке."""
    if not shifton_chat_sync_lock.acquire(blocking=False):
        return

    def worker():
        try:
            try:
                sync_shifton_employees()
            except Exception as e:
                shifton_runtime_status["last_employee_sync"] = moscow_timestamp()
                shifton_runtime_status["last_employee_sync_error"] = str(e)
                shifton_runtime_status["last_employee_sync_result"] = None
                print(f"Ошибка синхронизации сотрудников OMG Shift: {e}")
            sync_shifton_notification_chats()
        finally:
            shifton_chat_sync_lock.release()

    threading.Thread(target=worker, daemon=True).start()

def claim_shifton_notification():
    """Забирает одно ожидающее уведомление об изменении расписания."""
    url = f"{SHIFTON_API_URL}/api/bot/notifications/claim"
    headers = {"Authorization": f"Bearer {SHIFTON_API_TOKEN}"}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        return response.json()
    except Exception as e:
        return {"ok": False, "error": "request_failed", "details": str(e)}

def complete_shifton_notification(notification_id, success, error=""):
    """Сообщает OMG Shift результат отправки уведомления в Telegram."""
    url = f"{SHIFTON_API_URL}/api/bot/notifications/complete"
    headers = {
        "Authorization": f"Bearer {SHIFTON_API_TOKEN}",
        "Content-Type": "application/json"
    }
    try:
        response = requests.post(
            url,
            json={"id": notification_id, "success": bool(success), "error": error},
            headers=headers,
            timeout=10
        )
        return response.json()
    except Exception as e:
        return {"ok": False, "error": "request_failed", "details": str(e)}

def send_pending_shifton_notifications(bot, limit=10):
    """Отправляет ожидающие уведомления OMG Shift сотрудникам."""
    for _ in range(limit):
        result = claim_shifton_notification()
        shifton_runtime_status["last_notification_check"] = moscow_timestamp()
        if not result.get("ok"):
            error = result.get('error', 'unknown_error')
            shifton_runtime_status["last_notification_error"] = error
            print(f"Ошибка получения уведомлений OMG Shift: {error}")
            return

        shifton_runtime_status["last_notification_error"] = None
        notification = result.get("notification")
        if not notification:
            return

        notification_id = notification.get("id")
        try:
            bot.send_message(
                notification.get("chatId"),
                format_shifton_notification(notification.get("text")),
                parse_mode='HTML',
                disable_web_page_preview=True,
            )
            complete_shifton_notification(notification_id, True)
            shifton_runtime_status["last_notification_sent"] = moscow_timestamp()
            print(f"Уведомление OMG Shift отправлено: {notification_id}")
        except Exception as e:
            complete_shifton_notification(notification_id, False, str(e))
            shifton_runtime_status["last_notification_error"] = str(e)
            print(f"Ошибка отправки уведомления OMG Shift {notification_id}: {e}")

def get_shifton_runtime_status():
    return dict(shifton_runtime_status)

def start_shifton_notifications_check(bot):
    """Запускает обработку очереди в фоне и не допускает параллельных проверок."""
    if not shifton_notifications_lock.acquire(blocking=False):
        return

    def worker():
        try:
            send_pending_shifton_notifications(bot)
        finally:
            shifton_notifications_lock.release()

    threading.Thread(target=worker, daemon=True).start()

def last_monday(datetime_str):
    dt = datetime.strptime(datetime_str, '%Y-%m-%d %H:%M:%S')
    days_since_monday = dt.weekday()
    last_monday_date = dt - timedelta(days=days_since_monday)
    last_monday_date = last_monday_date.replace(hour=0, minute=0, second=0)
    return last_monday_date

# --- ОСНОВНАЯ ЛОГИКА БОТА ---

def rasp(message, bot):
    if not require_role(message, bot, ROLE_EMPLOYEE):
        return
    bot.send_message(message.chat.id, f'Этот раздел посвящен расписанию и всё что с ним связано!')
    markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add(*funclist_rasp)
    bot.send_message(message.chat.id, 'Что вы хотите сделать? 👀', reply_markup=markup)
    bot.register_next_step_handler(message, func_rasp, bot)

def func_rasp(message, bot):
    if not require_role(message, bot, ROLE_EMPLOYEE):
        return
    if message.text == '📄 Расписание на сегодня':
        try:
            today_date = datetime.now(pytz.timezone('Europe/Moscow'))
            today_text = get_today_schedule(today_date.strftime("%Y-%m-%d"))
            bot.send_message(message.chat.id, today_text)
            
            markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
            markup.add(*funclist_rasp)
            bot.send_message(message.chat.id, 'Что вы хотите сделать? 👀', reply_markup=markup)
            bot.register_next_step_handler(message, func_rasp, bot)
        except Exception as e:
            bot.send_message(message.chat.id, 'Что-то пошло не так! Перешлите ошибку ниже техническому специалисту')
            bot.send_message(message.chat.id, str(e))

    elif message.text == '📑 Расписание на неделю':
        markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
        markup.add(*funclist_rasp_week)
        bot.send_message(message.chat.id, f'Выбери в каком формате ты хочешь получить расписание', reply_markup=markup)
        bot.register_next_step_handler(message, handle_data, bot)
        
    elif message.text == '⬅️ Вернуться':
        returnback(message, bot)
    else:
        markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
        markup.add(*funclist_rasp)
        bot.send_message(message.chat.id, 'Что вы хотите сделать? 👀', reply_markup=markup)
        bot.register_next_step_handler(message, func_rasp, bot)

def returnback(message, bot):
    from menu import hello
    hello(message.chat.id, bot)

def get_today_schedule(date_iso):
    """Получение расписания на сегодня (1 запрос)"""
    data = fetch_schedule_from_api(date_iso)
    
    today = datetime.now(pytz.timezone('Europe/Moscow')).strftime('%d.%m.%Y')
    weekday = datetime.now(pytz.timezone('Europe/Moscow')).strftime('%A')

    full_text = f'{today}, {weekday.capitalize()}\n\n'
    full_text += f'{get_weather()}\n\n'

    if not data.get("ok"):
        return full_text + f"⚠️ Ошибка получения расписания: {data.get('error', 'API недоступен')}"

    locations = data.get("locations", [])
    
    for club in get_schedule_locations():
        full_text += f'{club["emoji"]} {club["name"]}\n'
        
        # Ищем локацию в ответе API
        loc_data = next(
            (loc for loc in locations if loc.get("title") == club['source_name']),
            None,
        )
        
        if loc_data and loc_data.get("shifts"):
            for shift in loc_data["shifts"]:
                name = shift.get("employee", "СВОБОДНАЯ СМЕНА")
                tg = shift.get("telegram", "")
                
                display_name = f"{name} ({tg})" if tg else name
                start, end = shift.get("start"), shift.get("end")
                
                full_text += f'{display_name} c {start} до {end}\n'
        
        full_text += '\n'

    return full_text

# --- ЛОГИКА НЕДЕЛИ ---

def get_week_data(start_dt):
    """Получает всю неделю одним диапазонным запросом."""
    week_shifts = []
    end_dt = start_dt + timedelta(days=6)
    data = fetch_schedule_range_from_api(
        start_dt.strftime('%Y-%m-%d'),
        end_dt.strftime('%Y-%m-%d'),
    )
    if not data.get("ok"):
        return week_shifts

    for day in data.get("days", []):
        current_dt = datetime.strptime(day["date"], '%Y-%m-%d')
        for loc in day.get("locations", []):
            loc_title = loc.get("title", "Неизвестно")
            for shift in loc.get("shifts", []):
                week_shifts.append({
                    "date_dt": current_dt,
                    "day_str": current_dt.strftime('%d.%m, %A').capitalize(),
                    "location": loc_title,
                    "employee": shift.get("employee", "СВОБОДНАЯ СМЕНА"),
                    "start": shift.get("start", ""),
                    "end": shift.get("end", "")
                })
    return week_shifts

def get_week_by_club(date_user):
    date_start_dt = datetime.strptime(date_user, '%d.%m.%Y')
    start_dt = last_monday(date_start_dt.strftime('%Y-%m-%d 00:00:00'))
    
    week_shifts = get_week_data(start_dt)
    
    full_text = f"🗓 <b>Расписание на неделю {start_dt.strftime('%d.%m')} - {(start_dt + timedelta(days=6)).strftime('%d.%m')}</b>\n\n"

    for club in get_schedule_locations():
        club_shifts = [
            shift for shift in week_shifts
            if shift["location"] == club['source_name']
        ]
        if not club_shifts: continue
            
        full_text += f'{club["emoji"]} <b>{club["name"]}</b>\n'
        
        shifts_by_day = {}
        for s in club_shifts:
            day_str = s["day_str"]
            if day_str not in shifts_by_day:
                shifts_by_day[day_str] = []
                
            time_str = f'с {s["start"]} до {s["end"]}'
            shifts_by_day[day_str].append(f'  └ {s["employee"]} {time_str}')
            
        for day, shifts in shifts_by_day.items():
            full_text += f'📅 {day}:\n'
            full_text += "\n".join(shifts) + "\n"
        full_text += '\n'        
        
    return full_text

def get_week_by_day(date_user):
    date_start_dt = datetime.strptime(date_user, '%d.%m.%Y')
    start_dt = last_monday(date_start_dt.strftime('%Y-%m-%d 00:00:00'))
    
    week_shifts = get_week_data(start_dt)
    
    full_text = f"🗓 <b>Расписание на неделю {start_dt.strftime('%d.%m')} - {(start_dt + timedelta(days=6)).strftime('%d.%m')}</b>\n\n"

    for p in range(7):
        current_dt = start_dt + timedelta(days=p)
        day_str = current_dt.strftime('%d.%m, %A').capitalize()
        
        day_has_shifts = False
        day_text = f"📅 <b>{day_str}</b>\n"
        
        for club in get_schedule_locations():
            club_shifts = [
                shift for shift in week_shifts
                if shift["location"] == club['source_name']
                and shift["date_dt"].date() == current_dt.date()
            ]
            if not club_shifts: continue
                
            day_has_shifts = True
            day_text += f' {club["emoji"]} {club["name"]}:\n'
            for s in club_shifts:
                time_str = f'с {s["start"]} до {s["end"]}'
                day_text += f'  └ {s["employee"]} {time_str}\n'
        
        if day_has_shifts:
            full_text += f"{day_text}\n"

    return full_text

def get_week_by_employee(date_user):
    date_start_dt = datetime.strptime(date_user, '%d.%m.%Y')
    start_dt = last_monday(date_start_dt.strftime('%Y-%m-%d 00:00:00'))
    
    week_shifts = get_week_data(start_dt)
    schedule_locations = {
        club['source_name']: club for club in get_schedule_locations()
    }
    
    full_text = f"🗓 <b>Расписание на неделю {start_dt.strftime('%d.%m')} - {(start_dt + timedelta(days=6)).strftime('%d.%m')}</b>\n\n"

    shifts_by_emp = {}
    for s in week_shifts:
        name = s["employee"]
        if name not in shifts_by_emp:
            shifts_by_emp[name] = {}
        
        day_str = s["day_str"]
        loc = s["location"]
        club = schedule_locations.get(loc)
        loc_color = club['emoji'] if club else ''
        loc_name = club['name'] if club else loc
        time_str = f'с {s["start"]} до {s["end"]}'
        
        if day_str not in shifts_by_emp[name]:
            shifts_by_emp[name][day_str] = []
            
        shifts_by_emp[name][day_str].append(f'  └ {time_str} {loc_color} {loc_name}')

    for emp, days_dict in shifts_by_emp.items():
        icon = "👤" if emp == "СВОБОДНАЯ СМЕНА" else random.choice(emojis)
        full_text += f'{icon} <b>{emp}</b>\n'
        for day_str, shifts in days_dict.items():
            full_text += f'📅 {day_str}:\n'
            full_text += "\n".join(shifts) + "\n"
        full_text += '\n'

    return full_text

# --- МАРШРУТИЗАЦИЯ --- (Остается практически без изменений, просто вырезал для экономии места, логика handle_data, get_week и send_long_text остается старой)

# --- МАРШРУТИЗАЦИЯ ---

def send_long_text(chat_id, text, bot):
    """Умная разбивка длинного сообщения с поддержкой HTML"""
    max_length = 4000
    if len(text) <= max_length:
        bot.send_message(chat_id, text, parse_mode='HTML')
        return
        
    parts = text.split('\n\n')
    msg = ""
    for part in parts:
        if len(msg) + len(part) + 2 > max_length:
            bot.send_message(chat_id, msg, parse_mode='HTML')
            msg = part + "\n\n"
        else:
            msg += part + "\n\n"
            
    if msg.strip():
        bot.send_message(chat_id, msg, parse_mode='HTML')

def handle_data(message, bot):
    if not require_role(message, bot, ROLE_EMPLOYEE):
        return
    if message.text == '⬅️ Вернуться':
        markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
        markup.add(*funclist_rasp)
        bot.send_message(message.chat.id, 'Что вы хотите сделать? 👀', reply_markup=markup)
        bot.register_next_step_handler(message, func_rasp, bot)
        
    elif message.text in funclist_rasp_week:
        sched_type = message.text
        
        markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
        markup.add('Текущая неделя', 'Следующая неделя')
        markup.add('Прошлая неделя', '⬅️ Вернуться')
        
        bot.send_message(message.chat.id, 'Выбери нужную неделю кнопкой или пришли любую дату в формате 15.04.2024 📆', reply_markup=markup)
        bot.register_next_step_handler(message, get_week, sched_type, bot)
    else:
        markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
        markup.add(*funclist_rasp)
        bot.send_message(message.chat.id, 'Что вы хотите сделать? 👀', reply_markup=markup)
        bot.register_next_step_handler(message, func_rasp, bot)

def get_week(message, sched_type, bot):
    if not require_role(message, bot, ROLE_EMPLOYEE):
        return
    if message.text == '⬅️ Вернуться':
        markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
        markup.add(*funclist_rasp)
        bot.send_message(message.chat.id, 'Что вы хотите сделать? 👀', reply_markup=markup)
        bot.register_next_step_handler(message, func_rasp, bot)
        return

    quick_ranges = ['Текущая неделя', 'Следующая неделя', 'Прошлая неделя']
    
    try:
        # Обработка смарт-кнопок
        if message.text in quick_ranges:
            today = datetime.now(pytz.timezone('Europe/Moscow'))
            
            if message.text == 'Текущая неделя':
                target_date = today
            elif message.text == 'Следующая неделя':
                target_date = today + timedelta(days=7)
            elif message.text == 'Прошлая неделя':
                target_date = today - timedelta(days=7)
                
            user_date = target_date.strftime('%d.%m.%Y')
            
        # Обработка ручного ввода
        else:
            user_date_dt = datetime.strptime(message.text, '%d.%m.%Y')
            user_date = user_date_dt.strftime('%d.%m.%Y')

        # Убираем клавиатуру на время загрузки
        bot.send_message(message.chat.id, f"⏳ Собираю расписание... ({user_date})", reply_markup=telebot.types.ReplyKeyboardRemove())

        # Получение расписания
        if sched_type == '👨🏻‍💻 По сотрудникам':
            mess_text = get_week_by_employee(user_date)
        elif sched_type == '🗓 По датам':
            mess_text = get_week_by_day(user_date)
        elif sched_type == '🔴 По клубам':
            mess_text = get_week_by_club(user_date)
            
        # Используем новую функцию отправки для защиты от лимитов Телеграма
        send_long_text(message.chat.id, mess_text, bot)
        
        # Возвращаем меню
        markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
        markup.add(*funclist_rasp)
        bot.send_message(message.chat.id, 'Что вы хотите сделать дальше? 👀', reply_markup=markup)
        bot.register_next_step_handler(message, func_rasp, bot)
    
    except Exception as e:
        bot.send_message(message.chat.id, f'❌ Ошибка: {e}\nПерешлите её техническому специалисту.')
        
        markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
        markup.add('Текущая неделя', 'Следующая неделя', 'Прошлая неделя', '⬅️ Вернуться')
        bot.send_message(message.chat.id, 'Попробуйте нажать кнопку или прислать дату в формате 15.04.2024:', reply_markup=markup)
        bot.register_next_step_handler(message, get_week, sched_type, bot)
