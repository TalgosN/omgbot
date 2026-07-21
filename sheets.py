from telebot import *
import pygsheets
import sqlite3
from datetime import date
import pytz
import pandas as pd


clubs = {'мар':'Марьино','лен':'Ленинский','про':'Прокшино','каш':'Каширка'}

action = {'#продление':'afterparty','#др':'birthday','#инициатива':'initiative'}

symb = {'#продление':10,'#др':3,'#инициатива':11}

bonus = {'#серт':'sert','#абик':'abik'}
         
tables = ['afterparty','birthday','initiative','abik','sert']
allowed_tables = set(tables + ['reviews'])

def validate_table(table):
    if table not in allowed_tables:
        raise ValueError(f"Неизвестная KPI-таблица: {table}")
    return table
    

def Insert(table,date,user,club,desc):
    table = validate_table(table)
    conn=sqlite3.connect('db/omgbot.sql')
    cur = conn.cursor()
    cur.execute(f'INSERT INTO "{table}" (dt_rep, who, club, desc, status) VALUES (?, ?, ?, ?, ?)', (date, user, club, desc, 'На проверке'))
    conn.commit()
    cur.close()
    conn.close()

def Insert_bonus(table,num,date,user,sale):
    table = validate_table(table)
    conn=sqlite3.connect('db/omgbot.sql')
    cur = conn.cursor()
    cur.execute(f'INSERT INTO "{table}" (num, d_rep, who, bonus) VALUES (?, ?, ?, ?)', (num, date, user, sale))
    conn.commit()
    cur.close()
    conn.close()
########################################################        


def update_table(table):
    table = validate_table(table)
    c = pygsheets.authorize(service_file='key/omgbot-430116-e9a4d9c69b7f.json')
    sh = c.open('KPI helper')
    
    conn=sqlite3.connect('db/omgbot.sql')
    cur = conn.cursor()
    cur.execute(f'SELECT * FROM "{table}"')
    data = cur.fetchall()
    cur.close()
    conn.close()

    wks = sh.worksheet_by_title(table)


    list1 =[]

    for i in range(len(data)):
        list2=[]
        for k in range(len(data[i])):
            list2.append(data[i][k])
        list1.append(list2)
    
    rng = wks.get_values(start='A2', end=f'F{wks.rows}', returnas='range')
    rng.clear()
    try:
        wks.update_values('A2', list1)
    except pygsheets.InvalidArgumentValue:
        pass
    except:
        pass
    

def update_status():
    c = pygsheets.authorize(service_file='key/omgbot-430116-e9a4d9c69b7f.json')
    sh = c.open('KPI helper')
    conn=sqlite3.connect('db/omgbot.sql')

    for i in action:
        table = action[i]
        validate_table(table)
        wks = sh.worksheet_by_title(table)
        ids = wks.get_values(start='A', end='A', returnas='matrix')
        statuses = wks.get_values(start='F', end='F', returnas='matrix')
        
        for k in range (len (ids)):
           
            if (statuses[k]!="") and (statuses[k]!="В обработке"):
                cur = conn.cursor()
                cur.execute(f'UPDATE "{table}" SET status=? WHERE id=?', (statuses[k][0], ids[k][0]))
                
    conn.commit()
    conn.close()

########################################################   





def def_count (table,user_name,begin,today):

    table = validate_table(table)
    conn=sqlite3.connect('db/omgbot.sql')
    cur = conn.cursor()
    cur.execute(f'''SELECT COUNT (*) FROM "{table}"
                    WHERE who=? AND dt_rep BETWEEN ? AND datetime(?, '+1 day')
                    AND status='Одобрено' ''', (user_name, begin, today))
    count = cur.fetchall()[0][0]
    cur.close()
    conn.close() 
    return count

def def_sum_bonus (table,user_name,begin,today):

    table = validate_table(table)
    conn=sqlite3.connect('db/omgbot.sql')
    cur = conn.cursor()
    cur.execute(f'''SELECT SUM (bonus) FROM "{table}"
                    WHERE who=? AND d_rep BETWEEN ? AND datetime(?, '+1 day')''', (user_name, begin, today))
    sum1 = cur.fetchall()[0][0]
    cur.close()
    conn.close() 
    return sum1


def update_users():
    c = pygsheets.authorize(service_file='key/omgbot-430116-e9a4d9c69b7f.json')
    sh = c.open('Сотрудники')
    wks = sh.worksheet_by_title('Main')
    
    conn=sqlite3.connect('db/omgbot.sql')
    cur = conn.cursor()
    cur.execute("SELECT * FROM users_new")
    data = cur.fetchall()
    cur.close()
    conn.close()
    list1 =[]

    for i in range(len(data)):
        list2=[]
        for k in range(len(data[i])):
            list2.append(data[i][k])
        list1.append(list2)
    
    rng = wks.get_values(start='A2', end=f'A{wks.rows}', returnas='range')
    rng.clear()
    wks.update_values('A2', list1)

####################
def update_schedule_table(data):
    c = pygsheets.authorize(service_file='key/omgbot-430116-e9a4d9c69b7f.json')
    sh = c.open('Расписание')
    
    
    
    wks = sh.worksheet_by_title('ShiftON')


    list1 =[]

    for i in range(len(data)):
        list2=[]
        for k in range(len(data[i])):
            list2.append(data[i][k])
        list1.append(list2)
    
    rng = wks.get_values(start='A2', end=f'F{wks.rows}', returnas='range')
    rng.clear()
    try:
        wks.update_values('A2', list1)
    except pygsheets.InvalidArgumentValue:
        pass
    except:
        pass


def update_table_open():
    c = pygsheets.authorize(service_file='key/omgbot-430116-e9a4d9c69b7f.json')
    sh = c.open('Открытия и закрытия')
    
    # Читаем данные напрямую в DataFrame, менеджер контекста сам закроет соединение
    with sqlite3.connect('db/omgbot.sql') as conn:
        df_activity = pd.read_sql_query("SELECT * FROM activity", conn)

    # Ищем лист по названию (замени 'Activity' на реальное имя твоего первого листа)
    try:
        wks = sh.worksheet_by_title('Activity')
    except pygsheets.WorksheetNotFound:
        wks = sh.add_worksheet('Activity')
    
    # Полностью сносим старые данные и заливаем новый срез с автоподгоном границ
    wks.clear()
    wks.set_dataframe(df_activity, start='A1', copy_head=True, fit=True)
