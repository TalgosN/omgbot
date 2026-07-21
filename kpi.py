import sqlite3
import pytz
from telebot import *
from constants import *
import pygsheets
from datetime import datetime, timedelta
import pandas as pd
import requests
import json
import math
import sql_scripts
from sheets import *
import random
import os


# Словари
clubs = {'мар':'Марьино','лен':'Ленинский','про':'Прокшино','каш':'Каширка','дми':'Дмитровка'}
action = {'#продление':'afterparty', '#др':'birthday', '#инициатива':'initiative'}
bonus = {'#серт':'sert', '#абик':'abik'}

KPI_SUCCESS = "success"
KPI_INVALID = "invalid"
KPI_ERROR = "error"
KPI_SAVED_ERROR = "saved_error"

# ==========================================
# 1. ЗАГРУЗКА И ВЫГРУЗКА ДАННЫХ И СМЕН
# ==========================================

def read_kpi():
    c = pygsheets.authorize(service_file='key/omgbot-430116-e9a4d9c69b7f.json')
    sh = c.open('KPI OMG VR')
    wks = sh.worksheet_by_title('Настройки')
    tasks = wks.get_values(start='A', end='A', returnas='matrix')
    price = wks.get_values(start='B', end='B', returnas='matrix')
    plan = wks.get_values(start='C', end='C', returnas='matrix')

    df_tasks = pd.DataFrame(tasks, columns=['Task'])
    df_price = pd.DataFrame(price, columns=['Club'])
    df_plan = pd.DataFrame(plan, columns=['Date'])
    return pd.concat([df_tasks, df_price, df_plan], axis=1)

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
    c = pygsheets.authorize(service_file='key/omgbot-430116-e9a4d9c69b7f.json')
    sh = c.open(table)
    wks = sh.worksheet_by_title(sheet)
    rng = wks.get_values(start='A2', end=f'F{wks.rows}', returnas='range')
    rng.clear()

    list1 = [list(row) for row in data]
    if len(list1) > 0:
        wks.update_values('A2', list1)

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
            columns = {row[1] for row in cur.execute('PRAGMA table_info(shifts)')}
            if 'source' not in columns:
                cur.execute('ALTER TABLE shifts ADD COLUMN source varchar(30)')
            if 'shift_login' not in columns:
                cur.execute('ALTER TABLE shifts ADD COLUMN shift_login varchar(50)')
            users_table = cur.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='users_new'"
            ).fetchone()
            if users_table:
                cur.execute(
                    """UPDATE shifts
                       SET shift_login = (
                           SELECT login FROM users_new
                           WHERE second_name = shifts.shift_second_name
                             AND first_name = shifts.shift_first_name
                           LIMIT 1
                       )
                       WHERE shift_login IS NULL"""
                )
            cur.execute(
                "DELETE FROM shifts WHERE dt_shift >= ? AND COALESCE(source, 'omg_shift') = 'omg_shift'",
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

def get_user_club_today(username):
    """Определяет текущий клуб сотрудника по API расписания на сегодня"""
    today_iso = datetime.now(pytz.timezone('Europe/Moscow')).strftime('%Y-%m-%d')
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

# ==========================================
# 3. МОДУЛЬНЫЕ ОБРАБОТЧИКИ ХЕШТЕГОВ
# ==========================================

def do_club_action(hashtag, message, text_args):
    """Обработчик для #др, #продление, #инициатива (автоматически тянет клуб)"""
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

    api_error = None
    if hashtag == "#др":
        try:
            headers = {"Authorization": f"Bearer {SHIFTON_API_TOKEN}", "Content-Type": "application/json"}
            payload = {"telegram": user_name, "comment": text_args}
            res = requests.post(f"{SHIFTON_API_URL}/api/bot/birthday", json=payload, headers=headers, timeout=5).json()
            if not res.get("ok"):
                api_error = res.get("error")
        except Exception as e:
            print(f"Ошибка API при отправке ДР: {e}")
            api_error = "request_failed"

    # Запись в локальную базу
    table = action[hashtag]
    update_status() # Функция из твоего старого кода (возможно в sheets.py)
    Insert(table, today, user_name, club, text_args)
    update_table(table)

    # Формируем красивый ответ
    if api_error and api_error != "shift_not_found":
        return KPI_SAVED_ERROR, f"Запись сохранена локально, но OMG Shift вернул ошибку: {api_error}.", ""
    if shift_not_found or api_error == "shift_not_found":
        return KPI_SAVED_ERROR, "Запись сохранена локально, но смена сотрудника на сегодня не найдена в OMG Shift.", ""
    return KPI_SUCCESS, random.choice(TEXTS['aff']) + f" (Клуб: {club})", ""


def do_double(message, text_args):
    """Обработчик для #двойная"""
    parts = text_args.split(maxsplit=1)
    if not parts:
        return KPI_INVALID, "Неверно написан хештег! Формат:", "```Правильно!\n#двойная *часов* *описание*```"
        
    hours_str = parts[0].strip().replace(',', '.')
    desc = parts[1].strip() if len(parts) > 1 else ""

    if not hours_str.replace('.', '', 1).isnumeric():
        return KPI_INVALID, "Неверно написан хештег! Формат:", "```Правильно!\n#двойная *часов* *описание*```"

    user_name = "@" + message.from_user.username
    today = datetime.now(pytz.timezone('Europe/Moscow')).strftime('%Y-%m-%d')
    hours = float(hours_str)

    api_error = None
    try:
        headers = {"Authorization": f"Bearer {SHIFTON_API_TOKEN}", "Content-Type": "application/json"}
        payload = {"telegram": user_name, "hours": hours}
        res = requests.post(f"{SHIFTON_API_URL}/api/bot/double", json=payload, headers=headers, timeout=5).json()
        if not res.get("ok"):
            api_error = res.get("error")
    except Exception as e:
        print(f"Ошибка API при отправке двойной: {e}")
        api_error = "request_failed"

    conn = sqlite3.connect('db/omgbot.sql')
    cur = conn.cursor()
    cur.execute("INSERT INTO double (who, d_rep, amount, desc) VALUES (?, ?, ?, ?)", (user_name, today, hours, desc))
    conn.commit()
    cur.close()
    conn.close()

    club = get_user_club_today(message.from_user.username)
    if api_error and api_error != "shift_not_found":
        return KPI_SAVED_ERROR, f"Запись сохранена локально, но OMG Shift вернул ошибку: {api_error}.", ""
    if club is None or api_error == "shift_not_found":
        return KPI_SAVED_ERROR, "Запись сохранена локально, но смена сотрудника на сегодня не найдена в OMG Shift.", ""
    
    return KPI_SUCCESS, random.choice(TEXTS['aff']), ""


def do_simple_amount(hashtag, message, text_args):
    """Обработчик для #автосим (10%) и #активация (100%)"""
    autosim_coeff=1
    amount_text = text_args.strip().replace(',', '.')
    if not amount_text or not amount_text.replace('.', '', 1).isnumeric():
        return KPI_INVALID, "Неверный формат хештега!", f"```Правильно!\n{hashtag} *сумма оплаты*```"
    
    user_name = "@" + message.from_user.username
    today = datetime.now(pytz.timezone('Europe/Moscow')).strftime('%Y-%m-%d')
    raw_amount = float(amount_text)
    if not math.isfinite(raw_amount) or raw_amount <= 0:
        return KPI_INVALID, "Сумма должна быть больше нуля!", f"```Правильно!\n{hashtag} *сумма оплаты*```"
    
    # Математика в зависимости от тега
    if hashtag == '#автосим':
        amount = raw_amount * autosim_coeff
        api_endpoint = "/api/bot/autosim"
    else:
        amount = raw_amount
        api_endpoint = "/api/bot/activation"

    api_error = None
    try:
        headers = {"Authorization": f"Bearer {SHIFTON_API_TOKEN}", "Content-Type": "application/json"}
        payload = {"telegram": user_name, "amount": amount}
        res = requests.post(f"{SHIFTON_API_URL}{api_endpoint}", json=payload, headers=headers, timeout=5).json()
        if not res.get("ok"):
            api_error = res.get("error")
    except Exception as e:
        print(f"Ошибка API при отправке {hashtag}: {e}")
        api_error = "request_failed"

    table_name = 'autosim' if hashtag == '#автосим' else 'activation'

    conn = sqlite3.connect('db/omgbot.sql')
    cur = conn.cursor()
    cur.execute(f"INSERT INTO {table_name} (who, d_rep, amount) VALUES (?, ?, ?)", (user_name, today, amount))
    conn.commit()
    cur.close()
    conn.close()

    club = get_user_club_today(message.from_user.username)
    if api_error and api_error != "shift_not_found":
        return KPI_SAVED_ERROR, f"Запись на {amount:g} руб. сохранена локально, но OMG Shift вернул ошибку: {api_error}.", ""
    if club is None or api_error == "shift_not_found":
        return KPI_SAVED_ERROR, f"Запись на {amount:g} руб. сохранена локально, но смена сотрудника на сегодня не найдена в OMG Shift.", ""
    
    return KPI_SUCCESS, random.choice(TEXTS['aff']) + f" (Сумма бонуса: {amount:g} руб)", ""


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
    
    conn = sqlite3.connect('db/omgbot.sql')
    cur = conn.cursor()
    cur.execute("SELECT login FROM users_new WHERE login=?", (target_login,))
    if not cur.fetchone():
        conn.close()
        return KPI_INVALID, 'Нет такого логина в базе!', "```Правильно!\n#штраф @логин причина```"
    
    cur.execute("SELECT status FROM users_new WHERE login=?", (f"@{message.from_user.username}",))
    fet2 = cur.fetchone()
    
    if fet2 and (int(fet2[0]) == 2 or bypass_admin):
        today = datetime.now(pytz.timezone('Europe/Moscow')).strftime('%Y-%m-%d')
        cur.execute("INSERT INTO penalty (dt, name, desc) VALUES (?, ?, ?)", (today, target_login, desc))
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
    '#двойная': do_double,
    '#продление': lambda m, args: do_club_action('#продление', m, args),
    '#др': lambda m, args: do_club_action('#др', m, args),
    '#инициатива': lambda m, args: do_club_action('#инициатива', m, args),
    '#отзывы': do_review,
    '#автосим': lambda m, args: do_simple_amount('#автосим', m, args),
    '#активация': lambda m, args: do_simple_amount('#активация', m, args)
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
        else:
            return KPI_INVALID, "Не понимаю о чем ты 🙈", "```Правильно!\nЕсли не знаешь как написать хештег, пиши /help```"
    except Exception as e:
        print(f"Ошибка в hash_handle: {e}")
        return KPI_ERROR, "Не удалось обработать хештег. Попробуйте ещё раз или обратитесь к администратору.", ""

# ==========================================
# 5. СИНХРОНИЗАЦИЯ
# ==========================================

def init():
    read_ank_table()
    read_shifts()
    write_data(sql_select(sql_scripts.shifts_ext), 'KPI helper', 'shifts')
    write_data(sql_select(sql_scripts.union), 'KPI OMG VR', 'data')
    write_data(sql_select(sql_scripts.shifts), 'KPI OMG VR', 'shifts')
    write_data(sql_select(sql_scripts.records), 'KPI OMG VR', 'raw')

def update_kpi():
    read_ank_table()
    write_data(sql_select(sql_scripts.union), 'KPI OMG VR', 'data')
    write_data(sql_select(sql_scripts.records), 'KPI OMG VR', 'raw')
