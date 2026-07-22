from telebot import *
from constants import *
from sheets import *
import requests
import json
import os
from datetime import datetime, timedelta
import math
import locale
import sqlite3
import threading
import pytz
from weather import get_weather
from permissions import ROLE_EMPLOYEE, require_role

locale.setlocale(locale.LC_ALL, 'ru_RU.UTF-8')

emojis = ['💀', '🤖', '🍓', '😎', '🤓', '🙄', '👽', '👻', '😈', '😇', '😅', '🤑', '😉', '🐯', '🌝', '🌚', '🥟']

shifton_chat_sync_lock = threading.Lock()
shifton_notifications_lock = threading.Lock()
shifton_runtime_status = {
    "last_notification_check": None,
    "last_notification_sent": None,
    "last_notification_error": None,
    "last_chat_sync": None,
    "last_chat_sync_result": None
}

def moscow_timestamp():
    return datetime.now(pytz.timezone('Europe/Moscow')).strftime('%Y-%m-%d %H:%M:%S')

funclist_rasp=('📄 Расписание на сегодня','📑 Расписание на неделю', '⬅️ Вернуться')
funclist_rasp_week=('👨🏻‍💻 По сотрудникам','🗓 По датам', '🔴 По клубам','⬅️ Вернуться')

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def fetch_schedule_from_api(date_iso):
    """Делает GET запрос к новому API на конкретную дату (YYYY-MM-DD)"""
    url = f"{SHIFTON_API_URL}/api/bot/schedule?date={date_iso}"
    headers = {"Authorization": f"Bearer {SHIFTON_API_TOKEN}"}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        return response.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}

def register_shifton_chat(telegram_tag, chat_id):
    """Привязывает личный Telegram-чат сотрудника к его карточке в OMG Shift."""
    url = f"{SHIFTON_API_URL}/api/bot/register-chat"
    headers = {
        "Authorization": f"Bearer {SHIFTON_API_TOKEN}",
        "Content-Type": "application/json"
    }
    try:
        response = requests.post(
            url,
            json={"telegram": telegram_tag, "chatId": chat_id},
            headers=headers,
            timeout=10
        )
        return response.json()
    except Exception as e:
        return {"ok": False, "error": "request_failed", "details": str(e)}

def sync_shifton_notification_chats():
    """Передаёт в OMG Shift Telegram chatid всех действующих сотрудников."""
    try:
        conn = sqlite3.connect('db/omgbot.sql')
        cur = conn.cursor()
        cur.execute("""
            SELECT login, chatid
            FROM users
            WHERE COALESCE(status, 0) <> -1
              AND login IS NOT NULL AND login <> ''
              AND chatid IS NOT NULL AND chatid <> ''
        """)
        users = cur.fetchall()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Ошибка чтения чатов для OMG Shift: {e}")
        return

    synced = 0
    errors = 0
    identity_changed = False
    for login, chat_id in users:
        telegram_tag = login if str(login).startswith('@') else f"@{login}"
        try:
            chat_id = int(chat_id)
        except (TypeError, ValueError):
            errors += 1
            print(f"Некорректный chatid для {telegram_tag}: {chat_id}")
            continue

        result = register_shifton_chat(telegram_tag, chat_id)
        if result.get("ok"):
            synced += 1
            try:
                from account import apply_omg_identity
                identity = apply_omg_identity(chat_id, telegram_tag, result.get("employee"))
                identity_changed = identity_changed or identity["changed"]
            except Exception as e:
                errors += 1
                print(f"Ошибка синхронизации ФИО OMG Shift для {telegram_tag}: {e}")
        else:
            errors += 1
            print(f"Ошибка регистрации чата OMG Shift для {telegram_tag}: {result.get('error', 'unknown_error')}")

    if identity_changed:
        from account import sync_google_dependencies
        google_errors = sync_google_dependencies(full=True)
        errors += len(google_errors)
        for error in google_errors:
            print(f"Ошибка синхронизации профиля с Google Sheets: {error}")

    print(f"Синхронизация чатов OMG Shift завершена: {synced} успешно, {errors} ошибок")
    shifton_runtime_status["last_chat_sync"] = moscow_timestamp()
    shifton_runtime_status["last_chat_sync_result"] = f"{synced} успешно, {errors} ошибок"

def start_shifton_chat_sync():
    """Запускает синхронизацию чатов в фоне, не блокируя общий планировщик."""
    if not shifton_chat_sync_lock.acquire(blocking=False):
        return

    def worker():
        try:
            sync_shifton_notification_chats()
        finally:
            shifton_chat_sync_lock.release()

    threading.Thread(target=worker, daemon=True).start()

def claim_shifton_notification():
    """Забирает одно ожидающее уведомление об изменении расписания."""
    url = f"{SHIFTON_API_URL}/api/bot/notifications/claim"
    headers = {"Authorization": f"Bearer {SHIFTON_API_TOKEN}"}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        return response.json()
    except Exception as e:
        return {"ok": False, "error": "request_failed", "details": str(e)}

def complete_shifton_notification(notification_id, success, error=""):
    """Сообщает OMG Shift результат отправки уведомления в Telegram."""
    url = f"{SHIFTON_API_URL}/api/bot/notifications/complete"
    headers = {
        "Authorization": f"Bearer {SHIFTON_API_TOKEN}",
        "Content-Type": "application/json"
    }
    try:
        response = requests.post(
            url,
            json={"id": notification_id, "success": bool(success), "error": error},
            headers=headers,
            timeout=10
        )
        return response.json()
    except Exception as e:
        return {"ok": False, "error": "request_failed", "details": str(e)}

def send_pending_shifton_notifications(bot, limit=10):
    """Отправляет ожидающие уведомления OMG Shift сотрудникам."""
    for _ in range(limit):
        result = claim_shifton_notification()
        shifton_runtime_status["last_notification_check"] = moscow_timestamp()
        if not result.get("ok"):
            error = result.get('error', 'unknown_error')
            shifton_runtime_status["last_notification_error"] = error
            print(f"Ошибка получения уведомлений OMG Shift: {error}")
            return

        shifton_runtime_status["last_notification_error"] = None
        notification = result.get("notification")
        if not notification:
            return

        notification_id = notification.get("id")
        try:
            bot.send_message(notification.get("chatId"), notification.get("text"))
            complete_shifton_notification(notification_id, True)
            shifton_runtime_status["last_notification_sent"] = moscow_timestamp()
            print(f"Уведомление OMG Shift отправлено: {notification_id}")
        except Exception as e:
            complete_shifton_notification(notification_id, False, str(e))
            shifton_runtime_status["last_notification_error"] = str(e)
            print(f"Ошибка отправки уведомления OMG Shift {notification_id}: {e}")

def get_shifton_runtime_status():
    return dict(shifton_runtime_status)

def start_shifton_notifications_check(bot):
    """Запускает обработку очереди в фоне и не допускает параллельных проверок."""
    if not shifton_notifications_lock.acquire(blocking=False):
        return

    def worker():
        try:
            send_pending_shifton_notifications(bot)
        finally:
            shifton_notifications_lock.release()

    threading.Thread(target=worker, daemon=True).start()

def calculate_duration(start_time_str, end_time_str):
    """Вычисляет длительность в часах из строк вида '09:30' и '20:00'"""
    try:
        t1 = datetime.strptime(start_time_str, "%H:%M")
        t2 = datetime.strptime(end_time_str, "%H:%M")
        if t2 < t1:
            t2 += timedelta(days=1) # Переход через полночь
        duration = t2 - t1
        return round(math.fabs(duration.total_seconds() / 3600), 1)
    except:
        return 0

def last_monday(datetime_str):
    dt = datetime.strptime(datetime_str, '%Y-%m-%d %H:%M:%S')
    days_since_monday = dt.weekday()
    last_monday_date = dt - timedelta(days=days_since_monday)
    last_monday_date = last_monday_date.replace(hour=0, minute=0, second=0)
    return last_monday_date

# --- ОСНОВНАЯ ЛОГИКА БОТА ---

def rasp(message, bot):
    if not require_role(message, bot, ROLE_EMPLOYEE):
        return
    bot.send_message(message.chat.id, f'Этот раздел посвящен расписанию и всё что с ним связано!')
    markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add(*funclist_rasp)
    bot.send_message(message.chat.id, 'Что вы хотите сделать? 👀', reply_markup=markup)
    bot.register_next_step_handler(message, func_rasp, bot)

def func_rasp(message, bot):
    if not require_role(message, bot, ROLE_EMPLOYEE):
        return
    if message.text == '📄 Расписание на сегодня':
        try:
            today_date = datetime.now(pytz.timezone('Europe/Moscow'))
            today_text = get_today_schedule(today_date.strftime("%Y-%m-%d"))
            bot.send_message(message.chat.id, today_text)
            
            markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
            markup.add(*funclist_rasp)
            bot.send_message(message.chat.id, 'Что вы хотите сделать? 👀', reply_markup=markup)
            bot.register_next_step_handler(message, func_rasp, bot)
        except Exception as e:
            bot.send_message(message.chat.id, 'Что-то пошло не так! Перешлите ошибку ниже техническому специалисту')
            bot.send_message(message.chat.id, str(e))

    elif message.text == '📑 Расписание на неделю':
        markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
        markup.add(*funclist_rasp_week)
        bot.send_message(message.chat.id, f'Выбери в каком формате ты хочешь получить расписание', reply_markup=markup)
        bot.register_next_step_handler(message, handle_data, bot)
        
    elif message.text == '⬅️ Вернуться':
        returnback(message, bot)
    else:
        markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
        markup.add(*funclist_rasp)
        bot.send_message(message.chat.id, 'Что вы хотите сделать? 👀', reply_markup=markup)
        bot.register_next_step_handler(message, func_rasp, bot)

def returnback(message, bot):
    from menu import hello
    hello(message.chat.id, bot)

def get_today_schedule(date_iso):
    """Получение расписания на сегодня (1 запрос)"""
    data = fetch_schedule_from_api(date_iso)
    
    today = datetime.now(pytz.timezone('Europe/Moscow')).strftime('%d.%m.%Y')
    weekday = datetime.now(pytz.timezone('Europe/Moscow')).strftime('%A')

    full_text = f'{today}, {weekday.capitalize()}\n\n'
    full_text += f'{get_weather()}\n\n'

    if not data.get("ok"):
        return full_text + f"⚠️ Ошибка получения расписания: {data.get('error', 'API недоступен')}"

    locations = data.get("locations", [])
    
    for club in get_schedule_locations():
        full_text += f'{club["emoji"]} {club["name"]}\n'
        
        # Ищем локацию в ответе API
        loc_data = next(
            (loc for loc in locations if loc.get("title") == club['source_name']),
            None,
        )
        
        if loc_data and loc_data.get("shifts"):
            for shift in loc_data["shifts"]:
                name = shift.get("employee", "СВОБОДНАЯ СМЕНА")
                tg = shift.get("telegram", "")
                
                display_name = f"{name} ({tg})" if tg else name
                start, end = shift.get("start"), shift.get("end")
                
                full_text += f'{display_name} c {start} до {end}\n'
        
        full_text += '\n'

    return full_text

# --- ЛОГИКА НЕДЕЛИ ---

def get_week_data(start_dt):
    """Универсальная функция: делает 7 запросов и собирает все смены недели в удобный список"""
    week_shifts = []
    
    for p in range(7):
        current_dt = start_dt + timedelta(days=p)
        date_iso = current_dt.strftime('%Y-%m-%d')
        
        data = fetch_schedule_from_api(date_iso)
        if not data.get("ok"): continue
        
        for loc in data.get("locations", []):
            loc_title = loc.get("title", "Неизвестно")
            for shift in loc.get("shifts", []):
                week_shifts.append({
                    "date_dt": current_dt,
                    "day_str": current_dt.strftime('%d.%m, %A').capitalize(),
                    "location": loc_title,
                    "employee": shift.get("employee", "СВОБОДНАЯ СМЕНА"),
                    "start": shift.get("start", ""),
                    "end": shift.get("end", "")
                })
    return week_shifts

def get_week_by_club(date_user):
    date_start_dt = datetime.strptime(date_user, '%d.%m.%Y')
    start_dt = last_monday(date_start_dt.strftime('%Y-%m-%d 00:00:00'))
    
    week_shifts = get_week_data(start_dt)
    
    full_text = f"🗓 <b>Расписание на неделю {start_dt.strftime('%d.%m')} - {(start_dt + timedelta(days=6)).strftime('%d.%m')}</b>\n\n"

    for club in get_schedule_locations():
        club_shifts = [
            shift for shift in week_shifts
            if shift["location"] == club['source_name']
        ]
        if not club_shifts: continue
            
        full_text += f'{club["emoji"]} <b>{club["name"]}</b>\n'
        
        shifts_by_day = {}
        for s in club_shifts:
            day_str = s["day_str"]
            if day_str not in shifts_by_day:
                shifts_by_day[day_str] = []
                
            time_str = f'с {s["start"]} до {s["end"]}'
            shifts_by_day[day_str].append(f'  └ {s["employee"]} {time_str}')
            
        for day, shifts in shifts_by_day.items():
            full_text += f'📅 {day}:\n'
            full_text += "\n".join(shifts) + "\n"
        full_text += '\n'        
        
    return full_text

def get_week_by_day(date_user):
    date_start_dt = datetime.strptime(date_user, '%d.%m.%Y')
    start_dt = last_monday(date_start_dt.strftime('%Y-%m-%d 00:00:00'))
    
    week_shifts = get_week_data(start_dt)
    
    full_text = f"🗓 <b>Расписание на неделю {start_dt.strftime('%d.%m')} - {(start_dt + timedelta(days=6)).strftime('%d.%m')}</b>\n\n"

    for p in range(7):
        current_dt = start_dt + timedelta(days=p)
        day_str = current_dt.strftime('%d.%m, %A').capitalize()
        
        day_has_shifts = False
        day_text = f"📅 <b>{day_str}</b>\n"
        
        for club in get_schedule_locations():
            club_shifts = [
                shift for shift in week_shifts
                if shift["location"] == club['source_name']
                and shift["date_dt"].date() == current_dt.date()
            ]
            if not club_shifts: continue
                
            day_has_shifts = True
            day_text += f' {club["emoji"]} {club["name"]}:\n'
            for s in club_shifts:
                time_str = f'с {s["start"]} до {s["end"]}'
                day_text += f'  └ {s["employee"]} {time_str}\n'
        
        if day_has_shifts:
            full_text += f"{day_text}\n"

    return full_text

def get_week_by_employee(date_user):
    date_start_dt = datetime.strptime(date_user, '%d.%m.%Y')
    start_dt = last_monday(date_start_dt.strftime('%Y-%m-%d 00:00:00'))
    
    week_shifts = get_week_data(start_dt)
    schedule_locations = {
        club['source_name']: club for club in get_schedule_locations()
    }
    
    full_text = f"🗓 <b>Расписание на неделю {start_dt.strftime('%d.%m')} - {(start_dt + timedelta(days=6)).strftime('%d.%m')}</b>\n\n"

    shifts_by_emp = {}
    for s in week_shifts:
        name = s["employee"]
        if name not in shifts_by_emp:
            shifts_by_emp[name] = {}
        
        day_str = s["day_str"]
        loc = s["location"]
        club = schedule_locations.get(loc)
        loc_color = club['emoji'] if club else ''
        loc_name = club['name'] if club else loc
        time_str = f'с {s["start"]} до {s["end"]}'
        
        if day_str not in shifts_by_emp[name]:
            shifts_by_emp[name][day_str] = []
            
        shifts_by_emp[name][day_str].append(f'  └ {time_str} {loc_color} {loc_name}')

    for emp, days_dict in shifts_by_emp.items():
        icon = "👤" if emp == "СВОБОДНАЯ СМЕНА" else random.choice(emojis)
        full_text += f'{icon} <b>{emp}</b>\n'
        for day_str, shifts in days_dict.items():
            full_text += f'📅 {day_str}:\n'
            full_text += "\n".join(shifts) + "\n"
        full_text += '\n'

    return full_text

# --- МАРШРУТИЗАЦИЯ --- (Остается практически без изменений, просто вырезал для экономии места, логика handle_data, get_week и send_long_text остается старой)

# --- МАРШРУТИЗАЦИЯ ---

def send_long_text(chat_id, text, bot):
    """Умная разбивка длинного сообщения с поддержкой HTML"""
    max_length = 4000
    if len(text) <= max_length:
        bot.send_message(chat_id, text, parse_mode='HTML')
        return
        
    parts = text.split('\n\n')
    msg = ""
    for part in parts:
        if len(msg) + len(part) + 2 > max_length:
            bot.send_message(chat_id, msg, parse_mode='HTML')
            msg = part + "\n\n"
        else:
            msg += part + "\n\n"
            
    if msg.strip():
        bot.send_message(chat_id, msg, parse_mode='HTML')

def handle_data(message, bot):
    if not require_role(message, bot, ROLE_EMPLOYEE):
        return
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
    if not require_role(message, bot, ROLE_EMPLOYEE):
        return
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
        
        # Обновляем БД (опционально, если хочешь чтобы база тоже заполнялась)
        try:
            update_schedule(user_date)
        except Exception as e:
            print(f"Ошибка обновления базы расписания: {e}")
        
        # Возвращаем меню
        markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
        markup.add(*funclist_rasp)
        bot.send_message(message.chat.id, 'Что вы хотите сделать дальше? 👀', reply_markup=markup)
        bot.register_next_step_handler(message, func_rasp, bot)
    
    except Exception as e:
        bot.send_message(message.chat.id, f'❌ Ошибка: {e}\nПерешлите её техническому специалисту.')
        
        markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
        markup.add('Текущая неделя', 'Следующая неделя', 'Прошлая неделя', '⬅️ Вернуться')
        bot.send_message(message.chat.id, 'Попробуйте нажать кнопку или прислать дату в формате 15.04.2024:', reply_markup=markup)
        bot.register_next_step_handler(message, get_week, sched_type, bot)
        
# --- ИНТЕГРАЦИЯ В БАЗУ ДАННЫХ (ТАБЛИЦЫ) ---

def update_schedule(date_user):
    """
    Теперь эта функция запрашивает только ту неделю (7 дней), 
    к которой относится переданная дата.
    """
    date_start_dt = datetime.strptime(date_user, '%d.%m.%Y')
    start_dt = last_monday(date_start_dt.strftime('%Y-%m-%d 00:00:00'))
    
    # Делаем 7 запросов
    week_shifts = get_week_data(start_dt)
    schedule_list = []  

    for s in week_shifts:
        if s["employee"] != "СВОБОДНАЯ СМЕНА":
            str_day = s["date_dt"].strftime('%d.%m.%Y')
            duration = calculate_duration(s["start"], s["end"])
            
            # Формат: [name, str_day, shift_start, shift_end, location_title, duration_in_hours]
            schedule_list.append([
                s["employee"], 
                str_day, 
                s["start"], 
                s["end"], 
                s["location"], 
                duration
            ])
            
    # Отправляем в Google Sheets
    if schedule_list:
        update_schedule_table(schedule_list)
