from telebot import *
import pygsheets
import sqlite3
from datetime import date
import pytz


clubs = {'мар':'Марьино','лен':'Ленинский','про':'Прокшино','каш':'Каширка'}

action = {'#продление':'afterparty','#др':'birthday','#инициатива':'initiative'}

symb = {'#продление':10,'#др':3,'#инициатива':11}

bonus = {'#серт':'sert','#абик':'abik'}
         
tables = ['afterparty','birthday','initiative','abik','sert']
    

def Insert(table,date,user,club,desc):
    conn=sqlite3.connect('db/omgbot.sql')
    cur = conn.cursor()
    cur.execute("INSERT INTO '%s' (dt_rep, who, club, desc,status) VALUES ('%s','%s','%s','%s','%s')" % (table,date,user,club,desc,'На проверке'))
    conn.commit()
    cur.close()
    conn.close()

def Insert_bonus(table,num,date,user,sale):
    conn=sqlite3.connect('db/omgbot.sql')
    cur = conn.cursor()
    cur.execute("INSERT INTO '%s' (num,d_rep,who,bonus) VALUES ('%s','%s','%s','%s')" % (table,num,date,user,sale))
    conn.commit()
    cur.close()
    conn.close()
########################################################        


def update_table(table):
    c = pygsheets.authorize(service_file='key/omgbot-430116-e9a4d9c69b7f.json')
    sh = c.open('KPI helper')
    
    conn=sqlite3.connect('db/omgbot.sql')
    cur = conn.cursor()
    cur.execute("SELECT * FROM '%s'" % (table))
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
        wks = sh.worksheet_by_title(table)
        ids = wks.get_values(start='A', end='A', returnas='matrix')
        statuses = wks.get_values(start='F', end='F', returnas='matrix')
        
        for k in range (len (ids)):
           
            if (statuses[k]!="") and (statuses[k]!="В обработке"):
                cur = conn.cursor()
                cur.execute("UPDATE '%s' SET status = '%s' WHERE id = '%s'" % (table, statuses[k][0],ids[k][0]))
                
    conn.commit()
    conn.close()

########################################################   





def def_count (table,user_name,begin,today):

    conn=sqlite3.connect('db/omgbot.sql')
    cur = conn.cursor()
    cur.execute("SELECT COUNT (*) FROM '%s' WHERE who = '%s' AND dt_rep BETWEEN '%s' AND datetime('%s','+1 day') AND status = 'Одобрено'" % (table, user_name, begin, today))
    count = cur.fetchall()[0][0]
    cur.close()
    conn.close() 
    return count

def def_sum_bonus (table,user_name,begin,today):

    conn=sqlite3.connect('db/omgbot.sql')
    cur = conn.cursor()
    cur.execute("SELECT SUM (bonus) FROM '%s' WHERE who = '%s' AND d_rep BETWEEN '%s' AND datetime('%s','+1 day')" % (table, user_name, begin, today))
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


def update_table_open( ):
    c = pygsheets.authorize(service_file='key/omgbot-430116-e9a4d9c69b7f.json')
    sh = c.open('Открытия и закрытия')
    conn=sqlite3.connect('db/omgbot.sql')
    cur = conn.cursor()
    cur.execute("SELECT * FROM activity")
    data = cur.fetchall()
    cur.close()
    conn.close()

    wks = sh[0]


    list1 =[]

    for i in range(len(data)):
        list2=[]
        for k in range(len(data[i])):
            list2.append(data[i][k])
        list1.append(list2)
   
    rng = wks.get_values(start='A2', end=f'E{len(list1)}', returnas='range')
    rng.clear()
    wks.update_values('A2', list1)   