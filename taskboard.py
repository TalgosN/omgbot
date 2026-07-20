from telebot import *
from constants import *
import sqlite3

##### taskdesk

def convertToBinaryData(filename):
    # Convert digital data to binary format
    with open(filename, 'rb') as file:
        blobData = file.read()
    return blobData

def writeTofile(data, filename):
    # Convert binary data to proper format and write it on Hard Disk
    with open(filename, 'wb') as file:
        file.write(data)

   
   
def task_board(message,bot):
    
    bot.send_message(message.chat.id, f'Это полностью анонимная доска, где ты можешь сообщить менеджеру о проблеме в клубе, предложить улучшение или просто узнать мнение руководства о чем либо, а также посмотреть запросы от других!')
    markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add(*funclist_task)
    bot.send_message(message.chat.id, 'Что вы хотите сделать? 👀', reply_markup=markup)
    bot.register_next_step_handler(message, func_task,bot)

def func_task(message,bot):
    if message.text=='➕ Добавить':
        bot.send_message(message.chat.id, f'Здесь ты можешь добавить свой стикер на доску')
        add_task(message,bot)

    elif message.text=='⭕ Текущие':
        show_active_tasks(message,bot)

    elif message.text=='🛠 Ремонт':
        show_active_type(message,bot, 'Ремонт')

    elif message.text=='🤖 Улучшения бота':
        show_active_type(message,bot,'Улучшение бота')

    elif message.text=='✔ Выполненные':
        show_done_tasks(message,0,bot)
    
    else:
        markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
        markup.add(*funclist_task)
        bot.send_message(message.chat.id, 'Что вы хотите сделать? 👀', reply_markup=markup)
        bot.register_next_step_handler(message, func_task,bot)

###### add

def add_task(message,bot):
        conn=sqlite3.connect('db/omgbot.sql')
        cur = conn.cursor()
        cur.execute("SELECT title FROM tasks WHERE status='%s'" % ("В работе"))
        titles = cur.fetchall()
        cur.close()
        conn.close()
        '''if len(titles)>=64:
            bot.send_message(message.chat.id, f'Слишком много задач, добавление временно недоступно')
            returnback(message,bot)'''
   
        markup=types.ReplyKeyboardMarkup(row_width=len(messtype), resize_keyboard=True)
        markup.add(*messtype,"Вернуться")
        bot.send_message(message.chat.id, f'Выбери тип обращения или нажми "Вернуться"',reply_markup=markup)
        bot.register_next_step_handler(message, add_task_type,bot)

def returnback(message,bot):
        from menu import hello
        hello (message.chat.id,bot)

def add_task_type(message,bot):
    if message.text=="Вернуться":
        returnback(message,bot)
    elif message.text in messtype:
        task_type=message.text

        club_task(message,task_type,bot)
    else:
        bot.send_message(message.chat.id, "Извините, такого у нас нет!")
        add_task(message,bot)

def club_task(message,task_type,bot):
     
     markup=types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
     markup.add(*clublist_task,"Вернуться")
     bot.send_message(message.chat.id, f'К какому клубу относится твое обращение?',reply_markup=markup)
     bot.register_next_step_handler(message, add_title, task_type,bot)

def add_title(message,task_type,bot):

    if message.text=="Вернуться":
        returnback(message,bot)

    elif message.text in clublist_task:

        club_task=message.text
        markup = telebot.types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
        markup.add("Вернуться")
        bot.send_message(message.chat.id, TEXTS['messtype_dict'][task_type])
        bot.send_message(message.chat.id,f'Напиши название (суть) {TEXTS["messtype_fill"][task_type]} (не более 50-ти симоволов) или если хочешь сменить тип обращения, нажми "Вернуться"', reply_markup=markup)
        bot.register_next_step_handler(message, add_desc,task_type,club_task,bot)

    else:

        markup=types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
        markup.add(*clublist_task,"Вернуться")
        bot.send_message(message.chat.id, f'К какому клубу относится твое обращение?',reply_markup=markup)
        bot.register_next_step_handler(message, add_title, task_type,bot)


def add_desc(message,task_type,club_task,bot):

    if message.text=="Вернуться":

        returnback(message,bot)
    
    elif message.photo:
        markup = telebot.types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
        markup.add("Вернуться")
        bot.send_message(message.chat.id,f'Напиши название (суть) {TEXTS["messtype_fill"][task_type]} (не более 50-ти симоволов) или если хочешь сменить тип обращения, нажми "Вернуться"', reply_markup=markup)
        bot.send_message(message.chat.id, "Название не должно быть фотографией!")
        bot.register_next_step_handler(message, add_desc,task_type,club_task,bot)
        
    elif len(message.text)>50:

        markup = telebot.types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
        markup.add("Вернуться")
        bot.send_message(message.chat.id,f'Напиши название (суть) {TEXTS["messtype_fill"][task_type]} (не более 50-ти симоволов) или если хочешь сменить тип обращения, нажми "Вернуться"', reply_markup=markup)
        bot.send_message(message.chat.id, "Слишком длинное! Максимум 50 символов!")
        bot.register_next_step_handler(message, add_desc,task_type,club_task,bot)

    elif message.text.isnumeric():
        markup = telebot.types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
        markup.add("Вернуться")
        bot.send_message(message.chat.id,f'Напиши название (суть) {TEXTS["messtype_fill"][task_type]} (не более 50-ти симоволов) или если хочешь сменить тип обращения, нажми "Вернуться"', reply_markup=markup)
        bot.send_message(message.chat.id, "Название проблемы не должно состоять только из числа!")
        bot.register_next_step_handler(message, add_desc,task_type,club_task,bot)
        

    else:

        title = message.text
        markup = telebot.types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
        markup.add("Вернуться")
        bot.send_message(message.chat.id,f'Напиши описание {TEXTS["messtype_fill"][task_type]} (не более 1000-ти симоволов) или если хочешь сменить тип обращения, нажми "Вернуться"', reply_markup=markup)
        bot.register_next_step_handler(message, add_photo,task_type,title,club_task,bot)

def add_photo(message, task_type,title,club_task,bot):
    
    if message.text=="Вернуться":

        returnback(message,bot)
    
    elif message.photo:
        markup = telebot.types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
        markup.add("Вернуться")
        bot.send_message(message.chat.id,f'Напиши описание {TEXTS["messtype_fill"][task_type]} (не более 1000-ти симоволов) или если хочешь сменить тип обращения, нажми "Вернуться"', reply_markup=markup)
        bot.send_message(message.chat.id, "Описание не должно быть фотографией! На следующем этапе ты сможешь добавить фото, сейчас напиши только текст.")
        bot.register_next_step_handler(message, add_photo,task_type,title,club_task,bot)
    
    elif len(message.text)>1020:

        markup = telebot.types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
        markup.add("Вернуться")
        bot.send_message(message.chat.id,f'Напиши описание {TEXTS["messtype_fill"][task_type]} (не более 1000-ти симоволов) или если хочешь сменить тип обращения, нажми "Вернуться"', reply_markup=markup)
        bot.send_message(message.chat.id, "Слишком длинное! Максимум 1000 символов!")
        bot.register_next_step_handler(message, add_photo,task_type,title,club_task,bot)
    
    else:

        descrip=message.text
        markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
        markup.add("Без фото","Вернуться")
        bot.send_message(message.chat.id,f'Прикрепи фото, или, если его нет, нажми "Без фото". Если хочешь сменить тип обращения, нажми "Вернуться"', reply_markup=markup)
        bot.register_next_step_handler(message, send_task,task_type,title,descrip,club_task,bot)


def send_task(message,task_type,title, descrip,club_task,bot):

    today=datetime.today().strftime('%Y-%m-%d')
    photo_id_to_send = None # Инициализируем переменную для фото

    if message.text=="Вернуться":
        returnback(message,bot)
        return # <-- ИСПРАВЛЕНИЕ: прерываем выполнение, чтобы не отправлялась пустая задача

    elif message.text=="Без фото":
        conn=sqlite3.connect('db/omgbot.sql')
        cur = conn.cursor() 
        data_tuple=(today,task_type,club_task,title,descrip,"В работе")
        cur.execute(""" INSERT INTO tasks (dtrep,type, club, title, desc,status) VALUES (?,?,?,?,?,?)""", data_tuple)
        conn.commit()
        cur.close()
        conn.close()

    elif message.photo:
        photo = message.photo[-1]
        photo_id_to_send = photo.file_id # Сохраняем ID фото для моментальной пересылки в чат
        
        file_info = bot.get_file(photo.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        save_path = f'data/photo/photo_{message.chat.id}.jpg'
        with open(save_path, 'wb') as new_file:
            new_file.write(downloaded_file)
        photo_add=convertToBinaryData(save_path)
        
        conn=sqlite3.connect('db/omgbot.sql')
        cur = conn.cursor() 
        data_tuple=(today,task_type,club_task,title, photo_add,descrip,"В работе")
        cur.execute(""" INSERT INTO tasks (dtrep,type,club, title, photo, desc,status) VALUES (?,?,?,?,?,?,?)""", data_tuple)
        conn.commit()
        cur.close()
        conn.close()

    else:
        markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
        markup.add("Без фото","Вернуться")
        bot.send_message(message.chat.id,f'Прикрепи фото, или, если его нет, нажми "Без фото". Если хочешь сменить тип обращения, нажми "Вернуться"', reply_markup=markup)
        bot.register_next_step_handler(message, send_task,task_type,title,descrip,club_task,bot)
        return # <-- ИСПРАВЛЕНИЕ: прерываем выполнение, ждем фото

    # 2. Подготовка данных
    t_type_low = task_type.lower()
    clean_title = title.strip()
    short_desc = (descrip[:800] + '...') if len(descrip) > 800 else descrip 

    clubs = get_clubs()
    club_tag = clubs[club_task]['tag']
    
    # ПОЛНЫЙ ТЕКСТ (для канала отчетов)
    notification_full = (
        f"⚙️ Добавлена новая проблема-{t_type_low}:\n"
        f"<b>{clean_title}</b>\n\n"
        f"📝 <b>Описание:</b>\n{short_desc}"
    )
    
    # КОРОТКИЙ ТЕКСТ (для рабочих чатов, как было раньше)
    notification_short = f"⚙️ Добавлена новая проблема-{t_type_low}: <b>{clean_title}</b>"
    
    # 3. Логика определения тегов
    mentions = ""
    if task_type == 'Ремонт':
        extra = extra_tags[task_type] if club_tag != '@RobinKruzo1' else ''
        mentions = f"{extra} {club_tag}"
        # Отправка в доп. чат для ремонта (КОРОТКО)
        bot.send_message(CHATS['repair_extra'], f"@RobinKruzo1\n\n{notification_short}", parse_mode='html')
    
    elif task_type == 'Улучшение бота':
        mentions = extra_tags[task_type] 
    
    else:
        mentions = club_tag

    # 4. Универсальные уведомления
    bot.send_message(message.chat.id, f'Отлично, твоя проблема-{t_type_low} добавлена!')
    
    # В КАНАЛ ОТЧЕТОВ — Полная версия (с фото, если оно есть)
    if photo_id_to_send:
        bot.send_photo(CHATS['reports'], photo=photo_id_to_send, caption=f"#задачи\n\n{notification_full} @OMGVR_Admin_Bot", parse_mode='html')
    else:
        bot.send_message(CHATS['reports'], f"#задачи\n\n{notification_full} @OMGVR_Admin_Bot", parse_mode='html')

    # В РАБОЧИЙ ЧАТ — Короткая версия (всегда текстом, без фото)
    bot.send_message(CHATS['main_group'], f"{mentions}\n\n{notification_short}", parse_mode='html')

    returnback(message, bot)

###### show active

def show_active_tasks(message, bot):
    conn = sqlite3.connect('db/omgbot.sql')
    cur = conn.cursor()
    cur.execute("SELECT id, title, club, status FROM tasks WHERE status IN ('В работе', 'На проверке')")
    tasks = cur.fetchall()
    cur.close()
    conn.close()

    tasks_by_club = {club: [] for club in clublist_task}
    for task_id, title, club, status in tasks:
        if club in tasks_by_club:
            tasks_by_club[club].append((task_id, title, status))

    list_buttons = []
    text_lines = []

    for club in clublist_task:
        club_tasks = tasks_by_club[club]
        if club_tasks: 
            text_lines.append(f"\n<b>{club}:</b>")
            for i, (task_id, title, status) in enumerate(club_tasks, 1):
                # Убрали иконку ремонта, оставили только глаза
                prefix = "👀 " if status == 'На проверке' else ""
                text_lines.append(f"{i}) {prefix}{title}")
                
                short_title = title[:12] + "..." if len(title) > 12 else title
                list_buttons.append(types.InlineKeyboardButton(
                    f"{prefix}{club[:3]}: {short_title}",
                    callback_data=f'all_{task_id}'
                ))

    markup = telebot.types.InlineKeyboardMarkup()
    for i in range(len(list_buttons) // col):
        markup.row(*list_buttons[i * col:(i + 1) * col])
    if len(list_buttons) % col != 0:
        markup.row(*list_buttons[len(list_buttons) - len(list_buttons) % col:])

    markup.row(types.InlineKeyboardButton("Вернуться", callback_data="all_back"))
    text = "\n".join(text_lines) if text_lines else "Нет активных задач"

    bot.send_message(message.chat.id, f'Вот список текущих проблем:\n{text}', reply_markup=markup, parse_mode='HTML')
    bot.send_message(message.chat.id, 'Выбери одну, чтобы посмотреть подробнее или нажми "Вернуться"', reply_markup=types.ReplyKeyboardRemove())

###### show repairs

def show_active_type(message, bot, category):
    conn = sqlite3.connect('db/omgbot.sql')
    cur = conn.cursor()
    cur.execute("SELECT id, title, club, status FROM tasks WHERE status IN ('В работе', 'На проверке') AND type=?", (category,))
    tasks = cur.fetchall()
    cur.close()
    conn.close()

    tasks_by_club = {club: [] for club in clublist_task}
    for task_id, title, club, status in tasks:
        if club in tasks_by_club:
            tasks_by_club[club].append((task_id, title, status))

    list_buttons = []
    text_lines = []
    task_counter = 1 

    for club in clublist_task:
        club_tasks = tasks_by_club[club]
        if club_tasks: 
            text_lines.append(f"\n<b>{club}:</b>")
            for task_id, title, status in club_tasks:
                prefix = "👀 " if status == 'На проверке' else ""
                text_lines.append(f"{task_counter}) {prefix}{title}")
                
                short_title = title[:12] + "..." if len(title) > 12 else title
                list_buttons.append(types.InlineKeyboardButton(
                    f"{prefix}{club[:3]}: {short_title}",
                    callback_data=f'all_{task_id}'
                ))
                task_counter += 1

    markup = telebot.types.InlineKeyboardMarkup()
    for i in range(len(list_buttons) // col):
        markup.row(*list_buttons[i * col:(i + 1) * col])
    if len(list_buttons) % col != 0:
        markup.row(*list_buttons[len(list_buttons) - len(list_buttons) % col:])

    markup.row(types.InlineKeyboardButton("Вернуться", callback_data="all_back"))
    text = "\n".join(text_lines) if text_lines else f"Нет активных задач по типу: {category}"

    bot.send_message(message.chat.id, f'Вот список текущих ремонтов:\n{text}', reply_markup=markup, parse_mode='HTML')
    bot.send_message(message.chat.id, 'Выбери одну, чтобы посмотреть подробнее или нажми "Вернуться"', reply_markup=types.ReplyKeyboardRemove())


def dotask(message, task_id, current_status, bot):
    conn=sqlite3.connect('db/omgbot.sql')
    cur = conn.cursor()
    cur.execute("SELECT status from users_new WHERE login=?", ("@"+message.from_user.username,))
    users = cur.fetchall()
    cur.close()
    conn.close()

    if message.text == 'Выбрать другое':
        show_active_tasks(message, bot)

    elif current_status == 'В работе' and message.text == 'Обработать':
        if len(users) == 0 or users[0][0] < 1:
            bot.send_message(message.chat.id, "У вас недостаточно прав!")
            show_active_tasks(message, bot)
        else:
            markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
            markup.add("Вернуться")
            bot.send_message(message.chat.id, 'Напишите решение по проблеме (оно уйдет сотрудникам на проверку):', reply_markup=markup)
            bot.register_next_step_handler(message, commit_task, task_id, bot)

    elif current_status == 'На проверке':
        if message.text == '✅ Подтвердить решение':
            today = datetime.today().strftime('%Y-%m-%d')
            conn = sqlite3.connect('db/omgbot.sql')
            cur = conn.cursor()
            cur.execute("UPDATE tasks SET status = 'Выполнено', dtfb = ? WHERE id = ?", (today, task_id))
            conn.commit()
            cur.close()
            conn.close()
            bot.send_message(message.chat.id, "✅ Спасибо! Проблема окончательно закрыта и перенесена в архив.", reply_markup=types.ReplyKeyboardRemove())
            show_active_tasks(message, bot)

        elif message.text == '❌ Вернуть в работу':
            markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
            markup.add("Вернуться")
            bot.send_message(message.chat.id, "Опишите, почему решение не помогло или что осталось неисправным:", reply_markup=markup)
            bot.register_next_step_handler(message, return_task_to_work, task_id, bot)
        else:
            show_active_tasks(message, bot)
    else:
        show_active_tasks(message, bot)


def commit_task(message, task_id, bot):
    answer = message.text
    if answer == "Вернуться":
        show_active_tasks(message, bot)
    elif len(answer) > 1020:
        bot.send_message(message.chat.id, "Слишком длинное! Напиши короче.")
        bot.register_next_step_handler(message, commit_task, task_id, bot)
    else:
        markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
        markup.add("Да", "Нет")
        bot.send_message(message.chat.id, "Отправить решение и перевести задачу в статус «На проверке»?", reply_markup=markup)
        bot.register_next_step_handler(message, change_task, task_id, answer, bot)


# -------------------------------------------------------------
# ОБНОВЛЕННЫЕ ФУНКЦИИ ИТЕРАЦИЙ И УВЕДОМЛЕНИЙ
# -------------------------------------------------------------

def change_task(message, task_id, answer, bot):
    if message.text == 'Нет':
        show_active_tasks(message, bot)
    elif message.text == 'Да':
        today_short = datetime.today().strftime('%d.%m')
        
        conn = sqlite3.connect('db/omgbot.sql')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT feedback, title, type, club FROM tasks WHERE id=?", (task_id,))
        task = cur.fetchone()
        
        old_feedback = task['feedback'] if task['feedback'] else ""
        title = task['title']
        task_type = task['type']
        club_task = task['club']
        
        # Добавляем новый ответ админа к истории
        new_entry = f"<b>[{today_short}] Админ:</b> {answer}"
        new_feedback = f"{old_feedback}\n\n{new_entry}".strip()

        cur.execute("UPDATE tasks SET status = 'На проверке', feedback = ? WHERE id = ?", (new_feedback, task_id))
        conn.commit()
        cur.close()
        conn.close()

        bot.send_message(message.chat.id, "✅ Решение отправлено! Задача ожидает подтверждения.", reply_markup=types.ReplyKeyboardRemove())
        
        # --- ФОРМИРОВАНИЕ УВЕДОМЛЕНИЙ ---
        clubs = get_clubs()
        club_tag = clubs[club_task]['tag']
        t_type_low = task_type.lower()
        
        mentions = ""
        if task_type == 'Ремонт':
            extra = extra_tags[task_type] if club_tag != '@RobinKruzo1' else ''
            mentions = f"{extra} {club_tag}"
        elif task_type == 'Улучшение бота':
            mentions = extra_tags[task_type] 
        else:
            mentions = club_tag

        msg_full = (f"👀 <b>Решение по проблеме-{t_type_low}:</b>\n{title}\n\n"
                    f"💬 <b>Ответ:</b>\n{answer}")
                    
        msg_short = (f"{mentions}\n\n👀 <b>Решение по проблеме-{t_type_low}:</b> {title}\n"
                     f"💬 <i>{answer}</i>\n\n"
                     f"👉 <b>Проверьте и подтвердите выполнение на доске задач!</b>")

        bot.send_message(CHATS['reports'], f"#задачи\n\n{msg_full}\n\n@OMGVR_Admin_Bot", parse_mode='HTML')
        bot.send_message(CHATS['main_group'], msg_short, parse_mode='HTML')
        
        if task_type == 'Ремонт':
            bot.send_message(CHATS['repair_extra'], f"@RobinKruzo1\n\n👀 <b>Решение по проблеме-{t_type_low}:</b> {title}\n💬 <i>{answer}</i>", parse_mode='HTML')
            
        show_active_tasks(message, bot)


def return_task_to_work(message, task_id, bot):
    if message.text == "Вернуться":
        show_active_tasks(message, bot)
        return

    reason = message.text
    today_short = datetime.today().strftime('%d.%m')

    conn = sqlite3.connect('db/omgbot.sql')
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT feedback, title, type, club FROM tasks WHERE id=?", (task_id,))
    task = cur.fetchone()
    
    old_feedback = task['feedback'] if task['feedback'] else ""
    title = task['title']
    task_type = task['type']
    club_task = task['club']

    # Добавляем комментарий сотрудника к истории
    new_entry = f"<b>[{today_short}] Сотрудник:</b> {reason}"
    new_feedback = f"{old_feedback}\n\n{new_entry}".strip()

    cur.execute("UPDATE tasks SET status = 'В работе', feedback = ? WHERE id = ?", (new_feedback, task_id))
    conn.commit()
    cur.close()
    conn.close()

    bot.send_message(message.chat.id, "❌ Задача возвращена в работу. Админы увидят ваш комментарий.", reply_markup=types.ReplyKeyboardRemove())
    
    # --- ФОРМИРОВАНИЕ УВЕДОМЛЕНИЙ ---
    clubs = get_clubs()
    club_tag = clubs[club_task]['tag']
    t_type_low = task_type.lower()
    
    mentions = ""
    if task_type == 'Ремонт':
        extra = extra_tags[task_type] if club_tag != '@RobinKruzo1' else ''
        mentions = f"{extra} {club_tag}"
    elif task_type == 'Улучшение бота':
        mentions = extra_tags[task_type] 
    else:
        mentions = club_tag

    msg_full = (f"⚠️ <b>Проблема-{t_type_low} возвращена в работу:</b>\n{title}\n\n"
                f"💬 <b>Причина возврата:</b>\n{reason}")
                
    msg_short = (f"{mentions}\n\n⚠️ <b>Проблема-{t_type_low} возвращена в работу:</b> {title}\n"
                 f"💬 <i>{reason}</i>")

    bot.send_message(CHATS['reports'], f"#задачи\n\n{msg_full}\n\n@OMGVR_Admin_Bot", parse_mode='HTML')
    bot.send_message(CHATS['main_group'], msg_short, parse_mode='HTML')
    
    if task_type == 'Ремонт':
        bot.send_message(CHATS['repair_extra'], f"@RobinKruzo1\n\n⚠️ <b>Проблема возвращена в работу:</b> {title}\n💬 <i>{reason}</i>", parse_mode='HTML')
        
    show_active_tasks(message, bot)
    
##### done tasks

    
def show_done_tasks(message, page, bot):
    conn = sqlite3.connect('db/omgbot.sql')
    cur = conn.cursor()
    # Выбираем и id, и title задач
    cur.execute("SELECT id, title FROM tasks WHERE status='Выполнено'")
    tasks = cur.fetchall()
    cur.close()
    conn.close()
    
    max_pages = (len(tasks)-1) // 30
    list_title = []
    list_buttons = []
    
    markup = telebot.types.InlineKeyboardMarkup()
    
    if max_pages == 0:
        # Все задачи на одной странице
        for i, (task_id, title) in enumerate(tasks):
            list_title.append(f'{i+1}) {title}')
            # Используем ID задачи в callback_data
            list_buttons.append(types.InlineKeyboardButton(title, callback_data=f'don_{task_id}'))

        # Разбиваем кнопки на строки
        for i in range(len(tasks) // col):
            markup.row(*list_buttons[i*col:(i+1)*col])

        if len(tasks) % col != 0:
            markup.row(*list_buttons[len(tasks)-len(tasks)%col:])
            
        markup.row(types.InlineKeyboardButton("Вернуться", callback_data="don_back"))  
        
    else:
        # Разбивка на страницы
        if page == 0:
            # Первая страница
            start, end = 0, 30
        elif page == max_pages:
            # Последняя страница
            start, end = page*30, len(tasks)
        else:
            # Промежуточные страницы
            start, end = page*30, (page+1)*30

        for i in range(start, end):
            task_id, title = tasks[i]
            list_title.append(f'{i+1}) {title}')
            list_buttons.append(types.InlineKeyboardButton(title, callback_data=f'don_{task_id}'))

        # Разбиваем кнопки на строки
        for i in range((end - start) // col):
            markup.row(*list_buttons[i*col:(i+1)*col])

        if (end - start) % col != 0:
            markup.row(*list_buttons[(end - start) - (end - start)%col:])
        
        # Добавляем навигацию
        nav_buttons = []
        if page > 0:
            nav_buttons.append(types.InlineKeyboardButton(f"{page} ⬅️", callback_data=f"don_page_{page-1}"))
        
        nav_buttons.append(types.InlineKeyboardButton("Вернуться", callback_data="don_back"))
        
        if page < max_pages:
            nav_buttons.append(types.InlineKeyboardButton(f"{page+2} ➡️", callback_data=f"don_page_{page+1}"))
            
        markup.row(*nav_buttons)
    
    text = "\n".join(list_title)
    bot.send_message(message.chat.id, f'Вот список выполненных проблем:\n\n{text}', reply_markup=markup)
    bot.send_message(message.chat.id, 'Выбери одну, чтобы посмотреть подробнее или нажми "Вернуться"', reply_markup=types.ReplyKeyboardRemove())

def register_callback(bot):
    @bot.callback_query_handler(func=lambda call: call.data.startswith('all_'))
    def callback(call):
        try:
            bot.answer_callback_query(call.id) 
            data = call.data[4:]

            if data == "back":
                bot.clear_step_handler_by_chat_id(call.message.chat.id)
                bot.edit_message_reply_markup(call.message.chat.id, call.message.id, reply_markup=None)
                returnback(call.message, bot)
                return

            task_id = int(data)
            conn = sqlite3.connect('db/omgbot.sql')
            conn.row_factory = sqlite3.Row 
            cur = conn.cursor()
            
            cur.execute("SELECT * FROM tasks WHERE id=? AND status IN ('В работе', 'На проверке')", (task_id,))
            task = cur.fetchone()
            cur.close()
            conn.close()

            if not task:
                bot.send_message(call.message.chat.id, "⚠️ Эта задача уже не актуальна (перенесена в архив).")
                return

            dtrep = task['dtrep']      
            tasktype = task['type']    
            club_task = task['club']   
            title = task['title']      
            photo = task['photo']      
            desc = task['desc']        
            status = task['status']    
            feedback = task['feedback'] if task['feedback'] else 'Ожидает решения...'

            # Форматируем красивую историю переписки
            text = f"<b>{title}</b>\n\n<b>Тип:</b> {tasktype}\n<b>Клуб:</b> {club_task}\n\n<b>Описание:</b> {desc}\n\n<b>Статус:</b> {status}\n<b>Дата:</b> {dtrep}\n\n💬 <b>История решения:</b>\n{feedback}"

            bot.edit_message_reply_markup(call.message.chat.id, call.message.id, reply_markup=None)

            if photo is not None:
                namephoto = f'data/photo/photo_downladed_{call.message.chat.id}.jpg'
                writeTofile(photo, namephoto)
                bot.send_photo(call.message.chat.id, photo=open(namephoto, 'rb'), caption=text, parse_mode='html')
            else:
                bot.send_message(call.message.chat.id, text, parse_mode='html')

            markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
            
            # Развилка логики кнопок в зависимости от статуса
            if status == 'На проверке':
                markup.add('✅ Подтвердить решение', '❌ Вернуть в работу')
            else:
                markup.add('Обработать') # Кнопка для админов
                
            markup.add('Выбрать другое')
            bot.send_message(call.message.chat.id, "Что вы хотите сделать с этим обращением?", reply_markup=markup)
            
            # Пробрасываем текущий статус на следующий шаг
            bot.register_next_step_handler(call.message, dotask, task_id, status, bot)

        except Exception as e:
            bot.send_message(call.message.chat.id, f"🔥 Ошибка при открытии задачи: {e}")

def register_callback2(bot):
    @bot.callback_query_handler(func=lambda call: call.data.startswith('don_'))
    def callback2(call):
        try:
            bot.answer_callback_query(call.id)
            data = call.data[4:]

            if data == "back":
                bot.clear_step_handler_by_chat_id(call.message.chat.id)
                bot.edit_message_reply_markup(call.message.chat.id, call.message.id, reply_markup=None)
                returnback(call.message, bot)
                return
            
            elif data.startswith("page_"):
                page = int(data[5:])
                show_done_tasks(call.message, page, bot)
                return

            # Если это открытие задачи
            bot.edit_message_reply_markup(call.message.chat.id, call.message.id, reply_markup=None)
            
            task_id = int(data)
            conn = sqlite3.connect('db/omgbot.sql')
            conn.row_factory = sqlite3.Row # <-- Важно!
            cur = conn.cursor()
            cur.execute("SELECT * FROM tasks WHERE id=?", (task_id,))
            task = cur.fetchone()
            cur.close()
            conn.close()

            if not task:
                bot.send_message(call.message.chat.id, "⚠️ Задача не найдена.")
                return

            # Безопасное получение данных
            dtrep = task['dtrep']
            tasktype = task['type']
            club_task = task['club']
            title = task['title']
            photo = task['photo']
            desc = task['desc']
            status = task['status']
            # Проверь, есть ли эти колонки в выполненных задачах, иногда они NULL
            dtfb = task['dtfb'] if task['dtfb'] else 'Не указано'
            feedback = task['feedback'] if task['feedback'] else 'История пуста'

            text = f"<b>{title}</b>\n\n<b>Тип:</b> {tasktype}\n<b>Клуб:</b> {club_task}\n\n<b>Описание:</b> {desc}\n\n<b>Статус:</b> {status}\n<b>Дата закрытия:</b> {dtfb}\n\n💬 <b>История решения:</b>\n{feedback}"
            if photo is not None:
                namephoto = f'data/photo/photo_downladed_{call.message.chat.id}.jpg'
                writeTofile(photo, namephoto)
                bot.send_photo(call.message.chat.id, photo=open(namephoto, 'rb'), caption=text, parse_mode='html')
            else:
                bot.send_message(call.message.chat.id, text, parse_mode='html')

            markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
            markup.add("Вернуться")
            bot.send_message(call.message.chat.id, "Выберете другое обращение или нажмите 'Вернуться'", reply_markup=markup)
            bot.register_next_step_handler(call.message, ret, bot)

        except Exception as e:
            bot.send_message(call.message.chat.id, f"🔥 Ошибка: {e}")

def ret (message,bot):

    if message.text=="Вернуться":

        show_done_tasks(message,0,bot)

    else:

        markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
        markup.add("Вернуться")
        bot.send_message(message.chat.id,"Выберете другое обращение или нажмите 'Вернуться'",reply_markup=markup)
        bot.register_next_step_handler(message, ret,bot)
