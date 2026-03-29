import telebot
from constants import *
from sheets import *
import sqlite3
import pytz
import schedule
import threading
from menu import hello
from datetime import datetime, timedelta
import time
from openclose import send_status_close, send_status_open, close_club
import kpi
from kpi import init
import requests

bot = telebot.TeleBot(TELEGRAM_API_KEY, num_threads=4)

############################# main constants

##### taskdesk



def all_active_tasks_schedule():

    list_title=[]
    conn=sqlite3.connect('db/omgbot.sql')
    cur = conn.cursor()
    cur.execute("SELECT title FROM tasks WHERE status='%s'" % ("В работе"))
    titles = cur.fetchall()
    cur.close()
    conn.close()

    for i in range(len(titles)):

        list_title.append(f'{i+1}) {titles[i][0]}')

    text="\n".join(list_title)
    bot.send_message(CHATS['reports'], f'Невыполненные задачи:\n\n{text}\n\n @OMGVR_Admin_Bot') #здесь в канал репорт CHATS['reports']





def send_status_bot(): #отправка статуса о работе бота в ЛС
    bot.send_message(CHATS['me'], f'Опять работа:(')
    




def check_dynamic_events(bot):
    """Эта функция запускается каждую минуту"""
    
    # 1. Читаем СВЕЖИЙ конфиг
    clubs = get_clubs()
    
    # 2. Получаем текущее время и день недели
    tz = pytz.timezone('Europe/Moscow')
    now = datetime.now(tz)
    current_time = now.strftime("%H:%M:00") # "10:00:00"
    weekday = now.weekday() # 0 = Понедельник, 6 = Воскресенье

    # 3. Проверяем каждый клуб
    for club_name, info in clubs.items():
        # Пропускаем виртуальные клубы (если есть такая логика)
        if not info.get('is_physical'):
            continue
            
        conf = info['schedule']

        # --- ПРОВЕРКА НА ЗАКРЫТИЕ (status_close_time) ---
        if current_time == conf['status_close_time']:
            send_status_close(club_name, bot)

        # --- ПРОВЕРКА НА ОТКРЫТИЕ ---
        # Если будни (0-4)
        if 0 <= weekday <= 4:
            if current_time == conf['open']['weekdays']:
                send_status_open(club_name, bot)
        
        # Если выходные (5-6)
        else:
            if current_time == conf['open']['weekend']:
                send_status_open(club_name, bot)

def schedule_func(bot): # Не забудь передать bot!
    # --- БАЗОВЫЕ ЗАДАЧИ (Статические) ---
    # Эти задачи не зависят от clubs.json, их можно оставить жесткими
    schedule.every().day.at("10:00:00", 'Europe/Moscow').do(init)
    schedule.every().monday.at("09:00:00", 'Europe/Moscow').do(all_active_tasks_schedule)
    schedule.every().day.at("09:00:00", 'Europe/Moscow').do(today_sched)
    
    # --- СТАТИЧЕСКИЕ ЗАДАЧИ КЛУБОВ ---
    # Например, принудительное закрытие в 05:00 (если оно всегда в 5 утра)
    # Можно оставить так, пробежавшись один раз при старте
    startup_clubs = get_clubs()
    for club_name, info in startup_clubs.items():
        if info.get('is_physical'):
            schedule.every().day.at("05:00:00", 'Europe/Moscow').do(close_club, club_name, bot)

    # --- ДИНАМИЧЕСКИЕ ЗАДАЧИ (Из таблицы) ---
    # Запускаем проверку каждую минуту. 
    # Она внутри себя сама разберется, во сколько кого открывать.
    schedule.every(1).minutes.do(check_dynamic_events, bot)

    # Вечный цикл
    while True:
        schedule.run_pending()
        time.sleep(1)






# main
def create_tables():
    # Задачи
    conn=sqlite3.connect('db/omgbot.sql')
    cur = conn.cursor()
    cur.execute('CREATE TABLE IF  NOT EXISTS tasks (ID INTEGER PRIMARY KEY AUTOINCREMENT, dtrep date, type varchar(50), club varchar(50), title varchar(50), photo BLOB, desc varchar(1024),status varchar(10), dtfb date,feedback varchar(1024))')
    conn.commit()
    cur.close()
    # Открытия и закрытия
    cur = conn.cursor()
    cur.execute('CREATE TABLE IF  NOT EXISTS activity (ID INTEGER PRIMARY KEY AUTOINCREMENT, dtrep datetime, login varchar(50), club varchar(50), action varchar(50))')
    conn.commit()
    cur.close()
    # Новая таблица юзеров
    # Статусы: 1 - Админ, 0 - Действующий сотрудник, -1 - Бывший сотрудник
    cur = conn.cursor()
    cur.execute('CREATE TABLE IF  NOT EXISTS users_new (ID INTEGER PRIMARY KEY AUTOINCREMENT,  login varchar(50), first_name varchar(50), second_name varchar(50), nick_name varchar(50), bday date, phone varchar(50), email varchar(50),status INTEGER, chatid varchar(50))')
    conn.commit()
    cur.close()
    #таблица нала
    cur = conn.cursor()
    cur.execute('CREATE TABLE IF  NOT EXISTS nal (ID INTEGER PRIMARY KEY AUTOINCREMENT, drep date, club varchar(50), amount INTEGER)')
    conn.commit()
    cur.close()
    # штрафы

    cur = conn.cursor()
    cur.execute('CREATE TABLE IF NOT EXISTS penalty (ID INTEGER PRIMARY KEY, dt DATE, name varchar(50), desc varchar(50))')
    conn.commit()
    cur.close()

    conn.close()
    
    

def create_tables_KPI():
    conn=sqlite3.connect('db/omgbot.sql')
    cur = conn.cursor()
    cur.execute('CREATE TABLE IF  NOT EXISTS afterparty (ID INTEGER PRIMARY KEY AUTOINCREMENT, dt_rep datetime, who varchar(50), club varchar(50), desc varchar(1024), status varchar(50))')
    conn.commit()
    cur.close()

    cur = conn.cursor()
    cur.execute('CREATE TABLE IF  NOT EXISTS birthday (ID INTEGER PRIMARY KEY AUTOINCREMENT, dt_rep datetime, who varchar(50), club varchar(50), desc varchar(1024), status varchar(50))')
    conn.commit()
    cur.close()

    cur = conn.cursor()
    cur.execute('CREATE TABLE IF  NOT EXISTS initiative (ID INTEGER PRIMARY KEY AUTOINCREMENT, dt_rep datetime, who varchar(50), club varchar(50), desc varchar(1024), status varchar(50))')
    conn.commit()
    cur.close()
    
    cur = conn.cursor()
    cur.execute('CREATE TABLE IF  NOT EXISTS sert (ID INTEGER PRIMARY KEY AUTOINCREMENT, num varchar (4),d_rep date, who varchar(50), bonus integer)')
    conn.commit()
    cur.close()
    
    cur = conn.cursor()
    cur.execute('CREATE TABLE IF  NOT EXISTS abik (ID INTEGER PRIMARY KEY AUTOINCREMENT, num varchar (3),d_rep date, who varchar(50), bonus integer)')
    conn.commit()
    cur.close()
    
    cur = conn.cursor()
    cur.execute('CREATE TABLE IF  NOT EXISTS reviews (ID INTEGER PRIMARY KEY AUTOINCREMENT,  who varchar(50), d_rep date, amount integer, desc varchar(50))')
    conn.commit()
    cur.close()

    cur = conn.cursor()
    cur.execute('CREATE TABLE IF  NOT EXISTS double (ID INTEGER PRIMARY KEY AUTOINCREMENT,  who varchar(50), d_rep date, amount integer, desc varchar(50))')
    conn.commit()
    cur.close()

    conn.close()




def define_name(message):
    conn=sqlite3.connect('db/omgbot.sql')
    cur = conn.cursor()
    cur.execute("SELECT * FROM users_new WHERE login='%s'" % ("@"+message.from_user.username))
    users = cur.fetchall()
    cur.close()
    conn.close()
    return users

user_last_message_time = {}

def is_spam(message):
    user_id = message.from_user.id
    current_time = time.time()

    if user_id in user_last_message_time:
        if current_time - user_last_message_time[user_id] < MESSAGE_LIMIT_TIME:
            # Молча игнорируем спам, чтобы не засорять чат ответами "не так часто"
            return False
            
    user_last_message_time[user_id] = current_time
    return True

def send_react(message,emoji):
    
    url = f'https://api.telegram.org/bot6942615682:AAEhsdJuy6M8JwQ57pimD6XA3QIu9dGIRbc/setMessageReaction'
    data = {
        'chat_id': message.chat.id,
        'message_id': message.id,
        'reaction': [
            {
                'type': 'emoji',
                
                'emoji': emoji # Вариант со списком из смайликов.
            }
        ],
        'is_big': False
    }
    response = requests.post(url, json=data)
    result = response.json()
'''
Indexes of users
0 - id
1 - login
2 - name
3 - second name
4 - nick_name
5 - Birthday
6 - phone
7 - email
8 - status
9 - chatid
'''



############################# start
create_tables()
create_tables_KPI()



# Переделать чтобы кидало сразу в меню без старта


@bot.message_handler(commands=['start'])
def start(message):
    bot.send_message(message.chat.id,f'message.chat.id')
    if is_spam(message):
        if message.chat.id>0: # Отсев конф

            users = define_name(message)

            if len(users)==0 or users[0][8]==-1: #отсев посторонних и ушедших

                bot.send_message(message.chat.id, 'Доступ запрещен!')
                
            else:

                if users[0][9]==None or users[0][9]=="" : # есть в КФ но нет записи в БД, начнем авторизацию
                    from auth import start_auth
                    
                    start_auth(message,bot)
                    
                else:
                    

                    hello(message.chat.id,bot)


@bot.message_handler(commands=['weather'])
def weather(message):
    
    if is_spam(message):
        try: 
            from weather import get_weather
            text = get_weather()
            bot.send_message(message.chat.id,text)
        except Exception:
            bot.send_message(message.chat.id,"Прости, не знаю!")

@bot.message_handler(commands=['repair'])
def repair_list(message):
    
    if is_spam(message):
        conn = sqlite3.connect('db/omgbot.sql')
        cur = conn.cursor()
        # Получаем задачи с указанием клуба
        cur.execute("SELECT id, title, club FROM tasks WHERE status='В работе' AND type='Ремонт'")
        tasks = cur.fetchall()
        cur.close()
        conn.close()  
            # Создаем словарь для группировки задач по клубам
        tasks_by_club = {club: [] for club in clublist_task}
        for task_id, title, club in tasks:
            if club in tasks_by_club:
                tasks_by_club[club].append((task_id, title))

        text_lines = []
        task_counter = 1  # Общий счетчик задач

        # Формируем текст с группировкой по клубам
        for club in clublist_task:
            club_tasks = tasks_by_club[club]
            if club_tasks:  # Если есть задачи для этого клуба
                text_lines.append(f"\n<b>{club}:</b>")
                for task_id, title in club_tasks:
                    text_lines.append(f"{task_counter}) {title}")
                    task_counter += 1

        text = "\n".join(text_lines) if text_lines else "Нет активных задач по ремонту"

        bot.send_message(message.chat.id, f'Вот список текущих ремонтов:\n{text}', parse_mode='HTML')
       
@bot.message_handler(func=lambda message: message.text in ['👨🏻‍💻 Смена', '🚩 Доска проблем', '👤 Аккаунт', '🗓 Расписание', '💲 Финансы', '🆘 Помощь', '⚙️ Обновить настройки'])
def handle_main_menu(message):
    if is_spam(message):
        bot.clear_step_handler_by_chat_id(message.chat.id)
        from menu import func
        func(message, bot)
        
@bot.message_handler(func=lambda message: message.text is not None and '/' not in message.text and not message.text.startswith('#'))
def SaySmth(message): #fun talk
    if message.text.lower().find('кц офф')!=-1:
        send_react(message,"😴")

    elif message.text.lower().find('кц')!=-1 and message.text.lower().find('афк')!=-1:
        send_react(message,"👀")

    elif message.text.lower().find('кц')!=-1 and message.text.lower().find('с вами')!=-1:
        send_react(message,random.choice(emojis['confirm']))

    elif message.text.lower().find('виарыч')!=-1 and message.text.lower().find('как дела')!=-1:
        send_react(message, random.choice(emojis['mood']))
        bot.reply_to(message, random.choice(TEXTS['how_are_you']))
        

    elif message.text.lower().find('виарыч')!=-1 and message.text.lower().find('жги')!=-1:
        send_react(message,"🔥")
        bot.reply_to(message, random.choice(TEXTS['burn']))


        
@bot.message_handler(func=lambda message: message.text is not None and '/' not in message.text and message.text.startswith('#'))
def HashTags(message): #KPI handler
    if is_spam(message):
        try:
            from kpi import hash_handle
            flag,text,desc = hash_handle (message)
            if flag:
                send_react(message,random.choice(emojis['confirm']))
                from kpi import update_kpi
                update_kpi()
            else:
                send_react(message,'👎')

        except Exception as e:
            bot.send_message (CHATS['me'], e)
            bot.reply_to(message, f"По-видимому, что-то пошло не так:(")
            


############################# leave and join


@bot.message_handler(content_types=["new_chat_members"])
def handler_new_member(message): # Приветсвие нового сотрудника и запись его логина в БД
    
    user_name = message.new_chat_members[0].first_name
    
    bot.send_message(message.chat.id, "Добро пожаловать в нашу команду, {0}! В свободное время пройди регистрацию у меня (нужно написать мне /start в личные сообщения - НЕ СЮДА!)\n\nС помощью меня в будущем тебе нужно будет:\n- Открывать и закрывать смену\n- Писать обратную связь по клубам (через доску предложений)\n- Получать бонусы KPI (за продления, ДР, инициативы, продажу абонементов и сертификатов)\n\nВ обязательном порядке подпишись на наш инфоканал - https://t.me/+Q2YQbLpwLIswYWY6\n\nПо всем вопросам по моей работе обращайся к @talgos_n".format(user_name))
    
    conn=sqlite3.connect('db/omgbot.sql')
    cur = conn.cursor()
    cur.execute("SELECT * FROM users_new WHERE login='%s'" % ("@"+message.new_chat_members[0].username))
    users = cur.fetchall()
    cur.close()
    
    if len(users)==0:
        cur = conn.cursor()
        cur.execute("INSERT INTO users_new (login) VALUES ('%s')" % ("@"+message.new_chat_members[0].username))
        conn.commit()
        cur.close()
    conn.close()

@bot.message_handler(content_types=['left_chat_member'])
def handler_left_member(message): # Прощание с сотрудником и изменение его статуса
    
    user_name = message.left_chat_member.first_name
    bot.send_message(message.chat.id, "Туда тебе и дорога, {0}!".format(user_name))
    conn=sqlite3.connect('db/omgbot.sql')
    cur = conn.cursor()
    cur.execute("UPDATE users_new SET status ='%s' WHERE login ='%s' " % (-1,"@"+message.left_chat_member.username))
    conn.commit()
    cur.close()
    conn.close()

#############################


  


def stats(message, bot=bot):
    if is_spam(message):
        update_status()
        for i in range(len(tables)):
            update_table(tables[i])
            
        if "@" not in message.text and " " not in message.text:
            user_name = "@"+message.from_user.username
        elif  "@OMGVR_Admin_Bot" in message.text:
            user_name = "@"+message.from_user.username
        else:
            user_name = message.text[message.text.find(" ")+1:]

        begin = datetime.now().replace(day=1)
        today = datetime.now(pytz.timezone('Europe/Moscow')).strftime('%Y-%m-%d')
        count_list=[]
        for i in action:
            count_list.append(str(def_count(action[i],user_name,begin,today)))       
        
        sale_abik = def_sum_bonus('abik',user_name,begin,today)
        sale_sert = def_sum_bonus('sert',user_name,begin,today)

        if sale_abik==None:
            sale_abik = 0
        
        if sale_sert==None:
            sale_sert = 0

        
        
        text = f'{random.choice(TEXTS['hey'])} {user_name}!\n\n📊 Твоя статистика за месяц:\n\n⏱ Продления: {count_list[0]}\n🎂 Дни рождения: {count_list[1]}\n🌠 Инициативы: {count_list[2]}\n💸 Продано абонементов на сумму: {sale_abik} р.\n💲 Продано сертификатов на сумму: {sale_sert} р.'

        bot.reply_to(message, text)



def statsall(message, bot=bot):
    if is_spam(message):
        update_status()
        
        for i in range(len(tables)):
            update_table(tables[i])
            
        if "@" not in message.text and " " not in message.text:
            user_name = "@"+message.from_user.username
        elif  "@OMGVR_Admin_Bot" in message.text:
            user_name = "@"+message.from_user.username
        else:
            user_name = message.text[message.text.find(" ")+1:]

        begin = datetime.now().replace(year=2022)
        today = datetime.now(pytz.timezone('Europe/Moscow')).strftime('%Y-%m-%d')
        count_list=[]
        for i in action:
            count_list.append(str(def_count(action[i],user_name,begin,today)))       
        
        sale_abik = def_sum_bonus('abik',user_name,begin,today)
        sale_sert = def_sum_bonus('sert',user_name,begin,today)

        if sale_abik==None:
            sale_abik = 0
        
        if sale_sert==None:
            sale_sert = 0

        
        
        text = f'{random.choice(TEXTS['hey'])} {user_name}!\n\n📊 Твоя статистика за все время:\n\n⏱ Продления: {count_list[0]}\n🎂 Дни рождения: {count_list[1]}\n🌠 Инициативы: {count_list[2]}\n💸 Продано абонементов на сумму: {sale_abik} р.\n💲 Продано сертификатов на сумму: {sale_sert} р.'

        bot.reply_to(message, text)





@bot.message_handler(commands=['roll'])
def roll(message):
    emoji = random.choice(emojis['roll'])
            
    send_react(message,emoji)







def today_sched():

    date_start_dt = datetime.now(pytz.timezone('Europe/Moscow')).replace(hour=0, minute=0, second=0)
    date_start = date_start_dt.strftime('%Y-%m-%d %H:%M:%S')
    date_end = (date_start_dt+timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S')
    from rasp import get_today_schedule
    
    line='#сегодня\n'
    text = get_today_schedule (date_start, date_end)
    text=line+text
    bot.send_message(CHATS['main_group'], text)



    
########################################################################

############################# core taskboard

###### start
from taskboard import register_callback,register_callback2
register_callback (bot)
register_callback2 (bot)


if __name__ == "__main__":
    threading.Thread(target=schedule_func, args=(bot,)).start()
    update_users()
    init()

    conn=sqlite3.connect('db/omgbot.sql')
    cur = conn.cursor()
    cur.execute("SELECT chatid FROM users_new WHERE status <>-1")
    users=cur.fetchall()
    


    cur.close()
    conn.close()

    try:
        bot.infinity_polling(timeout=10, long_polling_timeout = 5)
    except Exception as e:
        print(e)
