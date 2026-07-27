import sqlite3
import pytz
from telebot import *
from constants import *
import pygsheets
from datetime import datetime, timedelta
import pandas as pd
import requests
import json
from permissions import ROLE_OWNER, role_of
import sql_scripts
from sheets import *
import random
import os
import re
import time
from kpi_calculator import (
    calculate_monthly_kpi,
    compare_with_sheet,
    import_sheet_penalty,
    initialize_kpi_calculation_schema,
    mirror_legacy_penalty,
    set_monthly_stream,
)


# Словари
action = {'#продление':'afterparty', '#инициатива':'initiative'}
bonus = {'#серт':'sert', '#абик':'abik'}

KPI_SUCCESS = "success"
KPI_REMOTE_SUCCESS = "remote_success"
KPI_IGNORED = "ignored"
KPI_INVALID = "invalid"
KPI_ERROR = "error"
KPI_SAVED_ERROR = "saved_error"

NUMBER_RE = re.compile(r"^\s*([0-9]+(?:[,.][0-9]+)?)(?=\s|$)")
LEGACY_BIRTHDAY_AMOUNT = 500
HASHTAG_RULES_CACHE_SECONDS = 60
_hashtag_rules_cache = {}
_hashtag_rules_cache_until = 0.0

# ==========================================
# 1. ЗАГРУЗКА И ВЫГРУЗКА ДАННЫХ И СМЕН
# ==========================================

def read_ank_table():
    conn = sqlite3.connect('db/omgbot.sql')
    cur = conn.cursor()
    cur.execute('CREATE TABLE IF NOT EXISTS anketi (ID INTEGER PRIMARY KEY AUTOINCREMENT, id_ank integer, dt_ank date, club_ank varchar(50))')
    cur.execute('DELETE FROM anketi')
    conn.commit()

    c = pygsheets.authorize(service_file='key/omgbot-430116-e9a4d9c69b7f.json')
    sh = c.open('Клиенты, серты, абики, логины, игры, скидки')
    wks = sh.worksheet_by_title('База Клиентов')
    
    ids = wks.get_values(start='B', end='B', returnas='matrix')
    club_ank = wks.get_values(start='C', end='C', returnas='matrix')
    dt_ank = wks.get_values(start='K', end='K', returnas='matrix')

    df_ids = pd.DataFrame(ids, columns=['ID'])
    df_club_ank = pd.DataFrame(club_ank, columns=['Club'])
    df_club_ank['Club'] = df_club_ank['Club'].str.replace('Мариэль', 'Марьино', case=False, regex=False)
    df_dt_ank = pd.DataFrame(dt_ank, columns=['Date'])

    df_combined = pd.concat([df_ids, df_club_ank, df_dt_ank], axis=1)
    df_combined['Date'] = pd.to_datetime(df_combined['Date'], format='%d.%m.%Y', errors='coerce')

    three_months_ago = pd.Timestamp.now() - pd.DateOffset(months=3)
    df_filtered = df_combined[df_combined['Date'] >= three_months_ago]

    for index, row in df_filtered.iterrows():
        cur.execute("INSERT INTO anketi (id_ank, dt_ank, club_ank) VALUES (?, ?, ?)", (row['ID'], str(row['Date']), row['Club']))

    conn.commit()
    cur.close()
    conn.close()
    return df_combined

def write_data(data, table, sheet):
    rows = [list(row) for row in data]
    row_width = len(rows[0]) if rows else 0
    if any(len(row) != row_width for row in rows):
        raise ValueError(f'Cannot write non-rectangular data to {table}/{sheet}')

    c = pygsheets.authorize(service_file='key/omgbot-430116-e9a4d9c69b7f.json')
    sh = c.open(table)
    wks = sh.worksheet_by_title(sheet)
    old_rows = wks.rows

    if rows:
        # extend=True не даёт выгрузке оборваться, когда данных стало больше,
        # чем строк в текущей сетке Google Sheets.
        wks.update_values('A2', rows, extend=True)

        last_data_row = len(rows) + 1
        if row_width < 6:
            start_column = chr(ord('A') + row_width)
            wks.get_values(
                start=f'{start_column}2',
                end=f'F{last_data_row}',
                returnas='range',
            ).clear()

        if old_rows > last_data_row:
            wks.get_values(
                start=f'A{last_data_row + 1}',
                end=f'F{old_rows}',
                returnas='range',
            ).clear()
    elif old_rows >= 2:
        wks.get_values(start='A2', end=f'F{old_rows}', returnas='range').clear()

def fetch_omg_shift_rows(start_date):
    """Сначала целиком получает окно расписания, не изменяя локальную БД."""
    schedule_list = []
    seen_shifts = set()
    headers = {"Authorization": f"Bearer {SHIFTON_API_TOKEN}"}

    for p in range(15):
        current_dt = start_date + pd.DateOffset(days=p)
        date_iso = current_dt.strftime('%Y-%m-%d')
        
        response = requests.get(
            f"{SHIFTON_API_URL}/api/bot/schedule?date={date_iso}",
            headers=headers,
            timeout=5,
        )
        response.raise_for_status()
        resp = response.json()
        if not isinstance(resp, dict) or not resp.get("ok"):
            error = resp.get("error", "invalid_response") if isinstance(resp, dict) else "invalid_response"
            raise RuntimeError(f"OMG Shift не вернул расписание за {date_iso}: {error}")

        for loc in resp.get("locations", []):
            club = loc.get("title", "Неизвестно")
            for shift in loc.get("shifts", []):
                emp_name = shift.get("employee", "Неизвестно")
                start_t = shift.get("start")
                end_t = shift.get("end")

                t1 = datetime.strptime(start_t, "%H:%M")
                t2 = datetime.strptime(end_t, "%H:%M")
                if t2 < t1:
                    t2 += timedelta(days=1)
                dur = round((t2 - t1).total_seconds() / 3600, 1)

                parts = emp_name.split()
                s_name = parts[0] if len(parts) > 0 else emp_name
                f_name = parts[1] if len(parts) > 1 else ""
                telegram = str(shift.get("telegram") or "").strip()
                if telegram and not telegram.startswith("@"):
                    telegram = f"@{telegram}"
                shift_key = (s_name, f_name, date_iso, club, start_t, end_t, telegram.lower())
                if shift_key in seen_shifts:
                    continue
                seen_shifts.add(shift_key)
                schedule_list.append([s_name, f_name, date_iso, club, dur, telegram or None])

    return schedule_list


def read_shifts():
    """Синхронизирует смены в режиме 'скользящего окна' (7 дней назад, 7 вперед)."""
    today = pd.Timestamp.now(tz='Europe/Moscow')
    start_date = today - pd.DateOffset(days=7)
    start_str = start_date.strftime("%Y-%m-%d")

    schedule_list = fetch_omg_shift_rows(start_date)

    conn = sqlite3.connect('db/omgbot.sql')
    try:
        with conn:
            cur = conn.cursor()
            cur.execute('CREATE TABLE IF NOT EXISTS shifts (shift_second_name varchar(50), shift_first_name varchar(50), dt_shift date, club varchar(50), dur REAL, source varchar(30), shift_login varchar(50))')
            cur.execute(
                "DELETE FROM shifts WHERE dt_shift >= ? AND source = 'omg_shift'",
                (start_str,),
            )
            cur.executemany(
                "INSERT INTO shifts (shift_second_name, shift_first_name, dt_shift, club, dur, shift_login, source) VALUES (?, ?, ?, ?, ?, ?, 'omg_shift')",
                schedule_list,
            )
            cur.close()
    finally:
        conn.close()

    return pd.DataFrame(
        [row[:5] for row in schedule_list],
        columns=['shift_second_name', 'shift_first_name', 'dt_shift', 'club', 'dur'],
    )

def sql_select(command):
    conn = sqlite3.connect('db/omgbot.sql')
    cur = conn.cursor()
    cur.execute(command)
    a = cur.fetchall()
    cur.close()
    conn.close()
    return a

# ==========================================
# 2. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ХЕШТЕГОВ
# ==========================================

def get_user_club_today(username, date_iso=None):
    """Определяет клуб сотрудника по API расписания на указанную дату или сегодня."""
    today_iso = date_iso or datetime.now(pytz.timezone('Europe/Moscow')).strftime('%Y-%m-%d')
    headers = {"Authorization": f"Bearer {SHIFTON_API_TOKEN}"}
    try:
        resp = requests.get(f"{SHIFTON_API_URL}/api/bot/schedule?date={today_iso}", headers=headers, timeout=5).json()
        if resp.get("ok"):
            for loc in resp.get("locations", []):
                club_name = loc.get("title", "Неизвестно")
                for shift in loc.get("shifts", []):
                    # API отдает телеграм с @ или без, подстраховываемся:
                    api_tg = shift.get("telegram", "").lower().strip()
                    if api_tg == username.lower() or api_tg == f"@{username.lower()}":
                        return club_name
    except Exception as e:
        print(f"Ошибка получения расписания: {e}")
    return None


def ensure_hashtag_events_table():
    conn = sqlite3.connect('db/omgbot.sql')
    try:
        with conn:
            conn.execute(
                '''
                CREATE TABLE IF NOT EXISTS hashtag_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram TEXT NOT NULL,
                    hashtag TEXT NOT NULL,
                    value REAL,
                    value_unit TEXT,
                    comment TEXT NOT NULL DEFAULT '',
                    event_date DATE NOT NULL,
                    club TEXT,
                    status TEXT NOT NULL,
                    source TEXT NOT NULL,
                    source_id INTEGER,
                    chat_id TEXT,
                    message_id INTEGER,
                    hashtag_index INTEGER NOT NULL DEFAULT 0,
                    api_error TEXT,
                    api_response TEXT,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    applied_at DATETIME,
                    UNIQUE(source, source_id),
                    UNIQUE(chat_id, message_id, hashtag_index)
                )
                '''
            )
            conn.execute(
                '''CREATE INDEX IF NOT EXISTS idx_hashtag_events_payroll
                   ON hashtag_events(hashtag, status, event_date, telegram)'''
            )
    finally:
        conn.close()


def initialize_hashtag_events():
    """Создаёт единое хранилище начислений и переносит в него старые записи."""
    ensure_hashtag_events_table()
    conn = sqlite3.connect('db/omgbot.sql')
    try:
        with conn:
            conn.execute(
                '''
                INSERT OR IGNORE INTO hashtag_events (
                    telegram, hashtag, value, value_unit, comment, event_date,
                    status, source, source_id, applied_at
                )
                SELECT who, '#двойная', amount, 'hours', COALESCE(desc, ''),
                       date(d_rep), 'applied', 'legacy_double', ID, datetime('now')
                FROM double
                '''
            )
            conn.execute(
                '''
                INSERT OR IGNORE INTO hashtag_events (
                    telegram, hashtag, value, value_unit, event_date,
                    status, source, source_id, applied_at
                )
                SELECT who, '#автосим', amount, 'rubles', date(d_rep),
                       'applied', 'legacy_autosim', ID, datetime('now')
                FROM autosim
                '''
            )
            conn.execute(
                '''
                INSERT OR IGNORE INTO hashtag_events (
                    telegram, hashtag, value, value_unit, event_date,
                    status, source, source_id, applied_at
                )
                SELECT who, '#активация', amount, 'rubles', date(d_rep),
                       'applied', 'legacy_activation', ID, datetime('now')
                FROM activation
                '''
            )
            conn.execute(
                '''
                INSERT OR IGNORE INTO hashtag_events (
                    telegram, hashtag, value, value_unit, comment, event_date,
                    club, status, source, source_id, applied_at
                )
                SELECT who, '#др', ?, 'rubles', COALESCE(desc, ''), date(dt_rep),
                       club,
                       CASE status
                           WHEN 'Одобрено' THEN 'applied'
                           WHEN 'Отклонено' THEN 'rejected'
                           ELSE 'pending'
                       END,
                       'legacy_birthday', ID,
                       CASE WHEN status = 'Одобрено' THEN datetime('now') END
                FROM birthday
                ''',
                (LEGACY_BIRTHDAY_AMOUNT,),
            )
    finally:
        conn.close()


def _message_identity(message):
    chat = getattr(message, 'chat', None)
    chat_id = getattr(chat, 'id', None)
    message_id = getattr(message, 'message_id', None)
    if message_id is None:
        message_id = getattr(message, 'id', None)
    return (
        str(chat_id) if chat_id is not None else None,
        message_id,
    )


def _extract_remote_value(text_args):
    match = NUMBER_RE.match(text_args)
    if not match:
        return "", text_args.strip()
    value_text = match.group(1).replace(',', '.')
    return value_text, text_args[match.end():].strip()


def get_remote_hashtag_rules():
    """Возвращает правила OMG Shift с коротким кешем для справки и метаданных."""
    global _hashtag_rules_cache, _hashtag_rules_cache_until
    now = time.monotonic()
    if now < _hashtag_rules_cache_until:
        return list(_hashtag_rules_cache.values())

    headers = {"Authorization": f"Bearer {SHIFTON_API_TOKEN}"}
    response = requests.get(
        f"{SHIFTON_API_URL}/api/bot/hashtags",
        headers=headers,
        timeout=5,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or not payload.get("ok"):
        return []
    _hashtag_rules_cache = {
        str(rule.get("hashtag", "")).lower(): rule
        for rule in payload.get("hashtags", [])
        if rule.get("hashtag")
    }
    _hashtag_rules_cache_until = now + HASHTAG_RULES_CACHE_SECONDS
    return list(_hashtag_rules_cache.values())


def _get_hashtag_rule(hashtag):
    """Получает метаданные правила; окончательное решение всё равно принимает POST."""
    return next(
        (
            rule for rule in get_remote_hashtag_rules()
            if str(rule.get("hashtag", "")).lower() == hashtag
        ),
        None,
    )


def _save_pending_hashtag(message, hashtag, value, value_unit, comment, event_date):
    ensure_hashtag_events_table()
    chat_id, message_id = _message_identity(message)
    conn = sqlite3.connect('db/omgbot.sql')
    try:
        with conn:
            existing = None
            if chat_id is not None and message_id is not None:
                existing = conn.execute(
                    '''SELECT id, status FROM hashtag_events
                       WHERE chat_id=? AND message_id=? AND hashtag_index=0''',
                    (chat_id, message_id),
                ).fetchone()
            if existing:
                return existing[0], existing[1], False
            cursor = conn.execute(
                '''
                INSERT INTO hashtag_events (
                    telegram, hashtag, value, value_unit, comment, event_date,
                    status, source, chat_id, message_id, hashtag_index
                ) VALUES (?, ?, ?, ?, ?, ?, 'pending', 'omg_shift', ?, ?, 0)
                ''',
                (
                    f"@{message.from_user.username}",
                    hashtag,
                    value,
                    value_unit,
                    comment,
                    event_date,
                    chat_id,
                    message_id,
                ),
            )
            return cursor.lastrowid, 'pending', True
    finally:
        conn.close()


def _finish_hashtag_event(event_id, status, response, error=None):
    conn = sqlite3.connect('db/omgbot.sql')
    try:
        with conn:
            conn.execute(
                '''
                UPDATE hashtag_events
                SET status=?, api_error=?, api_response=?,
                    applied_at=CASE WHEN ?='applied' THEN datetime('now') ELSE applied_at END
                WHERE id=?
                ''',
                (
                    status,
                    error,
                    json.dumps(response, ensure_ascii=False),
                    status,
                    event_id,
                ),
            )
    finally:
        conn.close()


def do_remote_hashtag(hashtag, message, text_args):
    """Передаёт начисление в OMG Shift и сохраняет подтверждённый результат."""
    username = getattr(message.from_user, 'username', None)
    if not username:
        return KPI_ERROR, "Не вижу Telegram username отправителя.", ""

    value_text, comment = _extract_remote_value(text_args)
    rule = None
    try:
        rule = _get_hashtag_rule(hashtag)
    except Exception as error:
        print(f"Не удалось получить правило {hashtag}: {error}")

    stored_value = float(value_text) if value_text else None
    value_unit = rule.get("valueUnit") if rule else None
    if rule and rule.get("type") == "fixed_bonus":
        stored_value = rule.get("amount")

    today = datetime.now(pytz.timezone('Europe/Moscow')).strftime('%Y-%m-%d')
    event_id, previous_status, created = _save_pending_hashtag(
        message,
        hashtag,
        stored_value,
        value_unit,
        comment,
        today,
    )
    if not created:
        if previous_status == 'applied':
            return KPI_REMOTE_SUCCESS, "", ""
        if previous_status == 'ignored':
            return KPI_IGNORED, "", ""
        return KPI_ERROR, "Это сообщение уже обрабатывалось, но начисление не было подтверждено.", ""

    headers = {
        "Authorization": f"Bearer {SHIFTON_API_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "telegram": f"@{username}",
        "hashtag": hashtag,
        "value": value_text,
        "comment": comment,
        "date": today,
    }
    try:
        response = requests.post(
            f"{SHIFTON_API_URL}/api/bot/hashtag",
            json=payload,
            headers=headers,
            timeout=5,
        )
        response.raise_for_status()
        result = response.json()
    except Exception as error:
        _finish_hashtag_event(event_id, 'failed', {"error": str(error)}, "request_failed")
        return KPI_ERROR, "Не удалось передать хештег в OMG Shift. Попробуйте позже.", ""

    if not isinstance(result, dict):
        _finish_hashtag_event(event_id, 'failed', {"error": "invalid_response"}, "invalid_response")
        return KPI_ERROR, "OMG Shift вернул некорректный ответ.", ""

    if result.get("ok"):
        _finish_hashtag_event(event_id, 'applied', result)
        return KPI_REMOTE_SUCCESS, "", ""

    error = result.get("error", "unknown_error")
    status = 'ignored' if error == "hashtag_not_configured" else 'failed'
    _finish_hashtag_event(event_id, status, result, error)
    if error == "hashtag_not_configured":
        return KPI_IGNORED, "", ""

    messages = {
        "employee_not_found": f"Сотрудник с тегом @{username} не найден в OMG Shift.",
        "shift_not_found": f"На сегодня не найдена смена для @{username}.",
        "multiple_shifts_found": f"У @{username} найдено несколько смен за день, нужна ручная проверка.",
        "value_required": f"После {hashtag} необходимо указать положительное число.",
    }
    return KPI_ERROR, messages.get(error, f"OMG Shift не обработал {hashtag}: {error}."), ""

# ==========================================
# 3. МОДУЛЬНЫЕ ОБРАБОТЧИКИ ХЕШТЕГОВ
# ==========================================

def do_club_action(hashtag, message, text_args):
    """Обработчик для #продление и #инициатива (автоматически тянет клуб)."""
    if "факт" in message.text.lower():
        return KPI_INVALID, 'Даже у меня есть имя, значит и у него есть!',  "```Правильно!\nНикаких 'фактов'!```"
    if len(text_args) > 1024:
        return KPI_INVALID, "Слишком длинно!", "```Правильно!\nПожалуйста, меньше 1024 символов```"

    user_name = "@" + message.from_user.username
    today = datetime.now(pytz.timezone('Europe/Moscow')).strftime('%Y-%m-%d %H:%M:%S')

    # Ищем пользователя на смене
    club = get_user_club_today(message.from_user.username)
    shift_not_found = (club is None)
    if shift_not_found:
        club = "Неизвестно"

    # Запись в локальную базу
    table = action[hashtag]
    Insert(table, today, user_name, club, text_args)
    update_table(table)

    # Формируем красивый ответ
    if shift_not_found:
        return KPI_SAVED_ERROR, "Запись сохранена локально, но смена сотрудника на сегодня не найдена в OMG Shift.", ""
    return KPI_SUCCESS, random.choice(TEXTS['aff']) + f" (Клуб: {club})", ""


def do_bonus(hashtag, message, text_args):
    """Обработчик для #серт и #абик"""
    parts = text_args.split()
    if len(parts) != 2 or not parts[0].isnumeric() or not parts[1].isnumeric():
        return KPI_INVALID, "Неверно написан хештег! Формат:", f"```Правильно!\n{hashtag} *номер* *сумма*```"
        
    num = parts[0]
    sale = parts[1]
    
    if (hashtag == "#абик" and int(num) < 1000) or (hashtag == "#серт" and int(num) >= 3000):
        table = bonus[hashtag]
        today = datetime.now(pytz.timezone('Europe/Moscow')).strftime('%Y-%m-%d')
        who = "@" + message.from_user.username
        Insert_bonus(table, num, today, who, sale)
        update_table(table)
        return KPI_SUCCESS, random.choice(TEXTS['aff']), ""
    else:
        return KPI_INVALID, "Неверно написан хештег!", "```Правильно!\nАбики имеют номер < 1000, серты >= 3000```"


def do_review(message, text_args):
    """Обработчик для #отзывы"""
    parts = text_args.split(maxsplit=1)
    if not parts or not parts[0].isnumeric():
        return KPI_INVALID, "Неверно написан хештег! Формат:", "```Правильно!\n#отзывы *количество* *описание*```"
        
    amount = int(parts[0])
    desc = parts[1].strip() if len(parts) > 1 else ""
    today = datetime.now(pytz.timezone('Europe/Moscow')).strftime('%Y-%m-%d')
    who = "@" + message.from_user.username

    conn = sqlite3.connect('db/omgbot.sql')
    cur = conn.cursor()
    cur.execute("INSERT INTO reviews (who, d_rep, amount, desc) VALUES (?, ?, ?, ?)", (who, today, amount, desc))
    conn.commit()
    cur.close()
    conn.close()
    
    update_table('reviews')
    return KPI_SUCCESS, random.choice(TEXTS['aff']), ""


def do_penalty(message, text_args, bypass_admin=False):
    """Обработчик для #штраф"""
    # Поддержка старого вызова с OPENCLOSE
    if isinstance(text_args, list):
        if len(text_args) > 0 and text_args[0] == 'OPENCLOSE':
            bypass_admin = True
            text_args = " ".join(text_args[1:])

    parts = text_args.split(maxsplit=1)
    if len(parts) < 2:
        return KPI_INVALID, 'Формат неверный!', "```Правильно!\n#штраф @логин причина```"
        
    target_login = parts[0]
    desc = parts[1]
    
    initialize_kpi_calculation_schema()
    conn = sqlite3.connect('db/omgbot.sql')
    cur = conn.cursor()
    cur.execute("SELECT login FROM users WHERE login=?", (target_login,))
    if not cur.fetchone():
        conn.close()
        return KPI_INVALID, 'Нет такого логина в базе!', "```Правильно!\n#штраф @логин причина```"
    
    if role_of(message) == ROLE_OWNER or bypass_admin:
        today = datetime.now(pytz.timezone('Europe/Moscow')).strftime('%Y-%m-%d')
        cur.execute("INSERT INTO penalty (dt, name, desc) VALUES (?, ?, ?)", (today, target_login, desc))
        mirror_legacy_penalty(
            conn,
            cur.lastrowid,
            target_login,
            today,
            desc,
            f"@{message.from_user.username}",
        )
        conn.commit()
        conn.close()
        return KPI_SUCCESS, random.choice(TEXTS['penalty_phrases']), ""
    else:
        conn.close()
        return KPI_INVALID, 'Ещё чего выдумал!', "```Правильно!\nШтраф выписывает только руководство```"

# ==========================================
# 4. РОУТЕР ХЕШТЕГОВ (Главная точка входа)
# ==========================================

kpi_dict = {
    '#серт': lambda m, args: do_bonus('#серт', m, args),
    '#абик': lambda m, args: do_bonus('#абик', m, args),
    '#штраф': lambda m, args: do_penalty(m, args),
    '#продление': lambda m, args: do_club_action('#продление', m, args),
    '#инициатива': lambda m, args: do_club_action('#инициатива', m, args),
    '#отзывы': do_review,
}

def hash_handle(message):
    try:
        # Разделяем на 2 части: хештег и всё остальное (аргументы)
        parts = message.text.split(maxsplit=1)
        if not parts:
            return KPI_INVALID, "Текст пустой!", ""
            
        hashtag = parts[0].lower()
        text_args = parts[1].strip() if len(parts) > 1 else ""
        
        if hashtag in kpi_dict:
            flag, answer, desc = kpi_dict[hashtag](message, text_args)
            return flag, answer, desc
        return do_remote_hashtag(hashtag, message, text_args)
    except Exception as e:
        print(f"Ошибка в hash_handle: {e}")
        return KPI_ERROR, "Не удалось обработать хештег. Попробуйте ещё раз или обратитесь к администратору.", ""


def _sheet_number(value, percent=False):
    raw = str(value or '').replace(' ', '').replace(',', '.').strip()
    is_percent = raw.endswith('%')
    raw = raw.rstrip('%')
    try:
        number = float(raw)
    except ValueError:
        return 0.0
    if percent or is_percent:
        return number / 100.0 if is_percent else number
    return number


def _sheet_truthy(value):
    return str(value or '').strip().lower() in ('true', 'истина', '1', 'да')


def _kpi_sheet_context(spreadsheet):
    employees = spreadsheet.worksheet_by_title('Сотрудники').get_values(
        start='A1',
        end='B60',
        returnas='matrix',
    )
    nickname_to_login = {
        str(row[0]).strip(): str(row[1]).strip()
        for row in employees
        if (
            len(row) > 1
            and str(row[0]).strip() not in ('', '-')
            and str(row[1]).strip() not in ('', '-')
        )
    }
    main_values = spreadsheet.worksheet_by_title('Главный').get_values(
        start='A1',
        end='AA60',
        returnas='matrix',
    )
    selected_month = (
        main_values[0][2]
        if main_values and len(main_values[0]) > 2
        else datetime.now(pytz.timezone('Europe/Moscow')).strftime('%Y-%m-%d')
    )
    return nickname_to_login, main_values, selected_month


def _legacy_nickname_logins():
    conn = sqlite3.connect('db/omgbot.sql')
    try:
        rows = conn.execute(
            '''
            SELECT nick_name, login
            FROM users
            WHERE nick_name IS NOT NULL AND trim(nick_name) <> ''
              AND login IS NOT NULL AND trim(login) <> ''
            '''
        ).fetchall()
    finally:
        conn.close()
    return {
        str(nickname).strip(): str(login).strip()
        for nickname, login in rows
    }


def import_legacy_kpi_sheet_controls(spreadsheet=None):
    """Импортирует доступные штрафы и текущие отметки трансляций идемпотентно."""
    initialize_kpi_calculation_schema()
    if spreadsheet is None:
        client = pygsheets.authorize(
            service_file='key/omgbot-430116-e9a4d9c69b7f.json'
        )
        spreadsheet = client.open('KPI OMG VR')

    nickname_to_login, main_values, selected_month = _kpi_sheet_context(spreadsheet)
    legacy_nickname_to_login = _legacy_nickname_logins()
    legacy_nickname_to_login.update(nickname_to_login)
    imported_penalties = 0
    unmatched = []

    for worksheet in spreadsheet.worksheets():
        title = worksheet.title
        if title == 'Штрафы':
            period_month = selected_month
        elif title.startswith('Штрафы '):
            period_month = title.split(' ', 1)[1]
        else:
            continue

        values = worksheet.get_values(
            start='A1',
            end='F60',
            returnas='matrix',
        )
        categories = values[0][1:6] if values else []
        for row_index, row in enumerate(values[2:], start=3):
            nickname = str(row[0]).strip() if row else ''
            if not nickname:
                # В архивных листах пустая строка после сотрудников содержит итоги.
                continue
            login = legacy_nickname_to_login.get(nickname)
            counts = row[1:6] if len(row) > 1 else []
            if not login and any(_sheet_number(value) > 0 for value in counts):
                unmatched.append({
                    'sheet': title,
                    'row': row_index,
                    'nickname': nickname,
                })
                continue
            if not login:
                continue

            for column_index, raw_count in enumerate(counts, start=2):
                count = max(0, int(_sheet_number(raw_count)))
                reason = (
                    str(categories[column_index - 2]).strip()
                    if len(categories) >= column_index - 1
                    else 'Импортированный штраф'
                )
                for sequence in range(1, count + 1):
                    source_key = (
                        f'legacy-sheet:{title}:{row_index}:{column_index}:{sequence}'
                    )
                    if import_sheet_penalty(
                        login,
                        period_month,
                        reason,
                        source_key,
                    ):
                        imported_penalties += 1

    imported_streams = 0
    for row in main_values[7:]:
        nickname = str(row[1]).strip() if len(row) > 1 else ''
        login = nickname_to_login.get(nickname)
        if not login or len(row) <= 18 or not _sheet_truthy(row[18]):
            continue
        set_monthly_stream(
            login,
            selected_month,
            True,
            'legacy_import',
            source='legacy_google_sheet',
            overwrite=False,
        )
        imported_streams += 1

    return {
        'penalties': imported_penalties,
        'streams_seen': imported_streams,
        'unmatched': unmatched,
    }


def _sheet_shadow_rows(main_values, nickname_to_login):
    rows = []
    for row in main_values[7:]:
        nickname = str(row[1]).strip() if len(row) > 1 else ''
        login = nickname_to_login.get(nickname)
        if not login:
            continue

        def cell(index):
            return row[index] if len(row) > index else 0

        rows.append({
            'login': login,
            'nickname': nickname,
            'shifts': _sheet_number(cell(2)),
            'weighted_shifts': _sheet_number(cell(3)),
            'reviews': _sheet_number(cell(4)),
            'reviews_pct': _sheet_number(cell(5), percent=True),
            'forms': _sheet_number(cell(6)),
            'forms_pct': _sheet_number(cell(7), percent=True),
            'extensions': _sheet_number(cell(8)),
            'extensions_pct': _sheet_number(cell(9), percent=True),
            'certificates': _sheet_number(cell(10)),
            'certificates_pct': _sheet_number(cell(11), percent=True),
            'subscriptions': _sheet_number(cell(12)),
            'subscriptions_pct': _sheet_number(cell(13), percent=True),
            'bs': _sheet_number(cell(14)),
            'bs_pct': _sheet_number(cell(15), percent=True),
            'initiatives': _sheet_number(cell(16)),
            'initiatives_pct': _sheet_number(cell(17), percent=True),
            'penalties': _sheet_number(cell(19)),
            'total_pct': _sheet_number(cell(20), percent=True),
            'weighted_pct': _sheet_number(cell(21), percent=True),
            'amount': _sheet_number(cell(22)),
            'rank': _sheet_number(cell(24)),
        })
    return rows


def compare_server_kpi_with_sheet(spreadsheet=None):
    """Сравнивает новый расчёт с Google Sheets, не меняя ответы пользователям."""
    if spreadsheet is None:
        client = pygsheets.authorize(
            service_file='key/omgbot-430116-e9a4d9c69b7f.json'
        )
        spreadsheet = client.open('KPI OMG VR')
    nickname_to_login, main_values, selected_month = _kpi_sheet_context(spreadsheet)
    server_rows = calculate_monthly_kpi(
        selected_month,
        employee_logins=nickname_to_login.values(),
        ensure_schema=False,
    )
    sheet_rows = _sheet_shadow_rows(main_values, nickname_to_login)
    return {
        'period_month': str(selected_month),
        'employees': len(sheet_rows),
        'differences': compare_with_sheet(server_rows, sheet_rows),
    }


def run_kpi_shadow_cycle():
    try:
        client = pygsheets.authorize(
            service_file='key/omgbot-430116-e9a4d9c69b7f.json'
        )
        spreadsheet = client.open('KPI OMG VR')
        imported = import_legacy_kpi_sheet_controls(spreadsheet)
        comparison = compare_server_kpi_with_sheet(spreadsheet)
        differences = comparison['differences']
        print(
            'KPI shadow: '
            f"month={comparison['period_month']}, "
            f"employees={comparison['employees']}, "
            f'differences={len(differences)}, '
            f"imported_penalties={imported['penalties']}, "
            f"unmatched_penalties={len(imported['unmatched'])}"
        )
        for difference in differences[:20]:
            print(f'KPI shadow difference: {difference}')
        for unresolved in imported['unmatched'][:20]:
            print(f'KPI legacy penalty unresolved: {unresolved}')
        return {
            'import': imported,
            'comparison': comparison,
        }
    except Exception as error:
        print(f'KPI shadow failed: {error}')
        return None


# ==========================================
# 5. СИНХРОНИЗАЦИЯ
# ==========================================

def init():
    read_ank_table()
    read_shifts()
    write_data(sql_select(sql_scripts.sheets_shifts_ext), 'KPI helper', 'shifts')
    write_data(sql_select(sql_scripts.sheets_union), 'KPI OMG VR', 'data')
    write_data(sql_select(sql_scripts.sheets_shifts), 'KPI OMG VR', 'shifts')
    write_data(sql_select(sql_scripts.sheets_records), 'KPI OMG VR', 'raw')
    run_kpi_shadow_cycle()

def update_kpi():
    read_ank_table()
    write_data(sql_select(sql_scripts.sheets_union), 'KPI OMG VR', 'data')
    write_data(sql_select(sql_scripts.sheets_records), 'KPI OMG VR', 'raw')
