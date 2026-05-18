from telebot import *
from constants import *
from sheets import *
import requests
import json
from datetime import datetime, timedelta
import math
import locale
from weather import get_weather
locale.setlocale(locale.LC_ALL, 'ru_RU.UTF-8')

############ Additional functions

def add_hours(datetime_str, hours):
    # Parse the string into a datetime object
    dt = datetime.strptime(datetime_str, '%Y-%m-%d %H:%M:%S')
    # Add the specified number of hours
    new_dt = dt + timedelta(hours=hours)
    # Return the new datetime as a string
    return new_dt.strftime('%H:%M')


def add_days(datetime_str, days, dt_format):
    # Parse the string into a datetime object
    dt = datetime.strptime(datetime_str, '%Y-%m-%d %H:%M:%S')
    # Add the specified number of hours
    new_dt = dt + timedelta(days=days)
    # Return the new datetime as a string
    return new_dt.strftime(dt_format)

def day_of_week (datetime_str):
    # Parse the string into a datetime object
    dt = datetime.strptime(datetime_str, '%Y-%m-%d %H:%M:%S')
    
    # Return the new datetime as a string
    return dt.strftime('%d.%m, %A')

def last_monday(datetime_str):
    # Parse the string into a datetime object
    dt = datetime.strptime(datetime_str, '%Y-%m-%d %H:%M:%S')
    
    # Calculate the last Monday
    days_since_monday = dt.weekday()  # Monday is 0 and Sunday is 6
    last_monday_date = dt - timedelta(days=days_since_monday)
    
    last_monday_date = last_monday_date.replace(hour=0, minute=0, second=0)
    
    # Return the last Monday as a string
    return last_monday_date.strftime('%Y-%m-%d %H:%M:%S')



############ Get ShiftOn Token (it changes weekly)
def get_shifton_token():
    
    url = "https://api2.shifton.com/oauth/token"

    payload = json.dumps(SHIFTON_CREDITNAILS)
    headers = {'Accept': 'application/json',
               'Content-Type': 'application/json'}

    response_token = requests.request("POST", url, headers=headers, data=payload) 
    response_dict_token = response_token.json()
    return response_dict_token

############ some constants

clubs_color = {'Прокшино':'🔴', 'Каширка':'🟠', 'Марьино':'🟣', 'Коллцентр':'🔈', 'Ленинский':'🟢','Дмитровка':'🟡'}
emojis = ['💀', '🤖', '🍓', '😎', '🤓', '🙄', '👽', '👻', '😈', '😇', '😅', '🤑', '😉', '🐯', '🌝', '🌚', '🥟']

funclist_rasp=('📄 Расписание на сегодня','📑 Расписание на неделю', '⬅️ Вернуться')
funclist_rasp_week=('👨🏻‍💻 По сотрудникам','🗓 По датам', '🔴 По клубам','⬅️ Вернуться')




projectId = 17253
companyId = 16303
scheduleId = 27347

############ enterpoint bot

def rasp(message,bot):
    
    bot.send_message(message.chat.id, f'Этот раздел посвящен расписанию и всё что с ним связано!')
    markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add(*funclist_rasp)
    bot.send_message(message.chat.id, 'Что вы хотите сделать? 👀', reply_markup=markup)
    bot.register_next_step_handler(message, func_rasp,bot)


def func_rasp(message,bot):
    
    if message.text=='📄 Расписание на сегодня':
        
        
        # Get today's date in the specified timezone
        current_date = datetime.now(pytz.timezone('Europe/Moscow')).replace(hour=0, minute=0, second=0, microsecond=0)

        # Format today's date
        formatted_today = current_date.strftime("%Y-%m-%d %H:%M:%S")

        # Calculate tomorrow's date
        tomorrow_date = current_date + timedelta(days=1)

        # Format tomorrow's date
        formatted_tomorrow = tomorrow_date.strftime("%Y-%m-%d %H:%M:%S")
        
        try:
        
            today_text = get_today_schedule (formatted_today, formatted_tomorrow)
            bot.send_message(message.chat.id, today_text)
            markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
            markup.add(*funclist_rasp)
            bot.send_message(message.chat.id, 'Что вы хотите сделать? 👀', reply_markup=markup)
            bot.register_next_step_handler(message, func_rasp,bot)
            
        except Exception as e:
        
            bot.send_message(message.chat.id, 'Что-то пошло не так! Перешлите ошибку ниже техническому специалисту')
            bot.send_message(message.chat.id, e)

    elif message.text=='📑 Расписание на неделю':
        
        markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
        markup.add(*funclist_rasp_week)
        bot.send_message(message.chat.id, f'Выбери в каком формате ты хочешь получить расписание', reply_markup=markup)
        
        bot.register_next_step_handler(message, handle_data, bot)

        
    elif message.text=='⬅️ Вернуться':
        returnback(message,bot)
       
    else:
        
        markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
        markup.add(*funclist_rasp)
        bot.send_message(message.chat.id, 'Что вы хотите сделать? 👀', reply_markup=markup)
        bot.register_next_step_handler(message, func_rasp,bot)
        
        
        
def returnback(message,bot):
        from menu import hello
        hello (message.chat.id,bot)
        

def get_today_schedule(date_start, date_end):
    response_dict, response_dict_employ = get_shifts_and_employees(date_start, date_end)

    today = datetime.now(pytz.timezone('Europe/Moscow')).strftime('%d.%m.%Y')
    weekday = datetime.now(pytz.timezone('Europe/Moscow')).strftime('%A')

    full_text = f'{today}, {weekday.capitalize()}\n\n'
    full_text += f'{get_weather()}\n\n'
    
    # 1. Достаем юзернеймы из базы данных
    import sqlite3
    conn = sqlite3.connect('db/omgbot.sql')
    cur = conn.cursor()
    # Берем всех активных сотрудников
    cur.execute("SELECT first_name, second_name, login FROM users_new WHERE status <> -1")
    
    db_users = {}
    for f_name, s_name, login in cur.fetchall():
        if f_name and s_name:
            # Сохраняем оба варианта склейки на случай, если в ShiftOn они записаны по-разному
            db_users[f"{f_name.strip()} {s_name.strip()}"] = login
            db_users[f"{s_name.strip()} {f_name.strip()}"] = login
    cur.close()
    conn.close()

    # 2. Формируем текст
    for i in clubs_color:
        full_text += f'{clubs_color[i]} {i}\n'
        for j in response_dict:
            name = ''
            for k in response_dict_employ:
                if k['id'] == j["employee_id"]:
                    name = k['full_name']
            
            if name != "":
                # Ищем username в нашем словаре. Если нет, возвращаем пустую строку
                username = db_users.get(name.strip(), "")
                
                # Если юзернейм нашелся, приклеиваем его в скобках
                display_name = f"{name} ({username})" if username else name
                
                text = f'{display_name} c {add_hours(j["planned_from"],3)} до {add_hours(j["planned_to"],3)}\n'
            else:
                text = f'СВОБОДНАЯ СМЕНА c {add_hours(j["planned_from"],3)} до {add_hours(j["planned_to"],3)}\n'
            
            # Защита от свободных смен без привязки к клубу
            if j.get("location") is not None:
                if j["location"]["title"] == i:
                    full_text += text
                    
        full_text += '\n'        

    return full_text   
    
def get_shifts_and_employees (date_start, date_end):


    response_dict_token = get_shifton_token()
    
    headers = {'Accept': 'application/json',
               'Content-Type': 'application/json',
               'Authorization':f"Bearer {response_dict_token['access_token']}",
               'refresh_token': response_dict_token["refresh_token"]}


    payload = json.dumps({"start": date_start,
                          "end": date_end})
    
    response = requests.request("GET", f'https://api.shifton.com/work/1.0.0/projects/{projectId}/shifts', headers=headers, data = payload)

    response_dict = response.json()

    response_employ = requests.request("GET", f'https://api2.shifton.com/work/1.0.0/companies/{companyId}/employees', headers=headers)

    response_dict_employ = response_employ.json()
    
    return  response_dict,response_dict_employ
    
    
    
def get_week_by_employee (date_user):
    
    date_start_dt = datetime.strptime(date_user, '%d.%m.%Y')
   
    date_start_iso = last_monday(date_start_dt.strftime('%Y-%m-%d %H:%M:%S'))
    date_end_iso = add_days(date_start_dt.strftime('%Y-%m-%d %H:%M:%S'), 7, '%Y-%m-%d %H:%M:%S')
    
    response_dict, response_dict_employ = get_shifts_and_employees (date_start_iso, date_end_iso)

    full_text=f"Расписание на неделю {datetime.strptime(date_start_iso, '%Y-%m-%d %H:%M:%S').strftime('%d.%m')}-{datetime.strptime(date_end_iso, '%Y-%m-%d %H:%M:%S').strftime('%d.%m')}\n\n"

    # Словарь для хранения смен сотрудников с группировкой по дням и локациям
    employee_shifts = {}

    for p in range(7):
    
        str_day = add_days(date_start_iso, p, '%d.%m.%Y')
        dtt = datetime.strptime(str_day, '%d.%m.%Y')
        dtt_day = dtt.strftime('%A')

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

            if name != "":
                shift_info = f'{dtt_day}: с {add_hours(j["planned_from"], 3)} до {add_hours(j["planned_to"], 3)} {location_title}\n'
            else:
                shift_info = f'{dtt_day}: СВОБОДНАЯ СМЕНА с {add_hours(j["planned_from"], 3)} до {add_hours(j["planned_to"], 3)} {location_title}\n'

            day_shift = datetime.strptime(j["planned_from"], '%Y-%m-%d %H:%M:%S')
            str_day1 = day_shift.strftime('%d.%m.%Y')

            # Если дата совпадает с текущим днем, добавляем информацию о смене
            if str_day == str_day1:
                if name not in employee_shifts:
                    employee_shifts[name] = {}
                if dtt_day not in employee_shifts[name]:
                    employee_shifts[name][dtt_day] = []
                employee_shifts[name][dtt_day].append(shift_info)

    # Теперь формируем текстовый вывод
    for employee, shifts in employee_shifts.items():

        if employee=="":
            employee="Свободные смены!"
        else:
            employee=f"{random.choice(emojis)} {employee}"
        full_text += f'{employee}:\n'
        for day, shift_infos in shifts.items():
            for shift_info in shift_infos:
                full_text += f'  {shift_info}'
        full_text += '\n'



    # В конце можно вывести или сохранить full_text
    return full_text
    


def get_week_by_day (date_user):
    
    date_start_dt = datetime.strptime(date_user, '%d.%m.%Y')
   
    date_start_iso = last_monday(date_start_dt.strftime('%Y-%m-%d %H:%M:%S'))
    date_end_iso = add_days(date_start_dt.strftime('%Y-%m-%d %H:%M:%S'), 7, '%Y-%m-%d %H:%M:%S')
    
    response_dict, response_dict_employ = get_shifts_and_employees (date_start_iso, date_end_iso)
    
    full_text=f"Расписание на неделю {datetime.strptime(date_start_iso, '%Y-%m-%d %H:%M:%S').strftime('%d.%m')}-{datetime.strptime(date_end_iso, '%Y-%m-%d %H:%M:%S').strftime('%d.%m')}\n\n"


    for p in range (7):
        str_day = add_days(date_start_iso,p,'%d.%m.%Y')
        dtt = datetime.strptime(str_day,'%d.%m.%Y')
        dtt_day = dtt.strftime('%A')
        
        full_text=full_text+f'{str_day}, {dtt_day}\n\n'
            
        for i in clubs_color:
            full_text = full_text +f'{clubs_color[i]} {i}\n'
            for j in response_dict:
                name = ''
                for k in response_dict_employ:
            
            
                    if k['id']==j["employee_id"]:
                        name = k['full_name']
              
                if name!="":
                    text=f'{name} c {add_hours(j["planned_from"],3)} до {add_hours(j["planned_to"],3)}\n'
                else:
                    text=f'СВОБОДНАЯ СМЕНА c {add_hours(j["planned_from"],3)} до {add_hours(j["planned_to"],3)}\n'
            
                day_shift = datetime.strptime(j["planned_from"], '%Y-%m-%d %H:%M:%S')
                str_day1 = day_shift.strftime('%d.%m.%Y')

                
                if j["location"] is not None:
                    if (j["location"]["title"])==i and str_day==str_day1:
                        full_text=full_text+text
                else:
                    continue

                

            full_text=full_text+'\n'        

        
    return full_text
    
    
    
    
def get_week_by_club (date_user):
    
    date_start_dt = datetime.strptime(date_user, '%d.%m.%Y')
   
    date_start_iso = last_monday(date_start_dt.strftime('%Y-%m-%d %H:%M:%S'))
    date_end_iso = add_days(date_start_dt.strftime('%Y-%m-%d %H:%M:%S'), 7, '%Y-%m-%d %H:%M:%S')
    
    response_dict, response_dict_employ = get_shifts_and_employees (date_start_iso, date_end_iso)
    
    
    full_text=f"Расписание на неделю {datetime.strptime(date_start_iso, '%Y-%m-%d %H:%M:%S').strftime('%d.%m')}-{datetime.strptime(date_end_iso, '%Y-%m-%d %H:%M:%S').strftime('%d.%m')}\n\n"

    for i in response_dict:
        name = ''
        for k in response_dict_employ:
            
            
            if k['id']==i["employee_id"]:
                name = k['full_name']

        if i["location"] is not None:
            text=f'{name}: {i["location"]["title"]} c {i["planned_from"]} до {i["planned_to"]}'
        else:
            continue      
        
        



    for i in clubs_color:
        full_text = full_text +f'{clubs_color[i]} {i}\n'
        for j in response_dict:
            name = ''
            for k in response_dict_employ:
            
            
                if k['id']==j["employee_id"]:
                    name = k['full_name']
            if name!="":
                text=f'{day_of_week(j["planned_from"]).capitalize()}: {name} c {add_hours(j["planned_from"],3)} до {add_hours(j["planned_to"],3)}\n'
            else:
                text=f'{day_of_week(j["planned_from"]).capitalize()}: СВОБОДНАЯ СМЕНА! c {add_hours(j["planned_from"],3)} до {add_hours(j["planned_to"],3)}\n'

            
             
            
            if j["location"] is not None:
                if (j["location"]["title"])==i:
                    full_text=full_text+text
            else:
                continue    
            
        full_text=full_text+'\n'        

        
    return full_text
    
def send_long_text(chat_id, text, bot):
    """Умная разбивка длинного сообщения, чтобы не ловить лимит Телеграма"""
    max_length = 4000
    if len(text) <= max_length:
        bot.send_message(chat_id, text)
        return
        
    parts = text.split('\n\n')
    msg = ""
    for part in parts:
        # Если добавление нового блока превысит лимит, отправляем то, что накопилось
        if len(msg) + len(part) + 2 > max_length:
            bot.send_message(chat_id, msg)
            msg = part + "\n\n"
        else:
            msg += part + "\n\n"
            
    # Отправляем остатки
    if msg.strip():
        bot.send_message(chat_id, msg)   
    
def handle_data(message, bot):
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
        import traceback
        traceback.print_exc()
        
        markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
        markup.add('Текущая неделя', 'Следующая неделя', 'Прошлая неделя', '⬅️ Вернуться')
        bot.send_message(message.chat.id, 'Попробуйте нажать кнопку или прислать дату в формате 15.04.2024:', reply_markup=markup)
        bot.register_next_step_handler(message, get_week, sched_type, bot)           
            
            
def update_schedule (date_user):

    date_start_dt = datetime.strptime(date_user, '%d.%m.%Y')
   
    date_start_iso = add_days(date_start_dt.strftime('%Y-%m-%d %H:%M:%S'), -7, '%Y-%m-%d %H:%M:%S')
    
    date_end_iso = add_days(date_start_dt.strftime('%Y-%m-%d %H:%M:%S'), 7, '%Y-%m-%d %H:%M:%S')

    days_between = (datetime.strptime(date_end_iso, "%Y-%m-%d %H:%M:%S") - datetime.strptime(date_start_iso, "%Y-%m-%d %H:%M:%S")).days
    
    response_dict, response_dict_employ = get_shifts_and_employees (date_start_iso, date_end_iso)
  


    schedule_list = []  # Заголовки

    for p in range(days_between):
        str_day = add_days(date_start_iso, p)

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
        
    update_schedule_table(schedule_list)    