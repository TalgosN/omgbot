import pygsheets
import sqlite3
import pandas as pd


action = {'#продление':'afterparty','#инициатива':'initiative'}

bonus = {'#серт':'sert','#абик':'abik'}
         
tables = ['afterparty','initiative','abik','sert']
allowed_tables = set(tables + ['reviews'])
table_date_columns = {
    'afterparty': 'dt_rep',
    'initiative': 'dt_rep',
    'abik': 'd_rep',
    'sert': 'd_rep',
    'reviews': 'd_rep',
}

def validate_table(table):
    if table not in allowed_tables:
        raise ValueError(f"Неизвестная KPI-таблица: {table}")
    return table
    

def Insert(table,date,user,club,desc):
    table = validate_table(table)
    conn=sqlite3.connect('db/omgbot.sql')
    cur = conn.cursor()
    cur.execute(f'INSERT INTO "{table}" (dt_rep, who, club, desc, status) VALUES (?, ?, ?, ?, ?)', (date, user, club, desc, 'Одобрено'))
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
    date_column = table_date_columns[table]
    cur.execute(
        f'''SELECT * FROM "{table}"
            WHERE date(substr("{date_column}", 1, 10)) >=
                  date('now', '+3 hours', '-3 months')'''
    )
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
    

def finalize_legacy_kpi_approval():
    """Однократно одобряет старые ожидающие записи, сохраняя отклонённые."""
    conn = sqlite3.connect('db/omgbot.sql')
    try:
        with conn:
            for table in action.values():
                validate_table(table)
                conn.execute(
                    f'''UPDATE "{table}" SET status='Одобрено'
                        WHERE status='На проверке' '''
                )
    finally:
        conn.close()

########################################################   





def update_users():
    c = pygsheets.authorize(service_file='key/omgbot-430116-e9a4d9c69b7f.json')
    sh = c.open('Сотрудники')
    wks = sh.worksheet_by_title('Main')
    
    conn=sqlite3.connect('db/omgbot.sql')
    cur = conn.cursor()
    cur.execute("SELECT * FROM users")
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
