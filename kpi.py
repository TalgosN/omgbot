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




def read_kpi():
    c = pygsheets.authorize(service_file='key/omgbot-430116-e9a4d9c69b7f.json')
    sh = c.open('KPI OMG VR')
    wks = sh.worksheet_by_title('Настройки')
    tasks = wks.get_values(start='A', end='A', returnas='matrix')
    price = wks.get_values(start='B', end='B', returnas='matrix')
    plan =wks.get_values(start='C', end='C', returnas='matrix')

    df_tasks = pd.DataFrame(tasks, columns=['Task'])
    df_price = pd.DataFrame(price, columns=['Club'])
    df_plan = pd.DataFrame(plan, columns=['Date'])

    # Объединяем DataFrame по строкам
    df_combined = pd.concat([df_tasks, df_price, df_plan], axis=1)
    return (df_combined)



def read_ank_table(): #чтение с таблицы и запись в sql
    conn=sqlite3.connect('db/omgbot.sql')
    cur = conn.cursor()
    cur.execute('CREATE TABLE IF  NOT EXISTS anketi (ID INTEGER PRIMARY KEY AUTOINCREMENT, id_ank integer, dt_ank date, club_ank varchar(50))')
    conn.commit()
    cur.close()
    

    c = pygsheets.authorize(service_file='key/omgbot-430116-e9a4d9c69b7f.json')
    
    cur = conn.cursor()
    cur.execute('DELETE FROM anketi')
    conn.commit()
    cur.close()


    sh = c.open('Клиенты, серты, абики, логины, игры, скидки')
    wks = sh.worksheet_by_title('База Клиентов')
    ids = wks.get_values(start='B', end='B', returnas='matrix')
    club_ank = wks.get_values(start='C', end='C', returnas='matrix')
    dt_ank =wks.get_values(start='K', end='K', returnas='matrix')

    # Преобразуем полученные данные в DataFrame
    df_ids = pd.DataFrame(ids, columns=['ID'])
    df_club_ank = pd.DataFrame(club_ank, columns=['Club'])
    df_club_ank['Club'] = df_club_ank['Club'].str.replace('Мариэль', 'Марьино', case=False, regex=False)
    df_dt_ank = pd.DataFrame(dt_ank, columns=['Date'])

    # Объединяем DataFrame по строкам
    df_combined = pd.concat([df_ids, df_club_ank, df_dt_ank], axis=1)

    # Преобразуем столбец с датами в формат datetime
    df_combined['Date'] = pd.to_datetime(df_combined['Date'], format='%d.%m.%Y', errors='coerce')

    # Фильтруем строки не раньше 3 месяцев
    current_date = pd.Timestamp.now()

    three_months_ago = current_date - pd.DateOffset(months=3)

    df_filtered = df_combined[df_combined['Date'] >= three_months_ago]

    cur = conn.cursor()
    for index, row in df_filtered.iterrows():
        
        cur.execute("INSERT INTO anketi (id_ank, dt_ank, club_ank) VALUES ('%s','%s','%s')"%(row['ID'], row['Date'], row['Club']))

    # Сохраняем изменения и закрываем соединение
    conn.commit()
    conn.close()
    return (df_combined)



def write_data (data,table,sheet):
    
    c = pygsheets.authorize(service_file='key/omgbot-430116-e9a4d9c69b7f.json')
    sh = c.open(table)
    wks = sh.worksheet_by_title(sheet)
    rng = wks.get_values(start='A2', end=f'F{wks.rows}', returnas='range')
    rng.clear()

    list1 =[]

    for i in range(len(data)):
        list2=[]
        for k in range(len(data[i])):
            list2.append(data[i][k])
        list1.append(list2)

    if len(list1)>0:
        wks.update_values('A2', list1)


def read_bs_table():
    pass


def read_shifts():
    locale.setlocale(locale.LC_ALL, 'ru_RU.UTF-8')

    url = "https://api2.shifton.com/oauth/token"

    payload = json.dumps(SHIFTON_CREDITNAILS)
    headers = {
    'Accept': 'application/json',
    'Content-Type': 'application/json'
    }

    response_token = requests.request("POST", url, headers=headers, data=payload) 
    response_dict_token = response_token.json()

    projectId = 17253

    headers = {'Accept': 'application/json',
    'Content-Type': 'application/json',
            'Authorization':f"Bearer {response_dict_token['access_token']}",
            'refresh_token': response_dict_token["refresh_token"]}



    # Текущая дата
    today = pd.Timestamp.now()

    # Вычисление start_time и end_time
    start_time = today - pd.DateOffset(months=3)
    end_time = today + pd.DateOffset(days=1)

    # Форматирование в строку
    start_time = start_time.strftime("%Y-%m-%d %H:%M:%S")
    end_time = end_time.strftime("%Y-%m-%d %H:%M:%S")



    days_between = (datetime.strptime(end_time, "%Y-%m-%d %H:%M:%S") - datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S")).days


    payload = json.dumps({
    "start": start_time,
    "end": end_time,
    
    })


    companyId = 16303



    conn=sqlite3.connect('db/omgbot.sql')
    cur = conn.cursor()
    cur.execute('CREATE TABLE IF  NOT EXISTS shifts (shift_second_name varchar(50), shift_first_name varchar(50), dt_shift date, club varchar(50), dur REAL)')
    conn.commit()
    cur.close()
    
    cur = conn.cursor()
    cur.execute('DELETE FROM shifts')
    conn.commit()
    cur.close()


    def add_hours(datetime_str, hours):
        # Parse the string into a datetime object
        dt = datetime.strptime(datetime_str, '%Y-%m-%d %H:%M:%S')
        # Add the specified number of hours
        new_dt = dt + timedelta(hours=hours)
        # Return the new datetime as a string
        return new_dt.strftime('%H:%M')

    def add_days(datetime_str, days):
        # Parse the string into a datetime object
        dt = datetime.strptime(datetime_str, '%Y-%m-%d %H:%M:%S')
        # Add the specified number of hours
        new_dt = dt + timedelta(days=days)
        # Return the new datetime as a string
        return new_dt.strftime('%d.%m.%Y')


    response = requests.request("GET", f'https://api.shifton.com/work/1.0.0/projects/{projectId}/shifts', headers=headers, data = payload)

    response_dict = response.json()

    response_employ = requests.request("GET", f'https://api2.shifton.com/work/1.0.0/companies/{companyId}/employees', headers=headers)

    response_dict_employ = response_employ.json()


    today = datetime.now(pytz.timezone('Europe/Moscow')).strftime('%d.%m.%Y')



    schedule_list = []  # Заголовки

    for p in range(days_between):
        str_day = add_days(start_time, p)


        # Итерируемся по сменам
        for j in response_dict:
            name = ''
            location_title = ''
            
            for k in response_dict_employ:
                if k['id'] == j["employee_id"]:
                    name = k['full_name']
            
            # Получаем название локации
            if "location" in j:
                if j["location"] is not None:
                    location_title = j["location"]["title"]
                else:
                    continue

            # Формируем информацию о смене
            if name != "":
                shift_start = f'{add_hours(j["planned_from"], 3)}'
                
                shift_end = f'{add_hours(j["planned_to"], 3)}'

                start_time_dt = datetime.strptime(j["planned_from"], '%Y-%m-%d %H:%M:%S')
                end_time_dt = datetime.strptime(j["planned_to"], '%Y-%m-%d %H:%M:%S')

                # Вычисляем разницу
                duration = end_time_dt - start_time_dt

                # Получаем длительность в часах
                duration_in_hours = round(math.fabs(duration.total_seconds() / 3600),1)
                
            
                

            day_shift = datetime.strptime(j["planned_from"], '%Y-%m-%d %H:%M:%S')
            str_day1 = day_shift.strftime('%d.%m.%Y')

            # Если дата совпадает с текущим днем, добавляем информацию о смене
            if str_day == str_day1 and name != "":
                schedule_list.append([name, str_day, shift_start,shift_end, location_title, duration_in_hours])

    
    for i in schedule_list:
        cur = conn.cursor()
        dt_str = i[1]
        dt_str = datetime.strptime(dt_str, '%d.%m.%Y')
        dt_str.strftime('%Y-%m-%d')
        cur.execute("INSERT INTO shifts (shift_second_name, shift_first_name,dt_shift, club, dur) VALUES ('%s','%s','%s','%s','%s')" % (i[0].split()[0],i[0].split()[1],dt_str,i[4],i[5]))
        conn.commit()
        cur.close()
    conn.close()
    return pd.DataFrame(schedule_list, columns=['name', 'str_day', 'shift_start','shift_end', 'location_title', 'duration_in_hours'])



def sql_select(command):
    conn=sqlite3.connect('db/omgbot.sql')
    cur = conn.cursor()
    cur.execute(command)
    a=cur.fetchall()
    cur.close()
    conn.close()
    return a


def hash_handle(message):
    try:
        message1 = ' '.join(message.text.split())
        
        parts = message1.split(' ', 2)  # Ограничиваем разбивку до 3 частей
        if len(parts) == 2:
            parts.append("")
        elif len(parts) == 3:
            pass
        else:
            return False, "Не понимаю о чем ты 🙈","```Правильно!\nЕсли не знаешь как написать хештег, пиши /help```"

        if parts[0] in kpi_dict:
            flag,answer,desc = kpi_dict[parts[0]](message,parts)
        else:
            return False, "Не понимаю о чем ты 🙈","```Правильно!\nЕсли не знаешь как написать хештег, пиши /help```"

        return flag, answer, desc
    except Exception as e:
        print (e)
        return True, "Что-то пошло не так!",""


def do_action(message,parts):
    if "факт" in message.text.lower():
        return False, 'Даже у меня есть имя, значит и у него есть!',  "```Правильно!\nНикаких 'фактов'!```"
    else:
        action_do = parts[0].lower()
        club = parts[1].lower()
        if club in clubs:
            club = clubs[club]
            table = action[action_do]
            user_name = "@"+message.from_user.username
            today = datetime.now(pytz.timezone('Europe/Moscow')).strftime('%Y-%m-%d %H:%M:%S')
            desc = parts[2].strip()
            if len(desc)>1024:
                return False, "Слишком длинно!", "```Правильно!\nПожалуйста, меньше 1024 символов```"
            else:                                     
                update_status()
                Insert(table, today, user_name,club,desc)
                update_table(table)
                return True, random.choice(TEXTS['aff']),""
        else:
            return False, "Неверно написан хештег!", "```Правильно!\nКоды клубов: лен, мар, каш, про, дми```"

def do_double(message,parts):
    
    if parts[1].strip().isnumeric() and len (parts)==3:
        today = datetime.now(pytz.timezone('Europe/Moscow')).strftime('%Y-%m-%d')

        conn=sqlite3.connect('db/omgbot.sql')
        cur = conn.cursor()
        cur.execute("INSERT INTO '%s' (who,d_rep, amount, desc) VALUES ('%s','%s','%s','%s')" % ('double',"@"+message.from_user.username,today,int(parts[1]),parts[2].strip()))
        conn.commit()
        cur.close()
        conn.close()

        return True, random.choice(TEXTS['aff']),""
    else:
        return False, "Неверно написан хештег! Формат:", "```Правильно!\n#двойная *часов* *описание*```"


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
         



kpi_dict={'#серт':do_bonus, '#абик':do_bonus, '#штраф':do_penalty,'#двойная':do_double, '#продление':do_action,'#др':do_action,'#инициатива':do_action,'#отзывы':do_review}
def init():
                
    tables = [read_ank_table(),
              read_bs_table(),
              read_shifts(),
              write_data(sql_select(sql_scripts.shifts_ext),'KPI helper','shifts'),
              write_data(sql_select(sql_scripts.union),'KPI OMG VR','data'),
              write_data(sql_select(sql_scripts.shifts),'KPI OMG VR','shifts'),
              write_data(sql_select(sql_scripts.records),'KPI OMG VR','raw')]

    for i in tables:
        i

def update_kpi():
    tables = [read_ank_table(),
              read_bs_table(),
              
              write_data(sql_select(sql_scripts.union),'KPI OMG VR','data'),
              write_data(sql_select(sql_scripts.records),'KPI OMG VR','raw')
              ]

    for i in tables:
        i