import sqlite3
import pytz
from telebot import *
#from constants import *
from constants import get_clubs, funclist_today, CHATS, TEXTS, tags_main, clublist
from sheets import *
from datetime import datetime,timedelta


CLUBS=get_clubs()
############################# core openclose

def func_today (message,bot):
    markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add(*funclist_today)
    bot.send_message(message.chat.id, 'О, так ты на смене? Что хочешь сделать?', reply_markup=markup)
    bot.register_next_step_handler(message, func_today_2,bot)

def func_today_2 (message,bot):
    a = message.text
    if a== '✅ Открыть смену' or a == '🚫 Закрыть смену':
        check_club(message,a,bot)

    elif a == '🚩 Репорт':

        markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
        markup.add('⬅️ Вернуться')
        bot.send_message (message.chat.id, 'Чем бы ты хотел поделиться?', reply_markup=markup)
        bot.register_next_step_handler(message, do_report, bot)

    elif a=='⬅️ Вернуться':
        from main import hello
        hello (message.chat.id,bot)

    else:
        markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
        markup.add(*funclist_today)
        bot.send_message(message.chat.id, 'О, так ты на смене? Что хочешь сделать?', reply_markup=markup)
        bot.register_next_step_handler(message, func_today_2,bot)
        
def do_report(message,bot):
    
    
    from main import define_name
    users=define_name(message)

    if message.photo:
        if message.text==None:
            text = ""
        else:
            text = f'\n\n{message.text}'
        photo1 = [types.InputMediaPhoto(message.photo[0].file_id, caption=f"🔺 {users[0][4]} (@{message.from_user.username}) репортит!{text}" )]
        
        bot.send_media_group(CHATS['reports'], media=(photo1))

        func_today(message,bot)

    elif message.text == '⬅️ Вернуться':
        func_today(message,bot)

    else:

        text= message.text
        text_to_send = f'🔺 {users[0][4]} (@{message.from_user.username}) репортит!\n\n{text}'
        bot.send_message (CHATS['reports'], text_to_send)
        func_today(message,bot)
    
    
def check_club(message, a, bot):
    markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add(*clublist, "Вернуться")
    bot.send_message(message.chat.id, 'Какой клуб?', reply_markup=markup)
    bot.register_next_step_handler(message, check_club_status, a, False, bot)

def check_club_status(message, a, tooearly, bot):
    club = message.text
    
    if club == "Вернуться":
        func_today(message, bot)
        return
    
    if club not in clublist:
        bot.send_message(message.chat.id, "Извините, такого клуба у нас нет")
        check_club(message, a, bot)
        return
    
    # Проверка статуса клуба
    conn = sqlite3.connect('db/omgbot.sql')
    cur = conn.cursor()
    cur.execute("SELECT status FROM clubs WHERE club='%s'"%(club))
    result = cur.fetchone()
    cur.close()
    conn.close()
    
    status = result[0] if result else None
   
    if a == '✅ Открыть смену' and status == 'Открыт':
        bot.send_message(message.chat.id, f'Дружок, ты что-то перепутал! {club} уже открыт!')
        check_club(message, a, bot)
        return
    elif a == '🚫 Закрыть смену' and status == 'Закрыт':
        bot.send_message(message.chat.id, f'Дружок, ты что-то перепутал! {club} уже закрыт!')
        check_club(message, a, bot)
        return
    
    if a == '✅ Открыть смену':
        enter_club(message, a, club, tooearly, bot)
    else:
        is_early(message, a, club, bot)  # Передаем club как параметр

def is_early(message, a, club, bot):
    # Берем конфиг из нашего нового JSON
    conf = CLUBS[club]['schedule']
    now = datetime.now(pytz.timezone('Europe/Moscow'))
    
    # Парсим время "21:45:00" в объект time
    limit_t = datetime.strptime(conf['early_check_time'], "%H:%M:%S").time()
    # Создаем datetime на сегодня с этим временем
    limit_dt = now.replace(hour=limit_t.hour, minute=limit_t.minute, second=0, microsecond=0)

    # Если сейчас раннее утро (до 5 утра), значит дедлайн был вчера вечером
    if now.hour < 5:
        limit_dt -= timedelta(days=1)

    # Считаем разницу
    late = (limit_dt - now).total_seconds()

    if late > 0:
        markup = telebot.types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
        markup.add(*TEXTS['ui']['answer_options'])
        bot.send_message(message.chat.id, 'Что-то ты рановато! Ты уверен, что хочешь закрыть смену?', reply_markup=markup)
        bot.register_next_step_handler(message, closeconfirm, a, club, bot)
    else:
        enter_club(message, a, club, False, bot)

def closeconfirm(message, a, club, bot):
    if message.text == TEXTS['ui']['answer_options'][0]:
        enter_club(message, a, club, True, bot)
    else:
        func_today(message, bot)

def enter_club(message, a, club, tooearly, bot):
    # 1. Обработка кнопки возврата
    if club == "⬅️ Вернуться" or club == "Вернуться":
        func_today(message, bot)
        return

    # 2. Проверка наличия
    if club not in clublist:
        bot.send_message(message.chat.id, "Извините, такого клуба у нас нет")
        check_club(message, a, bot)
        return

    # 3. Развилка логики
    if a == '🚫 Закрыть смену':
        # Если закрываем — сразу идем в подтверждение, пропускаем глупый вопрос
        confirm_enter(message, a, club, tooearly, bot)
    else:
        # Если открываем — спрашиваем подтверждение
        markup = telebot.types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
        markup.add('Да, я на месте!', 'Нет, я ещё не зашёл...')
        bot.send_message(message.chat.id, f"Ты точно на рабочем месте ({club})? Только не ври!", reply_markup=markup)
        bot.register_next_step_handler(message, confirm_enter, a, club, tooearly, bot)


def confirm_enter(message, a, club, tooearly, bot):
    # Условие: Либо юзер нажал "Да", ЛИБО это закрытие смены (тогда кнопка не нужна)
    if message.text == 'Да, я на месте!' or a == '🚫 Закрыть смену':
        
        # 1. Обновляем статус в БД
        new_status = 'Открыт' if a == '✅ Открыть смену' else 'Закрыт'
        conn = sqlite3.connect('db/omgbot.sql')
        cur = conn.cursor()
        cur.execute("UPDATE clubs SET status = ? WHERE club = ?", (new_status, club))
        conn.commit()
        cur.close()
        conn.close()
        
        # 2. Подготовка данных
        current_datetime = datetime.now(pytz.timezone('Europe/Moscow')).strftime("%H:%M")
        
        # Чек-лист
        checklist = CLUBS[club].get('checklists', {}).get(a, [])
        check_list_text = "\n– " + "\n– ".join(checklist) if checklist else " Отсутствует"

        # Выбор варианта вопросов
        q_variants = CLUBS[club]['questions'].get(a, [[]])
        variant_index = datetime.now().weekday() % len(q_variants)
        questions = q_variants[variant_index]
        
        # 3. Приветствие
        try:
            action_idx = funclist_today.index(a)
            ui_text = TEXTS['ui']['login_logout'][action_idx]
            readiness_text = TEXTS['ui']['readiness'][action_idx]
        except:
            ui_text = "Действие"
            readiness_text = "готовности"

        response = (f"{ui_text} {club} в {current_datetime}\n"
                    f"Самое время отправить отчет {readiness_text}!\n\n"
                    f"Чек лист:{check_list_text}")
        
        bot.send_message(message.chat.id, response, reply_markup=types.ReplyKeyboardRemove())
        
        # 4. Лог в репорты
        from main import define_name
        users = define_name(message)
        name = users[0][4] if users else "Сотрудник"
        
        log_msg = "зашёл в" if a == '✅ Открыть смену' else "начинает закрывать"
        bot.send_message(CHATS['reports'], f"⚠️ {name} {log_msg} {club} в {current_datetime}")

        # 5. ЗАПУСК ОПРОСА
        # Важно: передаем expected_type=None, так как это первый шаг и проверять пока нечего
        run_step(message, bot, a, club, questions, [], [], current_datetime, tooearly, expected_type=None, current_q_text=None)
        
    else:
        check_club(message, a, bot)

def run_step(message, bot, a, club, remaining_questions, answers, photos, start_time, tooearly, expected_type=None, current_q_text=None):
    # 0. ГЛАВНАЯ ПРОВЕРКА: Хочет ли юзер вернуться?
    if message.text == "Вернуться" or message.text == "⬅️ Вернуться":
        from main import hello
        bot.send_message(message.chat.id, "Отмена операции. Возвращаемся в меню.")
        hello(message.chat.id, bot)
        return

    # 1. ВАЛИДАЦИЯ ПРЕДЫДУЩЕГО ВОПРОСА
    if expected_type:
        # Валидация фото
        if expected_type == "photo" and not message.photo:
            bot.send_message(message.chat.id, "Стой! Здесь нужно именно фото 📸 (или нажми 'Вернуться')")
            # Передаем current_q_text обратно, чтобы не потерять контекст
            return bot.register_next_step_handler(message, run_step, bot, a, club, remaining_questions, answers, photos, start_time, tooearly, expected_type, current_q_text)
        
        # Валидация числа
        if expected_type == "num" and (not message.text or not message.text.isnumeric()):
            bot.send_message(message.chat.id, "Нужно ввести число (только цифры) 🔢")
            return bot.register_next_step_handler(message, run_step, bot, a, club, remaining_questions, answers, photos, start_time, tooearly, expected_type, current_q_text)

        # --- СОХРАНЕНИЕ ДАННЫХ (ИЗМЕНЕНИЯ ЗДЕСЬ) ---
        if message.photo:
            photos.append(types.InputMediaPhoto(message.photo[-1].file_id))
        elif message.text:
            # Берем первые 2 слова из вопроса
            if current_q_text:
                label = " ".join(current_q_text.split()[:2])
            else:
                label = "Ответ"
            
            # Сохраняем как КОРТЕЖ (Метка, Значение), чтобы не сломать БД
            answers.append((label, message.text))

    # 2. ПРОВЕРКА: Если вопросы закончились
    if not remaining_questions:
        finish_report(message, bot, a, club, answers, photos, start_time, tooearly)
        return

    # 3. ЗАДАЕМ СЛЕДУЮЩИЙ ВОПРОС
    current_q_data = remaining_questions[0]
    next_expected_type = current_q_data['type']
    next_q_text = current_q_data['text'] # Запоминаем текст вопроса

    markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("Вернуться")
    
    bot.send_message(message.chat.id, next_q_text, reply_markup=markup)
    
    # 4. РЕГИСТРИРУЕМ СЛЕДУЮЩИЙ ШАГ
    # Передаем next_q_text в следующий вызов
    bot.register_next_step_handler(message, run_step, bot, a, club, 
                                   remaining_questions[1:], answers, photos, start_time, tooearly, 
                                   next_expected_type, next_q_text)

def finish_report(message, bot, a, club, answers, photos, start_time, tooearly):
    from main import define_name, hello
    
    # 1. Инициализация данных
    users = define_name(message)
    name = users[0][4]
    
    # Текущее время (когда закончил заполнять)
    tz = pytz.timezone('Europe/Moscow')
    now = datetime.now(tz)
    today_db = now.strftime('%Y-%m-%d %H:%M:%S')

    # --- ИСПРАВЛЕННЫЙ РАСЧЕТ ОПОЗДАНИЯ ---
    diff_minutes = 0
    if a == '✅ Открыть смену':
        try:
            # 1. start_time это просто "HH:MM" (например, "10:05")
            # Нам нужно добавить к нему сегодняшнюю дату, чтобы считать разницу
            current_date = now.date()
            start_t = datetime.strptime(start_time, "%H:%M").time()
            start_dt = datetime.combine(current_date, start_t) # Получили полноценный datetime
            
            # 2. Определяем целевое время из JSON
            sched = CLUBS[club]['schedule']['open_strict']
            
            # Проверяем день недели (0-4 будни, 5-6 выходные)
            is_weekend = start_dt.weekday() >= 5 
            target_str = sched['weekend'] if is_weekend else sched['weekdays']
            
            # 3. Собираем целевой datetime (сегодня + время из расписания)
            target_time = datetime.strptime(target_str, "%H:%M:%S").time()
            target_dt = datetime.combine(current_date, target_time)
            
            # 4. Считаем разницу в секундах
            diff_sec = (start_dt - target_dt).total_seconds()
            diff_minutes = int(diff_sec / 60) # Переводим в минуты
            
        except Exception as e:
            print(f"Ошибка расчета времени: {e}")
            diff_minutes = 0

    # 2. Формируем текст отчета
    answers_text = "\nОтветы на вопросы:\n" + "\n".join([f"— {ans[0]}: {ans[1]}" for ans in answers]) if answers else ""
    
    report_caption = (
        f"📍 Клуб: {club}\n"
        f"👤 Сотрудник: {name} (@{message.from_user.username})\n"
        f"📝 Действие: {a}\n"
        f"⏰ Время начала: {start_time}\n"
        f"✅ Завершено: {now.strftime('%H:%M')}"
        f"{answers_text}"
    )

    # 3. Отправка в КАНАЛ ОТЧЕТОВ (CHATS['reports'])
    try:
        if photos:
            photos[0].caption = report_caption
            bot.send_media_group(CHATS['reports'], media=photos)
        else:
            bot.send_message(CHATS['reports'], report_caption)
            
        # Уведомление о раннем закрытии
        if tooearly and a == '🚫 Закрыть смену':
            bot.send_message(CHATS['reports'], f"⚠️ Внимание! Раннее закрытие!\n{tags_main}")

        # Уведомление об опоздании (для админов)
        if a == '✅ Открыть смену' and diff_minutes > 5:
             bot.send_message(CHATS['reports'], f"😡 Внимание! ОПОЗДАНИЕ на {diff_minutes} мин!\n{tags_main}")

    except Exception as e:
        bot.send_message(message.chat.id, f"Ошибка при отправке отчета: {e}")

    # 4. Уведомление в ОБЩУЮ ГРУППУ (CHATS['main_group']) + ЛОГИКА ШТРАФОВ
    penalty_text = ""
    msg_type = 'good_morning' # Дефолтное значение
    
    if a == '✅ Открыть смену':
        if diff_minutes > 5:
            # Опоздал больше чем на 5 минут
            msg_type = 'penalty_phrases'
            penalty_text = f'🚨 ШТРАФ (опоздание {diff_minutes} мин)! 🚨\n' 
        else:
            # Пришел вовремя
            msg_type = 'good_morning'    
    else:
        # Закрытие смены
        msg_type = 'good_night'
    
    # Безопасное получение фраз (если вдруг в TEXTS нет penalty_phrases)
    phrases = TEXTS.get(msg_type, ["Смена открыта/закрыта.", "Хорошего отдыха!"])
    
    # Собираем итоговое сообщение
    compliment = f"{penalty_text}{random.choice(phrases)}"
    
    bot.send_message(
        CHATS['main_group'], 
        f"{name} {a.lower().replace('ть', 'л')} в {club} в {now.strftime('%H:%M')}! {compliment}"
    )

    # 5. Запись в БД
    try:
        conn = sqlite3.connect('db/omgbot.sql')
        cur = conn.cursor()
        
        cur.execute(
            "INSERT INTO activity (dtrep, login, club, action) VALUES (?, ?, ?, ?)",
            (today_db, f"@{message.from_user.username}", club, a)
        )

        # Запись нала: берем answers[0][1] — это само значение (число)
        if answers and (a == '✅ Открыть смену' or a == '🚫 Закрыть смену'):
             # answers[0] теперь выглядит как ('Касса', '1000')
             # Нам нужен второй элемент -> answers[0][1]
             if str(answers[0][1]).isdigit(): 
                try:
                    cur.execute(
                        "INSERT INTO nal (drep, club, amount) VALUES (?, ?, ?)",
                        (today_db, club, answers[0][1])
                    )
                except Exception as e:
                    print(f"Ошибка записи нала: {e}")

        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Ошибка БД: {e}")

    # 6. Финал
    bot.send_message(message.chat.id, "Отчет успешно принят! Спасибо за работу. 😎")
    hello(message.chat.id, bot)

############################# common functions

##### openclose

# schedule module

def send_status_close(club,bot):
   conn=sqlite3.connect('db/omgbot.sql')
   cur = conn.cursor()
   cur.execute("SELECT * FROM clubs WHERE status='Открыт' and club='%s'"%(club))
   clubs = cur.fetchall()
   cur.close()
   conn.close()
   if len(clubs)!=0:
    	bot.send_message(CHATS['reports'], f'Не прислан отчет о закрытии: {club}') #CHATS['reports']
        

def send_status_open(club,bot):
        conn=sqlite3.connect('db/omgbot.sql')
        cur = conn.cursor()
        cur.execute("SELECT * FROM clubs WHERE status='Закрыт' and club='%s'"%(club))
        clubs = cur.fetchall()
        cur.close()
        conn.close()
        if len(clubs)!=0:
            bot.send_message(CHATS['main_group'], f'{tags_main}\n{club} еще не открыт! Кажется кто-то огребет 😉') #CHATS['main_group']

def close_club (club,bot):
   conn=sqlite3.connect('db/omgbot.sql')
   cur = conn.cursor()
   cur.execute("UPDATE clubs SET status='Закрыт' WHERE status='Открыт' and club='%s'"%(club))
   rows_affected = cur.rowcount  # Количество измененных строк
   conn.commit()
   cur.close()
   conn.close()

   # Отправляем сообщение только если были изменения (rows_affected > 0)
   if rows_affected > 0:
       bot.send_message(CHATS['main_group'], f'Закрыл {club} за тебя. Да-да, я к тебе обращаюсь 🙄')