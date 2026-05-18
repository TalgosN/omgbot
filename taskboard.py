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
        bot.send_message(message.chat.id,f'Напиши название (суть) {TEXTS["messtype_fill"][task_type]} (не более 20-ти симоволов) или если хочешь сменить тип обращения, нажми "Вернуться"', reply_markup=markup)
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
        bot.send_message(message.chat.id,f'Напиши название (суть) {TEXTS["messtype_fill"][task_type]} (не более 20-ти симоволов) или если хочешь сменить тип обращения, нажми "Вернуться"', reply_markup=markup)
        bot.send_message(message.chat.id, "Название не должно быть фотографией!")
        bot.register_next_step_handler(message, add_desc,task_type,club_task,bot)
        
    elif len(message.text)>20:

        markup = telebot.types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
        markup.add("Вернуться")
        bot.send_message(message.chat.id,f'Напиши название (суть) {TEXTS["messtype_fill"][task_type]} (не более 20-ти симоволов) или если хочешь сменить тип обращения, нажми "Вернуться"', reply_markup=markup)
        bot.send_message(message.chat.id, "Слишком длинное! Максимум 20 символов!")
        bot.register_next_step_handler(message, add_desc,task_type,club_task,bot)

    elif message.text.isnumeric():
        markup = telebot.types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
        markup.add("Вернуться")
        bot.send_message(message.chat.id,f'Напиши название (суть) {TEXTS["messtype_fill"][task_type]} (не более 20-ти симоволов) или если хочешь сменить тип обращения, нажми "Вернуться"', reply_markup=markup)
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

    if message.text=="Вернуться":

        returnback(message,bot)

    elif message.text=="Без фото":

        conn=sqlite3.connect('db/omgbot.sql')
        cur = conn.cursor() #INSERT INTO users (login, name) VALUES ('%s','%s')"
        data_tuple=(today,task_type,club_task,title,descrip,"В работе")
        cur.execute(""" INSERT INTO tasks (dtrep,type, club, title, desc,status) VALUES (?,?,?,?,?,?)""", data_tuple)
        conn.commit()
        cur.close()
        conn.close()




    elif message.photo:

        photo = message.photo[-1]
        file_info = bot.get_file(photo.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        save_path = f'data/photo/photo_{message.chat.id}.jpg'
        with open(save_path, 'wb') as new_file:
            new_file.write(downloaded_file)
        photo_add=convertToBinaryData(save_path)
        conn=sqlite3.connect('db/omgbot.sql')

        cur = conn.cursor() #cur.execute("INSERT INTO users (login, name) VALUES ('%s','%s')" % ("@"+message.from_user.username,user_name))
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

    # 2. Подготовка данных
    t_type_low = task_type.lower()
    clean_title = title.strip()

    clubs = get_clubs()
    club_tag = clubs[club_task]['tag']
    
    # 3. Логика определения тегов (убираем кучу if/else)
    mentions = ""
    if task_type == 'Ремонт':
        extra = extra_tags[task_type] if club_tag != '@RobinKruzo1' else ''
        mentions = f"{extra} {club_tag}"
        # Отправка в доп. чат для ремонта
        bot.send_message(CHATS['repair_extra'], f"@RobinKruzo1\n\n⚙️ Добавлена новая проблема-{t_type_low}: <b>{clean_title}</b>", parse_mode='html')
    
    elif task_type == 'Улучшение бота':
        mentions = extra_tags[task_type] 
    
    else:
        mentions = club_tag

    # 4. Универсальные уведомления
    # Пользователю
    bot.send_message(message.chat.id, f'Отлично, твоя проблема-{t_type_low} добавлена!')
    
    # В канал отчетов
    bot.send_message(CHATS['reports'], f'#задачи\n\n⚙️ Добавлена новая проблема-{t_type_low}: {clean_title} @OMGVR_Admin_Bot')
    
    # В основной рабочий чат (одна команда вместо трех разных веток)
    bot.send_message(CHATS['main_group'], f'{mentions}\n\n⚙️ Добавлена новая проблема-{t_type_low}: <b>{clean_title}</b>', parse_mode='html')

    returnback(message, bot)

###### show active

def show_active_tasks(message, bot):
    conn = sqlite3.connect('db/omgbot.sql')
    cur = conn.cursor()
    # Получаем задачи с указанием клуба
    cur.execute("SELECT id, title, club FROM tasks WHERE status='В работе'")
    tasks = cur.fetchall()
    cur.close()
    conn.close()

    # Создаем словарь для группировки задач по клубам
    tasks_by_club = {club: [] for club in clublist_task}
    for task_id, title, club in tasks:
        if club in tasks_by_club:
            tasks_by_club[club].append((task_id, title))

    list_buttons = []
    text_lines = []

    # Формируем текст с группировкой по клубам
    for club in clublist_task:
        club_tasks = tasks_by_club[club]
        if club_tasks:  # Если есть задачи для этого клуба
            text_lines.append(f"\n<b>{club}:</b>")
            for i, (task_id, title) in enumerate(club_tasks, 1):
                text_lines.append(f"{i}) {title}")
                list_buttons.append(types.InlineKeyboardButton(
                    f"{club[:3]}: {title[:15]}..." if len(title) > 15 else f"{club[:3]}: {title}",
                    callback_data=f'all_{task_id}'
                ))

    markup = telebot.types.InlineKeyboardMarkup()

    # Разбиваем кнопки на строки по col штук
    for i in range(len(list_buttons) // col):
        markup.row(*list_buttons[i * col:(i + 1) * col])

    if len(list_buttons) % col != 0:
        markup.row(*list_buttons[len(list_buttons) - len(list_buttons) % col:])

    markup.row(types.InlineKeyboardButton("Вернуться", callback_data="all_back"))

    text = "\n".join(text_lines) if text_lines else "Нет активных задач"

    bot.send_message(message.chat.id, f'Вот список текущих проблем:\n{text}',
                     reply_markup=markup, parse_mode='HTML')
    bot.send_message(message.chat.id,
                     'Выбери одну, чтобы посмотреть подробнее или нажми "Вернуться"',
                     reply_markup=types.ReplyKeyboardRemove())

###### show repairs

def show_active_type(message, bot, category):
    conn = sqlite3.connect('db/omgbot.sql')
    cur = conn.cursor()
    # Получаем задачи с указанием клуба
    cur.execute("SELECT id, title, club FROM tasks WHERE status='В работе' AND type=?", (category,))
    tasks = cur.fetchall()
    cur.close()
    conn.close()

    # Создаем словарь для группировки задач по клубам
    tasks_by_club = {club: [] for club in clublist_task}
    for task_id, title, club in tasks:
        if club in tasks_by_club:
            tasks_by_club[club].append((task_id, title))

    list_buttons = []
    text_lines = []
    task_counter = 1  # Общий счетчик задач

    # Формируем текст с группировкой по клубам
    for club in clublist_task:
        club_tasks = tasks_by_club[club]
        if club_tasks:  # Если есть задачи для этого клуба
            text_lines.append(f"\n<b>{club}:</b>")
            for task_id, title in club_tasks:
                text_lines.append(f"{task_counter}) {title}")
                list_buttons.append(types.InlineKeyboardButton(
                    f"{club[:3]}: {title[:15]}..." if len(title) > 15 else f"{club[:3]}: {title}",
                    callback_data=f'all_{task_id}'
                ))
                task_counter += 1

    markup = telebot.types.InlineKeyboardMarkup()

    # Разбиваем кнопки на строки по col штук
    for i in range(len(list_buttons) // col):
        markup.row(*list_buttons[i * col:(i + 1) * col])

    if len(list_buttons) % col != 0:
        markup.row(*list_buttons[len(list_buttons) - len(list_buttons) % col:])

    markup.row(types.InlineKeyboardButton("Вернуться", callback_data="all_back"))

    text = "\n".join(text_lines) if text_lines else "Нет активных задач по ремонту"

    bot.send_message(message.chat.id, f'Вот список текущих ремонтов:\n{text}',
                     reply_markup=markup, parse_mode='HTML')
    bot.send_message(message.chat.id,
                     'Выбери одну, чтобы посмотреть подробнее или нажми "Вернуться"',
                     reply_markup=types.ReplyKeyboardRemove())

def register_callback(bot):
    @bot.callback_query_handler(func=lambda call: call.data.startswith('all_'))
    def callback(call):
        try:
            # Убираем часики загрузки у кнопки
            bot.answer_callback_query(call.id) 
            
            data = call.data[4:]

            if data == "back":
                bot.clear_step_handler_by_chat_id(call.message.chat.id)
                bot.edit_message_reply_markup(call.message.chat.id, call.message.id, reply_markup=None)
                returnback(call.message, bot)
                return

            task_id = int(data)
            
            conn = sqlite3.connect('db/omgbot.sql')
            # МАГИЯ ЗДЕСЬ: Позволяет обращаться по именам колонок
            conn.row_factory = sqlite3.Row 
            cur = conn.cursor()
            
            # Используем безопасный запрос (?)
            cur.execute("SELECT * FROM tasks WHERE id=? AND status='В работе'", (task_id,))
            task = cur.fetchone()
            cur.close()
            conn.close()

            if not task:
                bot.send_message(call.message.chat.id, "⚠️ Эта задача уже не актуальна (удалена или закрыта).")
                return

            # Теперь берем данные БЕЗОПАСНО по именам колонок
            # (Имена должны совпадать с названиями в твоей базе данных!)
            dtrep = task['dtrep']      # Было task[1]
            tasktype = task['type']    # Было task[2]
            club_task = task['club']   # Было task[3]
            title = task['title']      # Было task[4]
            photo = task['photo']      # Было task[5]
            desc = task['desc']        # Было task[6]
            status = task['status']    # Было task[7]

            text = f"<b>{title}</b>\n\n<b>Тип:</b> {tasktype}\n\n<b>Клуб:</b> {club_task}\n\n<b>Описание:</b> {desc}\n\n<b>Статус:</b> {status}\n\n<b>Дата добавления:</b> {dtrep}"

            # Убираем кнопки у старого сообщения
            bot.edit_message_reply_markup(call.message.chat.id, call.message.id, reply_markup=None)

            if photo is not None:
                namephoto = f'data/photo/photo_downladed_{call.message.chat.id}.jpg'
                writeTofile(photo, namephoto)
                bot.send_photo(call.message.chat.id, photo=open(namephoto, 'rb'), caption=text, parse_mode='html')
            else:
                bot.send_message(call.message.chat.id, text, parse_mode='html')

            markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
            markup.add(*taskto)
            bot.send_message(call.message.chat.id, "Что вы хотите сделать с этим обращением?", reply_markup=markup)
            bot.register_next_step_handler(call.message, dotask, task_id, bot)

        except Exception as e:
            # Если что-то сломается, ты увидишь это прямо в боте
            bot.send_message(call.message.chat.id, f"🔥 Ошибка при открытии задачи: {e}")
            print(f"ERROR in taskboard: {e}") # И в консоли

def dotask(message,task,bot):

    conn=sqlite3.connect('db/omgbot.sql')
    cur = conn.cursor()
    cur.execute("SELECT status from users_new WHERE login='%s'"% ("@"+message.from_user.username))
    users = cur.fetchall()
    cur.close()
    conn.close()

    if message.text=='Выбрать другое':
        show_active_tasks(message,bot)
    
    elif message.text=='Обработать':

        if len(users)==0 or users[0][0]<1:

            bot.send_message(message.chat.id,"У вас недостаточно прав!")
            show_active_tasks(message,bot)

        else: #нужно сделать проверку на юезра

            markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
            markup.add("Вернуться")
            bot.send_message(message.chat.id,'Напишите ответ на запрос или нажмите "Вернуться"',reply_markup=markup)
            bot.register_next_step_handler(message,commit_task, task,bot)

    else:

        markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
        markup.add(*taskto)
        bot.send_message(message.chat.id,"Что вы хотите сделать с этим обращением?",reply_markup=markup)
        bot.register_next_step_handler(message, dotask,task,bot)



def commit_task(message,task,bot):

    answer=message.text

    if answer=="Вернуться":

        markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
        markup.add(*taskto)
        bot.send_message(message.chat.id,"Что вы хотите сделать с этим обращением?",reply_markup=markup)
        bot.register_next_step_handler(message, dotask,task,bot)

    elif len(answer)>1020:

        bot.send_message(message.chat.id,"Слишком длинное!")
        markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
        markup.add("Вернуться")
        bot.send_message(message.chat.id,"Напишите ответ на запрос или нажмите 'Вернуться'",reply_markup=markup)
        bot.register_next_step_handler(message,commit_task, task,bot)

    else:

        markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
        markup.add("Да","Нет")
        bot.send_message(message.chat.id,"Изменить статус проблемы?",reply_markup=markup)
        bot.register_next_step_handler(message,change_task, task,answer,bot)

def change_task(message,task,answer,bot):

    if message.text=='Нет':

        markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
        markup.add("Вернуться")
        bot.send_message(message.chat.id,"Напишите ответ на запрос или нажмите 'Вернуться'",reply_markup=markup)
        bot.register_next_step_handler(message,commit_task, task,bot)

    elif message.text=='Да':

        today=datetime.today().strftime('%Y-%m-%d')
        conn=sqlite3.connect('db/omgbot.sql')
        cur = conn.cursor()
        cur.execute("UPDATE tasks SET status = 'Выполнено', dtfb = '%s', feedback='%s'  WHERE id = '%s'" % (today,answer,task))
        conn.commit()
        cur.close()
        conn.close()
        bot.send_message(message.chat.id,"Проблема решена!",reply_markup=types.ReplyKeyboardRemove())

        conn = sqlite3.connect('db/omgbot.sql')
        cur = conn.cursor()
        cur.execute("SELECT title FROM tasks WHERE id=?", (task,))
        task = cur.fetchone()
        task = task[0]
        cur.close()
        conn.close()
        
        bot.send_message(CHATS['main_group'],f'К задаче "{task}" было добавлено решение!\n\n{answer}')
        show_active_tasks(message,bot)

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
            dtfb = task['dtfb'] if 'dtfb' in task.keys() else 'Не указано'
            feedback = task['feedback'] if 'feedback' in task.keys() else 'Нет ответа'

            text = f"<b>{title}</b>\n\n<b>Тип:</b> {tasktype}\n\n<b>Клуб:</b> {club_task}\n\n<b>Описание:</b> {desc}\n\n<b>Статус:</b> {status}\n\n<b>Дата добавления:</b> {dtrep}\n\n<b>Ответ:</b> {feedback}\n\n<b>Дата ответа:</b> {dtfb}"

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
