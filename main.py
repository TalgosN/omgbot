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
from sender import safe_send
from permissions import ROLE_EMPLOYEE, get_user, initialize_permissions_schema, require_role
from db_migrations import migrate_table_names

validate_config()
bot = telebot.TeleBot(TELEGRAM_API_KEY, num_threads=4)

############################# main constants

##### taskdesk



def all_active_tasks_schedule():

    list_title=[]
    conn=sqlite3.connect('db/omgbot.sql')
    cur = conn.cursor()
    cur.execute("SELECT title FROM tasks WHERE status=?", ("В работе",))
    titles = cur.fetchall()
    cur.close()
    conn.close()

    for i in range(len(titles)):

        list_title.append(f'{i+1}) {titles[i][0]}')

    text="\n".join(list_title)
    bot.send_message(CHATS['reports'], f'🔺 Невыполненные #задачи:\n\n{text}\n\n @OMGVR_Admin_Bot') #здесь в канал репорт CHATS['reports']





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
    current_time_short = now.strftime("%H:%M")
    weekday_str = str(now.weekday())

    # --- ЛОГИКА РАССЫЛОК ---
    try:
        conn = sqlite3.connect('db/omgbot.sql')
        cur = conn.cursor()
        
        # Читаем из НОВОЙ таблицы
        cur.execute("SELECT id, text, photo, time, freq_type, freq_days FROM broadcasts WHERE status = 1")
        active_broadcasts = cur.fetchall()

        for b_id, b_text, b_photo, b_time, freq_type, freq_days in active_broadcasts:
            if b_time == current_time_short:
                should_send = False
                
                # Новая строгая проверка дней
                if freq_type == "once":
                    should_send = True
                elif freq_type == "daily":
                    should_send = True
                elif freq_type == "custom" and freq_days and (weekday_str in freq_days):
                    should_send = True
                    
                if should_send:
                    from sender import safe_send
                    safe_send(bot, CHATS['main_group'], b_text, photo=b_photo, parse_mode='HTML')
                    
                    # Отключаем только если тип строго "once" (однократно)
                    if freq_type == "once":
                        cur.execute("UPDATE broadcasts SET status = 0 WHERE id = ?", (b_id,))
                        conn.commit()

        cur.close()
        conn.close()
    except Exception as e:
        print(f"Ошибка в рассылках: {e}")
    # -----------------------

    # 3. Проверяем каждый клуб
    for club_name, info in clubs.items():
        # Пропускаем виртуальные клубы (если есть такая логика)
        if not info.get('is_physical'):
            continue
            
        conf = info['schedule']

        if current_time == conf['auto_close_time']:
            close_club(club_name, bot)

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
    schedule.every().monday.at("09:05:00", 'Europe/Moscow').do(all_active_tasks_schedule)
    schedule.every().day.at("09:00:00", 'Europe/Moscow').do(today_sched)
    
    from finance import auto_weekly_report
    schedule.every().monday.at("09:00:00", 'Europe/Moscow').do(auto_weekly_report, bot)

  
    from consumables import auto_consumables_report
    schedule.every().monday.at("09:10:00", 'Europe/Moscow').do(auto_consumables_report, bot, CHATS['reports'])

    from taskboard import auto_close_review_tasks, send_shift_review_reminders
    auto_close_review_tasks()
    schedule.every().day.at("09:10:00", 'Europe/Moscow').do(auto_close_review_tasks)
    schedule.every().day.at("09:20:00", 'Europe/Moscow').do(send_shift_review_reminders, bot)

    from rasp import start_shifton_chat_sync, start_shifton_notifications_check
    start_shifton_chat_sync()
    schedule.every().day.at("04:30:00", 'Europe/Moscow').do(start_shifton_chat_sync)
    schedule.every(15).seconds.do(start_shifton_notifications_check, bot)
    # --- ДИНАМИЧЕСКИЕ ЗАДАЧИ (Из таблицы) ---
    # Запускаем проверку каждую минуту. 
    # Она внутри себя сама разберется, во сколько кого открывать.
    schedule.every(1).minutes.do(check_dynamic_events, bot)

    # Вечный цикл
    while True:
        try:
            schedule.run_pending()
        except Exception as e:
            print(f"Ошибка фонового задания: {e}")
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
    # Статусы: -1 - заблокирован, 0 - сотрудник, 1 - ремонтник, 2 - менеджер, 3 - руководство
    cur = conn.cursor()
    cur.execute('CREATE TABLE IF  NOT EXISTS users (ID INTEGER PRIMARY KEY AUTOINCREMENT,  login varchar(50), first_name varchar(50), second_name varchar(50), nick_name varchar(50), bday date, phone varchar(50), email varchar(50),status INTEGER, chatid varchar(50))')
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
    
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS consumables (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            club TEXT,
            name TEXT NOT NULL,
            quantity INTEGER DEFAULT 0,
            min_limit INTEGER DEFAULT 5
        )
    ''')
    conn.commit()
    cur.close()

    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS consumables_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER,
            club TEXT,
            name TEXT,
            user_name TEXT,
            old_qty INTEGER,
            new_qty INTEGER,
            updated_at TIMESTAMP
        )
    ''')
    conn.commit()
    cur.close()

    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS broadcasts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT,
            photo TEXT,
            time TEXT,
            freq_type TEXT,
            freq_days TEXT,
            status INTEGER DEFAULT 1
        )
    ''')
    conn.commit()
    cur.close()

    conn.close()
    
    

def create_tables_KPI():
    conn=sqlite3.connect('db/omgbot.sql')
    cur = conn.cursor()
    cur.execute('CREATE TABLE IF NOT EXISTS bs (ID INTEGER PRIMARY KEY AUTOINCREMENT, id_bs integer, dt_bs date, name_bs varchar(50))')
    conn.commit()
    cur.close()

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
    cur.execute('CREATE TABLE IF NOT EXISTS autosim (ID INTEGER PRIMARY KEY AUTOINCREMENT, who varchar(50), d_rep date, amount REAL)')
    conn.commit()
    cur.close()

    cur = conn.cursor()
    cur.execute('CREATE TABLE IF NOT EXISTS activation (ID INTEGER PRIMARY KEY AUTOINCREMENT, who varchar(50), d_rep date, amount REAL)')
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
    cur.execute("SELECT * FROM users WHERE login=?", ("@"+message.from_user.username,))
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
    
    url = f'https://api.telegram.org/bot{TELEGRAM_API_KEY}/setMessageReaction'
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
    try:
        response = requests.post(url, json=data, timeout=10)
        response.raise_for_status()
        result = response.json()
        if not result.get("ok"):
            raise RuntimeError(result.get("description", "неизвестная ошибка Telegram"))
        return True
    except (requests.RequestException, ValueError, RuntimeError) as e:
        print(f"Ошибка отправки реакции Telegram: {e}")
        return False
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
migration_actions = migrate_table_names()
for migration_action in migration_actions:
    print(f'Миграция БД: {migration_action}')
create_tables()
initialize_permissions_schema()
create_tables_KPI()



# Переделать чтобы кидало сразу в меню без старта


@bot.message_handler(commands=['start'])
def start(message):
    
    if is_spam(message):
        if message.chat.id > 0: # Отсев конф
            
            # --- ПРОВЕРКА НА УЧАСТИЕ В ГРУППЕ ---
            
            try:
                # Запрашиваем статус пользователя в нужной группе
                user_status = bot.get_chat_member(CHATS['main_group'], message.from_user.id).status
                
                # Если пользователя там нет или он забанен
                if user_status in ['left', 'kicked']:
                    bot.send_message(message.chat.id, 'Сначала вступите в рабочую группу!')
                    return
            except Exception as e:
                # Сработает, если бот не админ в группе или указан неверный ID
                print(f"Ошибка проверки участника конфы: {e}")
                bot.send_message(message.chat.id, 'Внутренняя ошибка проверки прав доступа.')
                return
            # ------------------------------------

            if not message.from_user.username:
                bot.send_message(message.chat.id, 'Для регистрации установите публичный username в Telegram.')
                return

            login = f"@{message.from_user.username}"
            user = get_user(message)
            if not user:
                conn = sqlite3.connect('db/omgbot.sql')
                conn.row_factory = sqlite3.Row
                try:
                    same_login = conn.execute(
                        'SELECT * FROM users WHERE lower(login)=lower(?) ORDER BY ID LIMIT 1',
                        (login,),
                    ).fetchone()
                    if same_login and same_login['chatid'] not in (None, ''):
                        bot.send_message(message.chat.id, 'Этот Telegram username уже привязан к другой учётной записи. Обратитесь к руководству.')
                        return
                    with conn:
                        if same_login:
                            conn.execute(
                                'UPDATE users SET chatid=? WHERE ID=?',
                                (str(message.from_user.id), same_login['ID']),
                            )
                        else:
                            conn.execute(
                                'INSERT INTO users (login, chatid) VALUES (?, ?)',
                                (login, str(message.from_user.id)),
                            )
                finally:
                    conn.close()
                user = get_user(message)

            if user['status'] == -1:
                bot.send_message(message.chat.id, 'Доступ запрещен!')
                return

            if user['status'] is None:
                from auth import start_auth
                start_auth(message, bot)
                return

            from rasp import register_shifton_chat
            registration = register_shifton_chat(login, message.from_user.id)
            if not registration.get("ok"):
                print(f"Ошибка регистрации чата OMG Shift для {login}: {registration.get('error', 'unknown_error')}")
            else:
                try:
                    from account import apply_omg_identity, sync_google_dependencies
                    identity = apply_omg_identity(message.from_user.id, login, registration.get("employee"))
                    if identity["changed"]:
                        sync_google_dependencies(full=True)
                except ValueError as e:
                    print(f"Ошибка синхронизации профиля OMG Shift: {e}")
            hello(message.from_user.id, bot)


@bot.message_handler(commands=['weather'])
def weather(message):
    if require_role(message, bot, ROLE_EMPLOYEE) and is_spam(message):
        try: 
            from weather import get_weather
            text = get_weather()
            bot.send_message(message.chat.id,text)
        except Exception:
            bot.send_message(message.chat.id,"Прости, не знаю!")

@bot.message_handler(commands=['today'])
def cmd_today_schedule(message):
    """Ручной вызов расписания на сегодня"""
    if require_role(message, bot, ROLE_EMPLOYEE) and is_spam(message):
        try: 
            # Получаем текущую дату по Москве в нужном формате
            today_date = datetime.now(pytz.timezone('Europe/Moscow')).strftime("%Y-%m-%d")
            from rasp import get_today_schedule
            
            line = '#сегодня\n'
            # Вызываем обновленную функцию из rasp.py
            text = line + get_today_schedule(today_date)
            
            # Отправляем в тот чат, откуда вызвали команду
            bot.send_message(message.chat.id, text)
        except Exception as e:
            bot.send_message(message.chat.id, f"Произошла ошибка при получении расписания: {e}")

@bot.message_handler(commands=['repair'])
def repair_list(message):
    if require_role(message, bot, ROLE_EMPLOYEE) and is_spam(message):
        conn = sqlite3.connect('db/omgbot.sql')
        cur = conn.cursor()
        # Получаем задачи с указанием клуба
        cur.execute("SELECT id, title, club FROM tasks WHERE status='В работе' AND type='Ремонт'")
        tasks = cur.fetchall()
        cur.close()
        conn.close()  
            # Создаем словарь для группировки задач по клубам
        club_names = get_clublist_task()
        tasks_by_club = {club: [] for club in club_names}
        for task_id, title, club in tasks:
            if club in tasks_by_club:
                tasks_by_club[club].append((task_id, title))

        text_lines = []
        task_counter = 1  # Общий счетчик задач

        # Формируем текст с группировкой по клубам
        for club in club_names:
            club_tasks = tasks_by_club[club]
            if club_tasks:  # Если есть задачи для этого клуба
                text_lines.append(f"\n<b>{club}:</b>")
                for task_id, title in club_tasks:
                    text_lines.append(f"{task_counter}) {title}")
                    task_counter += 1

        text = "\n".join(text_lines) if text_lines else "Нет активных задач по ремонту"

        bot.send_message(message.chat.id, f'Вот список текущих ремонтов:\n{text}', parse_mode='HTML')
       
@bot.message_handler(func=lambda message: message.text in ['👨🏻‍💻 Смена', '🚩 Доска проблем', '👤 Аккаунт', '🗓 Расписание', '💲 Финансы', '🧑🏻‍💻 Админ панель', '📦 Расходники', '🆘 Помощь', '⚙️ Обновить настройки'])
def handle_main_menu(message):
    if require_role(message, bot, ROLE_EMPLOYEE) and is_spam(message):
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
    if require_role(message, bot, ROLE_EMPLOYEE) and is_spam(message):
        try:
            from kpi import hash_handle
            status, text, desc = hash_handle(message)

            if status == kpi.KPI_SUCCESS:
                from kpi import update_kpi
                update_kpi()
                if not send_react(message, random.choice(emojis['confirm'])):
                    bot.reply_to(message, "Запись сохранена, но не удалось поставить реакцию.")
            elif status == kpi.KPI_INVALID:
                if not send_react(message, '👎'):
                    bot.reply_to(message, "Не удалось поставить реакцию. Проверьте формат хештега.")
            else:
                if status == kpi.KPI_SAVED_ERROR:
                    from kpi import update_kpi
                    update_kpi()
                full_text = f"{text}\n{desc}".strip()
                bot.reply_to(message, full_text)

        except Exception as e:
            bot.send_message(CHATS['me'], str(e))
            bot.reply_to(message, "По-видимому, что-то пошло не так:(")
            


############################# leave and join


@bot.message_handler(content_types=["new_chat_members"])
def handler_new_member(message): # Приветсвие нового сотрудника и запись его логина в БД
    if str(message.chat.id) != str(CHATS['main_group']):
        return
    member = message.new_chat_members[0]
    if member.is_bot:
        return
    user_name = member.first_name
    
    bot.send_message(message.chat.id, "Добро пожаловать в нашу команду, {0}! В свободное время пройди регистрацию у меня (нужно написать мне /start в личные сообщения - НЕ СЮДА!)\n\nС помощью меня в будущем тебе нужно будет:\n- Открывать и закрывать смену\n- Писать обратную связь по клубам (через доску предложений)\n- Получать бонусы KPI (за продления, ДР, инициативы, продажу абонементов и сертификатов)\n\nВ обязательном порядке подпишись на наш инфоканал - https://t.me/+Q2YQbLpwLIswYWY6\n\nПо всем вопросам по моей работе обращайся к @talgos_n".format(user_name))
    
    conn=sqlite3.connect('db/omgbot.sql')
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE CAST(chatid AS TEXT)=CAST(? AS TEXT)", (member.id,))
    users = cur.fetchall()
    cur.close()
    
    if len(users)==0:
        if not member.username:
            conn.close()
            bot.send_message(message.chat.id, f'{user_name}, установи публичный username в Telegram перед регистрацией у бота.')
            return
        cur = conn.cursor()
        same_login = cur.execute(
            'SELECT 1 FROM users WHERE lower(login)=lower(?) LIMIT 1',
            (f'@{member.username}',),
        ).fetchone()
        if same_login:
            cur.close()
            conn.close()
            bot.send_message(message.chat.id, f'{user_name}, этот username уже связан с другой учётной записью. Обратись к руководству.')
            return
        cur.execute("INSERT INTO users (login, chatid) VALUES (?, ?)", ("@"+member.username, str(member.id)))
        conn.commit()
        cur.close()
    conn.close()

@bot.message_handler(content_types=['left_chat_member'])
def handler_left_member(message): # Прощание с сотрудником и изменение его статуса
    if str(message.chat.id) != str(CHATS['main_group']):
        return
    user_name = message.left_chat_member.first_name
    bot.send_message(message.chat.id, "Туда тебе и дорога, {0}!".format(user_name))
    conn=sqlite3.connect('db/omgbot.sql')
    cur = conn.cursor()
    previous = cur.execute(
        "SELECT login, status, chatid FROM users WHERE CAST(chatid AS TEXT)=CAST(? AS TEXT)",
        (message.left_chat_member.id,),
    ).fetchone()
    cur.execute("UPDATE users SET status=? WHERE CAST(chatid AS TEXT)=CAST(? AS TEXT)", (-1, message.left_chat_member.id))
    if previous and previous[1] != -1:
        cur.execute(
            '''INSERT INTO role_audit
               (changed_at, actor_chatid, actor_login, target_chatid,
                target_login, old_status, new_status)
               VALUES (datetime('now', '+3 hours'), ?, ?, ?, ?, ?, ?)''',
            ('system:main_group_leave', None, previous[2], previous[0], previous[1], -1),
        )
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

        
        
        text = f"{random.choice(TEXTS['hey'])} {user_name}!\n\n📊 Твоя статистика за месяц:\n\n⏱ Продления: {count_list[0]}\n🎂 Дни рождения: {count_list[1]}\n🌠 Инициативы: {count_list[2]}\n💸 Продано абонементов на сумму: {sale_abik} р.\n💲 Продано сертификатов на сумму: {sale_sert} р."

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

        
        
        text = f"{random.choice(TEXTS['hey'])} {user_name}!\n\n📊 Твоя статистика за все время:\n\n⏱ Продления: {count_list[0]}\n🎂 Дни рождения: {count_list[1]}\n🌠 Инициативы: {count_list[2]}\n💸 Продано абонементов на сумму: {sale_abik} р.\n💲 Продано сертификатов на сумму: {sale_sert} р."

        bot.reply_to(message, text)





@bot.message_handler(commands=['roll'])
def roll(message):
    if not require_role(message, bot, ROLE_EMPLOYEE):
        return
    emoji = random.choice(emojis['roll'])
            
    send_react(message,emoji)



def today_sched():
    """Автоматическая рассылка расписания в 09:00"""
    today_date = datetime.now(pytz.timezone('Europe/Moscow')).strftime('%Y-%m-%d')
    from rasp import get_today_schedule
    
    line = '#сегодня\n'
    text = line + get_today_schedule(today_date)
    bot.send_message(CHATS['main_group'], text)

    
########################################################################

############################# core taskboard

###### start
from taskboard import register_callback,register_callback2
register_callback (bot)
register_callback2 (bot)

from admin_panel import register_broadcast_callbacks
register_broadcast_callbacks(bot)

from consumables import register_consumables_callbacks
register_consumables_callbacks(bot)
from admin_panel import register_admin_consumables_callbacks
register_admin_consumables_callbacks(bot)

if __name__ == "__main__":
    threading.Thread(target=schedule_func, args=(bot,), name="omgbot-scheduler", daemon=True).start()

    for task_name, startup_task in (("сотрудников", update_users), ("KPI", init)):
        try:
            startup_task()
        except Exception as e:
            print(f"Ошибка стартовой синхронизации {task_name}: {e}")

    conn=sqlite3.connect('db/omgbot.sql')
    cur = conn.cursor()
    cur.execute("SELECT chatid FROM users WHERE status <>-1")
    users=cur.fetchall()
    


    cur.close()
    conn.close()

    try:
        bot.infinity_polling(timeout=10, long_polling_timeout = 5)
    except Exception as e:
        print(e)
