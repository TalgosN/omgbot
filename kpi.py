import sqlite3
import pytz
from telebot import *
from constants import *
import pygsheets
from datetime import datetime,timedelta
import pandas as pd
import requests
import json
import locale
import math
import sql_scripts
from sheets import *
import random
from constants import *





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

    df_combined = pd.concat([df_tasks, df_price, df_plan], axis=1)
    return df_combined

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

    current_date = pd.Timestamp.now()
    three_months_ago = current_date - pd.DateOffset(months=3)
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

def read_shifts():
    """Синхронизирует смены в режиме 'скользящего окна' (7 дней назад, 7 вперед)"""
    conn = sqlite3.connect('db/omgbot.sql')
    cur = conn.cursor()
    cur.execute('CREATE TABLE IF NOT EXISTS shifts (shift_second_name varchar(50), shift_first_name varchar(50), dt_shift date, club varchar(50), dur REAL)')
    
    today = pd.Timestamp.now(tz='Europe/Moscow')
    start_date = today - pd.DateOffset(days=7)
    
    start_str = start_date.strftime("%Y-%m-%d")
    cur.execute("DELETE FROM shifts WHERE dt_shift >= ?", (start_str,))
    conn.commit()

    schedule_list = []
    headers = {"Authorization": f"Bearer {SHIFTON_API_TOKEN}"}

    for p in range(15):
        current_dt = start_date + pd.DateOffset(days=p)
        date_iso = current_dt.strftime('%Y-%m-%d')
        
        try:
            resp = requests.get(f"{SHIFTON_API_URL}/api/bot/schedule?date={date_iso}", headers=headers, timeout=5).json()
            if resp.get("ok"):
                for loc in resp.get("locations", []):
                    club = loc.get("title", "Неизвестно")
                    for shift in loc.get("shifts", []):
                        emp_name = shift.get("employee", "Неизвестно")
                        start_t = shift.get("start")
                        end_t = shift.get("end")

                        try:
                            t1 = datetime.strptime(start_t, "%H:%M")
                            t2 = datetime.strptime(end_t, "%H:%M")
                            if t2 < t1: t2 += timedelta(days=1)
                            dur = round((t2 - t1).total_seconds() / 3600, 1)
                        except:
                            dur = 0

                        parts = emp_name.split()
                        s_name = parts[0] if len(parts) > 0 else emp_name
                        f_name = parts[1] if len(parts) > 1 else ""

                        schedule_list.append([s_name, f_name, date_iso, club, dur])
                        cur.execute("INSERT INTO shifts (shift_second_name, shift_first_name, dt_shift, club, dur) VALUES (?, ?, ?, ?, ?)", 
                                    (s_name, f_name, date_iso, club, dur))
        except Exception as e:
            print(f"Ошибка парсинга смен за {date_iso}: {e}")

    conn.commit()
    cur.close()
    conn.close()
    
    return pd.DataFrame(schedule_list, columns=['shift_second_name', 'shift_first_name', 'dt_shift', 'club', 'dur'])

def sql_select(command):
    conn = sqlite3.connect('db/omgbot.sql')
    cur = conn.cursor()
    cur.execute(command)
    a = cur.fetchall()
    cur.close()
    conn.close()
    return a

def hash_handle(message):
    try:
        message1 = ' '.join(message.text.split())
        parts = message1.split(' ', 2)
        if len(parts) == 2:
            parts.append("")
        elif len(parts) > 3:
            pass

        if parts[0] in kpi_dict:
            flag, answer, desc = kpi_dict[parts[0]](message, parts)
        else:
            return False, "Не понимаю о чем ты 🙈", "```Правильно!\nЕсли не знаешь как написать хештег, пиши /help```"

        return flag, answer, desc
    except Exception as e:
        print(e)
        return True, "Что-то пошло не так!", ""

def do_action(message, parts):
    if "факт" in message.text.lower():
        return False, 'Даже у меня есть имя, значит и у него есть!',  "```Правильно!\nНикаких 'фактов'!```"
    
    action_do = parts[0].lower()
    club = parts[1].lower()
    
    if club in clubs:
        club = clubs[club]
        table = action[action_do]
        user_name = "@" + message.from_user.username
        today = datetime.now(pytz.timezone('Europe/Moscow')).strftime('%Y-%m-%d %H:%M:%S')
        desc = parts[2].strip() if len(parts) > 2 else ""
        
        if len(desc) > 1024:
            return False, "Слишком длинно!", "```Правильно!\nПожалуйста, меньше 1024 символов```"
        
        # Интеграция с новым API для #ДР
        if action_do == "#др":
            try:
                headers = {"Authorization": f"Bearer {SHIFTON_API_TOKEN}", "Content-Type": "application/json"}
                payload = {"telegram": user_name, "comment": desc}
                requests.post(f"{SHIFTON_API_URL}/api/bot/birthday", json=payload, headers=headers, timeout=5)
            except Exception as e:
                print(f"Ошибка API при отправке ДР: {e}")

        update_status()
        Insert(table, today, user_name, club, desc)
        update_table(table)
        return True, random.choice(TEXTS['aff']), ""
    else:
        return False, "Неверно написан хештег!", "```Правильно!\nКоды клубов: лен, мар, каш, про, дми```"

def do_double(message, parts):
    # Разрешаем запятую для дробных часов (например, 1,5)
    hours_str = parts[1].strip().replace(',', '.')
    
    if hours_str.replace('.', '', 1).isnumeric() and len(parts) == 3:
        today = datetime.now(pytz.timezone('Europe/Moscow')).strftime('%Y-%m-%d')
        user_name = "@" + message.from_user.username
        
        # --- ИНТЕГРАЦИЯ С НОВЫМ API ДЛЯ #ДВОЙНАЯ ---
        try:
            headers = {"Authorization": f"Bearer {SHIFTON_API_TOKEN}", "Content-Type": "application/json"}
            payload = {"telegram": user_name, "hours": float(hours_str)}
            requests.post(f"{SHIFTON_API_URL}/api/bot/double", json=payload, headers=headers, timeout=5)
        except Exception as e:
            print(f"Ошибка API при отправке двойной: {e}")
        # ------------------------------------------

        conn = sqlite3.connect('db/omgbot.sql')
        cur = conn.cursor()
        cur.execute("INSERT INTO double (who, d_rep, amount, desc) VALUES (?, ?, ?, ?)", 
                    (user_name, today, float(hours_str), parts[2].strip()))
        conn.commit()
        cur.close()
        conn.close()

        return True, random.choice(TEXTS['aff']), ""
    else:
        return False, "Неверно написан хештег! Формат:", "```Правильно!\n#двойная *часов* *описание*```"


def do_simple_amount(message, parts):
    """Универсальный обработчик для хештегов #автосим и #активация"""
    if len(parts) >= 2 and parts[1].strip().isnumeric():
        today = datetime.now(pytz.timezone('Europe/Moscow')).strftime('%Y-%m-%d')
        amount = int(parts[1].strip())
        who = "@" + message.from_user.username
        
        # Определяем таблицу
        table_name = 'autosim' if parts[0].lower() == '#автосим' else 'activation'

        conn = sqlite3.connect('db/omgbot.sql')
        cur = conn.cursor()
        # Используем безопасную вставку
        cur.execute(f"INSERT INTO {table_name} (who, d_rep, amount) VALUES (?, ?, ?)", (who, today, amount))
        conn.commit()
        cur.close()
        conn.close()
        
        return True, random.choice(TEXTS['aff']), ""
    else:
        return False, "Неверный формат хештега!", f"```Правильно!\n#автосим *сумма оплаты*```"

def do_bonus(message,parts):
    
    if parts[1].isnumeric() and parts[2].isnumeric():
        if (parts[0]=="#абик" and int(parts[1])<1000) or (parts[0]=="#серт" and int(parts[1])>=3000):
           
            table = bonus[parts[0]]
            today = datetime.now(pytz.timezone('Europe/Moscow')).strftime('%Y-%m-%d')
            num = parts[1]
            sale = parts[2]
            who = "@"+message.from_user.username
            Insert_bonus(table,num,today,who,sale)
            update_table(table)
            return True, random.choice(TEXTS['aff']),""

        else:
            return  False, "Неверно написан хештег!", "```Правильно!\nАбики имеют номер 001, серты имеют номер 3001```"
    else:
        return False, "Неверно написан хештег! Формат:", "```Правильно!\n#серт *номер* *сумма*```"
        
def do_review(message,parts):
    
    if parts[1].strip().isnumeric() and len (parts)==3:
        today = datetime.now(pytz.timezone('Europe/Moscow')).strftime('%Y-%m-%d')

        conn=sqlite3.connect('db/omgbot.sql')
        cur = conn.cursor()
        cur.execute("INSERT INTO '%s' (who,d_rep, amount, desc) VALUES ('%s','%s','%s','%s')" % ('reviews',"@"+message.from_user.username,today,int(parts[1]),parts[2].strip()))
        conn.commit()
        cur.close()
        conn.close()
        
        update_table('reviews')
        return True, random.choice(TEXTS['aff']),""
    else:
        return False, "Неверно написан хештег! Формат:", "```Правильно!\n#отзывы *количество* *описание*```"
    
def do_penalty(message,parts):
    conn=sqlite3.connect('db/omgbot.sql')
    cur = conn.cursor()
    cur.execute("SELECT login FROM users_new WHERE login='%s'" % (parts[1]))
    fet = cur.fetchall()

    if fet:
        pass
    else:
        return False, 'Нет таких!', "```Правильно!\n#штраф *логин* *описание*```"
    
    cur.close()

    cur = conn.cursor()
    cur.execute("SELECT status FROM users_new WHERE login='%s'" % (f"@{message.from_user.username}"))
    fet2 = cur.fetchall()
    cur.close()
    if fet2:
        if int(fet2[0][0])==2 or parts[0]=='OPENCLOSE':
            today = datetime.now(pytz.timezone('Europe/Moscow')).strftime('%Y-%m-%d')
            cur = conn.cursor()
            cur.execute("INSERT INTO penalty (dt, name, desc) VALUES ('%s', '%s', '%s')" % (today, parts[1], parts[2]))
            conn.commit()
            conn.close()
            return True, random.choice(TEXTS['penalty_phrases']),""
        else:
            conn.close()
            return False, 'Ещё чего выдумал!', "```Правильно!\nШтраф выписывает только руководство```"
    else:
        return False, 'Ты кто?', "```Правильно!\nШтраф выписывает только руководство```"


clubs = {'мар':'Марьино','лен':'Ленинский','про':'Прокшино','каш':'Каширка','дми':'Дмитровка'}
action = {'#продление':'afterparty','#др':'birthday','#инициатива':'initiative'}
symb = {'#продление':10,'#др':3,'#инициатива':11}
bonus = {'#серт':'sert','#абик':'abik'}

# 1. ИСПРАВЛЕНО: Добавлены #автосим и #активация
kpi_dict = {
    '#серт': do_bonus, 
    '#абик': do_bonus, 
    '#штраф': do_penalty,
    '#двойная': do_double, 
    '#продление': do_action,
    '#др': do_action,
    '#инициатива': do_action,
    '#отзывы': do_review,
    '#автосим': do_simple_amount,
    '#активация': do_simple_amount
}

# 2. ИСПРАВЛЕНО: Чистый вызов функций без списков
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