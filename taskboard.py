from telebot import *
from constants import *
from html import escape, unescape
import io
import re
import sqlite3
from datetime import datetime, timedelta
import pytz
from permissions import (
    ROLE_EMPLOYEE,
    ROLE_MANAGER,
    ROLE_TECHNICIAN,
    get_user,
    require_role,
    role_of,
)
from task_notifications import (
    BOT_TASK_TYPE,
    GENERAL_TASK_TYPE,
    REPAIR_TASK_TYPE,
    created_task_notification,
    progress_task_notification,
)
from task_analytics import (
    record_task_event,
    system_task_actor,
    task_actor_snapshot,
)

TASK_DB_PATH = 'db/omgbot.sql'
TASK_REVIEW_DAYS = 14
READONLY_TASK_PAGE_SIZE = 30
READONLY_TASK_STATUSES = {
    'work': ('В работе',),
    'review': ('На проверке',),
    'done': ('Выполнено', 'Архив'),
}
def _task_mentions(task_type, club):
    club_tag = str(get_clubs().get(club, {}).get('tag') or '').strip()
    if task_type == REPAIR_TASK_TYPE:
        repair_tag = extra_tags.get(task_type, '')
        return ' '.join(
            value for value in (
                repair_tag if club_tag != repair_tag else '', club_tag,
            ) if value
        )
    if task_type == BOT_TASK_TYPE:
        return extra_tags.get(task_type, '')
    return club_tag


def _send_task_notification(
    bot, event, task_type, club, title, description='', message='',
    actor=None, photo_id=None,
):
    if event == 'created':
        full, short, _confirmation = created_task_notification(
            task_type, club, title, description, actor=actor,
        )
    else:
        full, short = progress_task_notification(
            event, task_type, club, title, message, actor=actor,
        )
    report_text = f"#задачи\n\n{full}\n\n@OMGVR_Admin_Bot"
    if photo_id:
        bot.send_photo(
            CHATS['reports'], photo=photo_id, caption=report_text,
            parse_mode='HTML',
        )
    else:
        bot.send_message(CHATS['reports'], report_text, parse_mode='HTML')

    mentions = _task_mentions(task_type, club)
    prefix = mentions if event in {'created', 'returned', 'completed'} else ''
    bot.send_message(
        CHATS['main_group'],
        f"{prefix}\n\n{short}" if prefix else short,
        parse_mode='HTML',
    )
    if task_type == REPAIR_TASK_TYPE:
        if photo_id:
            bot.send_photo(
                CHATS['repair_extra'], photo=photo_id, caption=full,
                parse_mode='HTML',
            )
        else:
            bot.send_message(CHATS['repair_extra'], full, parse_mode='HTML')


def _task_type_intro(task_type):
    if task_type == GENERAL_TASK_TYPE:
        return (
            'Здесь можно анонимно задать вопрос, оставить жалобу или предложить '
            'идею по работе клуба. Коротко напиши тему, а следующим сообщением '
            'расскажи подробнее.'
        )
    return TEXTS['messtype_dict'][task_type]


def _task_type_fill(task_type):
    if task_type == GENERAL_TASK_TYPE:
        return 'обращения'
    return TEXTS['messtype_fill'][task_type]


def _readonly_webapp_markup(status_key=None, page=0):
    from menu import _webapp_url

    markup = types.InlineKeyboardMarkup()
    if status_key in READONLY_TASK_STATUSES:
        markup.add(types.InlineKeyboardButton(
            'Вернуться',
            callback_data=f'readonly_tasks:{status_key}:{page}',
        ))
    problems_url = _webapp_url('problems')
    if problems_url:
        markup.add(types.InlineKeyboardButton(
            '🚩 Открыть в приложении',
            web_app=types.WebAppInfo(problems_url),
        ))
    return markup


def _readonly_plain_text(value):
    text = re.sub(r'<br\s*/?>', '\n', str(value or ''), flags=re.IGNORECASE)
    return unescape(re.sub(r'<[^>]+>', '', text)).strip()


def _readonly_html(value, limit=1000):
    text = _readonly_plain_text(value)
    result = []
    length = 0
    truncated = False
    for character in text:
        token = '<br>' if character == '\n' else escape(character)
        if length + len(token) > max(limit - 1, 0):
            truncated = True
            break
        result.append(token)
        length += len(token)
    if truncated:
        result.append('…')
    return ''.join(result)


def _readonly_task_rows(status_key, page):
    statuses = READONLY_TASK_STATUSES[status_key]
    placeholders = ','.join('?' for _status in statuses)
    direction = 'DESC' if status_key == 'done' else 'ASC'
    conn = sqlite3.connect(TASK_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        counts = {
            key: conn.execute(
                f"SELECT COUNT(*) FROM tasks WHERE status IN ({','.join('?' for _item in values)})",
                values,
            ).fetchone()[0]
            for key, values in READONLY_TASK_STATUSES.items()
        }
        total = counts[status_key]
        max_page = max((total - 1) // READONLY_TASK_PAGE_SIZE, 0)
        page = min(max(page, 0), max_page)
        rows = conn.execute(
            f'''SELECT ID, dtrep, type, club, title, photo, desc, status,
                       dtfb, feedback
                FROM tasks WHERE status IN ({placeholders})
                ORDER BY club COLLATE NOCASE, date(dtrep) {direction},
                         ID {direction}
                LIMIT ? OFFSET ?''',
            (*statuses, READONLY_TASK_PAGE_SIZE, page * READONLY_TASK_PAGE_SIZE),
        ).fetchall()
    finally:
        conn.close()
    return rows, counts, page, max_page


def show_readonly_tasks(message, bot, status_key='work', page=0, edit=False):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    if status_key not in READONLY_TASK_STATUSES:
        status_key = 'work'
    rows, _counts, page, max_page = _readonly_task_rows(status_key, page)
    grouped = {}
    for task in rows:
        club = str(task['club'] or 'Без клуба')
        grouped.setdefault(club, []).append(task)
    configured_clubs = list(get_clubs())
    club_order = [club for club in configured_clubs if club in grouped]
    club_order.extend(sorted(
        (club for club in grouped if club not in configured_clubs),
        key=str.casefold,
    ))
    text_lines = []
    for club in club_order:
        if text_lines:
            text_lines.append('')
        text_lines.append(f'<b>{escape(club)}:</b>')
        for index, task in enumerate(grouped[club], start=1):
            text_lines.append(
                f"{index}) {_readonly_html(task['title'], 80) or 'Без названия'}"
            )
    if not rows:
        text_lines.append('Заявок в этом разделе нет.')
    if max_page:
        text_lines.extend(('', f'Страница {page + 1} из {max_page + 1}'))

    markup = types.InlineKeyboardMarkup(row_width=2)
    task_buttons = [
        types.InlineKeyboardButton(
            f"{str(task['club'] or '—')[:3]}: "
            f"{(_readonly_plain_text(task['title'])[:12] or 'Без названия')}"
            f"{'...' if len(_readonly_plain_text(task['title'])) > 12 else ''}",
            callback_data=f"readonly_task:{task['ID']}:{status_key}:{page}",
        )
        for task in rows
    ]
    for index in range(0, len(task_buttons), 2):
        markup.row(*task_buttons[index:index + 2])
    navigation = []
    if page > 0:
        navigation.append(types.InlineKeyboardButton(
            '← Назад', callback_data=f'readonly_tasks:{status_key}:{page - 1}',
        ))
    if page < max_page:
        navigation.append(types.InlineKeyboardButton(
            'Дальше →', callback_data=f'readonly_tasks:{status_key}:{page + 1}',
        ))
    if navigation:
        markup.row(*navigation)
    markup.row(types.InlineKeyboardButton(
        'Вернуться', callback_data='readonly_tasks:close',
    ))
    headings = {
        'work': 'Вот список текущих проблем:',
        'review': 'Вот список рассматриваемых проблем:',
        'done': 'Вот список выполненных проблем:',
    }
    task_list = '\n'.join(text_lines)
    text = f"{headings[status_key]}\n\n{task_list}"
    if edit:
        try:
            bot.edit_message_text(
                text,
                message.chat.id,
                message.id,
                reply_markup=markup,
                parse_mode='HTML',
            )
            return
        except Exception:
            pass
    bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode='HTML')
    instructions = {
        'work': 'Выбери одну, чтобы посмотреть подробнее или нажми "Вернуться"',
        'review': 'Выбери одну, чтобы посмотреть подробнее или нажми "Вернуться"',
        'done': 'Выбери одну, чтобы посмотреть подробнее или нажми "Вернуться"',
    }
    bot.send_message(
        message.chat.id,
        instructions[status_key],
        reply_markup=types.ReplyKeyboardRemove(),
    )


def show_readonly_task_detail(message, bot, task_id, source_status='work', page=0):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    conn = sqlite3.connect(TASK_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        task = conn.execute(
            '''SELECT ID, dtrep, type, club, title, photo, desc, status,
                      dtfb, feedback FROM tasks WHERE ID=?''',
            (task_id,),
        ).fetchone()
    finally:
        conn.close()
    if not task:
        bot.send_message(message.chat.id, 'Заявка не найдена или уже удалена.')
        return
    def detail_text(compact=False):
        description_limit = 250 if compact else 1000
        feedback_limit = 300 if compact else 1800
        return (
            f"<b>{_readonly_html(task['title'], 100) or 'Без названия'}</b>\n\n"
            f"<b>Тип:</b> {_readonly_html(task['type'], 100) or '—'}\n"
            f"<b>Клуб:</b> {_readonly_html(task['club'], 100) or '—'}\n\n"
            f"<b>Описание:</b> "
            f"{_readonly_html(task['desc'], description_limit) or '—'}\n\n"
            f"<b>Статус:</b> {_readonly_html(task['status'], 100) or '—'}\n"
            f"<b>Дата:</b> {escape(str(task['dtrep'] or '—')[:10])}\n\n"
            f"💬 <b>История решения:</b>\n"
            f"{_readonly_html(task['feedback'], feedback_limit) or 'Ожидает решения...'}"
        )

    markup = _readonly_webapp_markup(source_status, page)
    if task['photo'] is not None:
        photo = io.BytesIO(task['photo'])
        photo.name = f"problem_{task['ID']}.jpg"
        bot.send_photo(
            message.chat.id,
            photo=photo,
            caption=detail_text(compact=True),
            reply_markup=markup,
            parse_mode='HTML',
        )
    else:
        bot.send_message(
            message.chat.id,
            detail_text(),
            reply_markup=markup,
            parse_mode='HTML',
        )

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
    if not require_role(message, bot, ROLE_EMPLOYEE):
        return
    bot.send_message(message.chat.id, f'Это полностью анонимная доска, где ты можешь сообщить менеджеру о проблеме в клубе, предложить улучшение или просто узнать мнение руководства о чем либо, а также посмотреть запросы от других!')
    markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add(*funclist_task)
    bot.send_message(message.chat.id, 'Что вы хотите сделать? 👀', reply_markup=markup)
    bot.register_next_step_handler(message, func_task,bot)

def func_task(message,bot):
    if not require_role(message, bot, ROLE_EMPLOYEE):
        return
    if message.text=='➕ Добавить':
        bot.send_message(message.chat.id, f'Здесь ты можешь добавить свой стикер на доску')
        add_task(message,bot)

    elif message.text=='⭕ Текущие':
        show_active_tasks(message,bot)

    elif message.text=='👀 Рассматриваемые':
        show_review_tasks(message,bot)

    elif message.text=='🛠 Ремонт':
        show_active_type(message,bot, 'Ремонт')

    elif message.text=='🤖 Улучшения бота':
        show_active_type(message,bot,'Улучшение бота')

    elif message.text=='✔ Выполненные':
        show_done_tasks(message,0,bot)

    elif message.text == '🏠 Главное меню':
        returnback(message, bot)
    
    else:
        markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
        markup.add(*funclist_task)
        bot.send_message(message.chat.id, 'Что вы хотите сделать? 👀', reply_markup=markup)
        bot.register_next_step_handler(message, func_task,bot)

###### add

def add_task(message,bot):
        conn=sqlite3.connect('db/omgbot.sql')
        cur = conn.cursor()
        cur.execute("SELECT title FROM tasks WHERE status=?", ("В работе",))
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
     markup.add(*get_clublist_task(),"Вернуться")
     bot.send_message(message.chat.id, f'К какому клубу относится твое обращение?',reply_markup=markup)
     bot.register_next_step_handler(message, add_title, task_type,bot)

def add_title(message,task_type,bot):

    if message.text=="Вернуться":
        returnback(message,bot)

    elif message.text in get_clublist_task():

        club_task=message.text
        markup = telebot.types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
        markup.add("Вернуться")
        bot.send_message(message.chat.id, _task_type_intro(task_type))
        bot.send_message(message.chat.id,f'Напиши название (суть) {_task_type_fill(task_type)} (не более 50-ти симоволов) или если хочешь сменить тип обращения, нажми "Вернуться"', reply_markup=markup)
        bot.register_next_step_handler(message, add_desc,task_type,club_task,bot)

    else:

        markup=types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
        markup.add(*get_clublist_task(),"Вернуться")
        bot.send_message(message.chat.id, f'К какому клубу относится твое обращение?',reply_markup=markup)
        bot.register_next_step_handler(message, add_title, task_type,bot)


def add_desc(message,task_type,club_task,bot):

    if message.text=="Вернуться":

        returnback(message,bot)
    
    elif message.photo:
        markup = telebot.types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
        markup.add("Вернуться")
        bot.send_message(message.chat.id,f'Напиши название (суть) {_task_type_fill(task_type)} (не более 50-ти симоволов) или если хочешь сменить тип обращения, нажми "Вернуться"', reply_markup=markup)
        bot.send_message(message.chat.id, "Название не должно быть фотографией!")
        bot.register_next_step_handler(message, add_desc,task_type,club_task,bot)
        
    elif len(message.text)>50:

        markup = telebot.types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
        markup.add("Вернуться")
        bot.send_message(message.chat.id,f'Напиши название (суть) {_task_type_fill(task_type)} (не более 50-ти симоволов) или если хочешь сменить тип обращения, нажми "Вернуться"', reply_markup=markup)
        bot.send_message(message.chat.id, "Слишком длинное! Максимум 50 символов!")
        bot.register_next_step_handler(message, add_desc,task_type,club_task,bot)

    elif message.text.isnumeric():
        markup = telebot.types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
        markup.add("Вернуться")
        bot.send_message(message.chat.id,f'Напиши название (суть) {_task_type_fill(task_type)} (не более 50-ти симоволов) или если хочешь сменить тип обращения, нажми "Вернуться"', reply_markup=markup)
        bot.send_message(message.chat.id, "Название проблемы не должно состоять только из числа!")
        bot.register_next_step_handler(message, add_desc,task_type,club_task,bot)
        

    else:

        title = message.text
        markup = telebot.types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
        markup.add("Вернуться")
        bot.send_message(message.chat.id,f'Напиши описание {_task_type_fill(task_type)} (не более 1000-ти симоволов) или если хочешь сменить тип обращения, нажми "Вернуться"', reply_markup=markup)
        bot.register_next_step_handler(message, add_photo,task_type,title,club_task,bot)

def add_photo(message, task_type,title,club_task,bot):
    
    if message.text=="Вернуться":

        returnback(message,bot)
    
    elif message.photo:
        markup = telebot.types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
        markup.add("Вернуться")
        bot.send_message(message.chat.id,f'Напиши описание {_task_type_fill(task_type)} (не более 1000-ти симоволов) или если хочешь сменить тип обращения, нажми "Вернуться"', reply_markup=markup)
        bot.send_message(message.chat.id, "Описание не должно быть фотографией! На следующем этапе ты сможешь добавить фото, сейчас напиши только текст.")
        bot.register_next_step_handler(message, add_photo,task_type,title,club_task,bot)
    
    elif len(message.text)>1020:

        markup = telebot.types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
        markup.add("Вернуться")
        bot.send_message(message.chat.id,f'Напиши описание {_task_type_fill(task_type)} (не более 1000-ти симоволов) или если хочешь сменить тип обращения, нажми "Вернуться"', reply_markup=markup)
        bot.send_message(message.chat.id, "Слишком длинное! Максимум 1000 символов!")
        bot.register_next_step_handler(message, add_photo,task_type,title,club_task,bot)
    
    else:

        descrip=message.text
        markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
        markup.add("Без фото","Вернуться")
        bot.send_message(message.chat.id,f'Прикрепи фото, или, если его нет, нажми "Без фото". Если хочешь сменить тип обращения, нажми "Вернуться"', reply_markup=markup)
        bot.register_next_step_handler(message, send_task,task_type,title,descrip,club_task,bot)


def send_task(message,task_type,title, descrip,club_task,bot):
    if not require_role(message, bot, ROLE_EMPLOYEE):
        return
    today=datetime.today().strftime('%Y-%m-%d')
    photo_id_to_send = None # Инициализируем переменную для фото
    actor = (
        task_actor_snapshot(get_user(message))
        if task_type == REPAIR_TASK_TYPE else None
    )

    if message.text=="Вернуться":
        returnback(message,bot)
        return # <-- ИСПРАВЛЕНИЕ: прерываем выполнение, чтобы не отправлялась пустая задача

    elif message.text=="Без фото":
        conn=sqlite3.connect('db/omgbot.sql')
        cur = conn.cursor() 
        data_tuple=(today,task_type,club_task,title,descrip,"В работе")
        cur.execute(""" INSERT INTO tasks (dtrep,type, club, title, desc,status) VALUES (?,?,?,?,?,?)""", data_tuple)
        record_task_event(conn, cur.lastrowid, 'created', actor=actor)
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
        record_task_event(conn, cur.lastrowid, 'created', actor=actor)
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
    _notification_full, _notification_short, confirmation = created_task_notification(
        task_type,
        club_task,
        title,
        descrip,
        actor=actor,
    )

    bot.send_message(message.chat.id, confirmation)
    _send_task_notification(
        bot, 'created', task_type, club_task, title,
        description=descrip, actor=actor, photo_id=photo_id_to_send,
    )

    returnback(message, bot)

###### show active

def show_active_tasks(message, bot):
    if not require_role(message, bot, ROLE_EMPLOYEE):
        return
    conn = sqlite3.connect('db/omgbot.sql')
    cur = conn.cursor()
    cur.execute("SELECT id, title, club, status FROM tasks WHERE status='В работе'")
    tasks = cur.fetchall()
    cur.close()
    conn.close()

    club_names = get_clublist_task()
    tasks_by_club = {club: [] for club in club_names}
    for task_id, title, club, status in tasks:
        if club in tasks_by_club:
            tasks_by_club[club].append((task_id, title, status))

    list_buttons = []
    text_lines = []

    for club in club_names:
        club_tasks = tasks_by_club[club]
        if club_tasks: 
            text_lines.append(f"\n<b>{club}:</b>")
            for i, (task_id, title, status) in enumerate(club_tasks, 1):
                text_lines.append(f"{i}) {title}")
                
                short_title = title[:12] + "..." if len(title) > 12 else title
                list_buttons.append(types.InlineKeyboardButton(
                    f"{club[:3]}: {short_title}",
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


def show_review_tasks(message, bot):
    if not require_role(message, bot, ROLE_EMPLOYEE):
        return
    conn = sqlite3.connect('db/omgbot.sql')
    cur = conn.cursor()
    cur.execute("SELECT id, title, club FROM tasks WHERE status='На проверке'")
    tasks = cur.fetchall()
    cur.close()
    conn.close()

    club_names = get_clublist_task()
    tasks_by_club = {club: [] for club in club_names}
    for task_id, title, club in tasks:
        if club in tasks_by_club:
            tasks_by_club[club].append((task_id, title))

    list_buttons = []
    text_lines = []
    for club in club_names:
        club_tasks = tasks_by_club[club]
        if club_tasks:
            text_lines.append(f"\n<b>{club}:</b>")
            for i, (task_id, title) in enumerate(club_tasks, 1):
                text_lines.append(f"{i}) {title}")
                short_title = title[:12] + "..." if len(title) > 12 else title
                list_buttons.append(types.InlineKeyboardButton(
                    f"👀 {club[:3]}: {short_title}",
                    callback_data=f'all_{task_id}'
                ))

    markup = telebot.types.InlineKeyboardMarkup()
    for i in range(len(list_buttons) // col):
        markup.row(*list_buttons[i * col:(i + 1) * col])
    if len(list_buttons) % col != 0:
        markup.row(*list_buttons[len(list_buttons) - len(list_buttons) % col:])
    markup.row(types.InlineKeyboardButton("Вернуться", callback_data="all_back"))

    text = "\n".join(text_lines) if text_lines else "Нет задач, ожидающих проверки"
    bot.send_message(message.chat.id, f'Вот список рассматриваемых проблем:\n{text}', reply_markup=markup, parse_mode='HTML')
    bot.send_message(message.chat.id, 'Выбери одну, чтобы проверить решение или вернуть задачу в работу.', reply_markup=types.ReplyKeyboardRemove())

###### show repairs

def show_active_type(message, bot, category):
    if not require_role(message, bot, ROLE_EMPLOYEE):
        return
    conn = sqlite3.connect('db/omgbot.sql')
    cur = conn.cursor()
    cur.execute("SELECT id, title, club, status FROM tasks WHERE status='В работе' AND type=?", (category,))
    tasks = cur.fetchall()
    cur.close()
    conn.close()

    club_names = get_clublist_task()
    tasks_by_club = {club: [] for club in club_names}
    for task_id, title, club, status in tasks:
        if club in tasks_by_club:
            tasks_by_club[club].append((task_id, title, status))

    list_buttons = []
    text_lines = []
    task_counter = 1 

    for club in club_names:
        club_tasks = tasks_by_club[club]
        if club_tasks: 
            text_lines.append(f"\n<b>{club}:</b>")
            for task_id, title, status in club_tasks:
                text_lines.append(f"{task_counter}) {title}")
                
                short_title = title[:12] + "..." if len(title) > 12 else title
                list_buttons.append(types.InlineKeyboardButton(
                    f"{club[:3]}: {short_title}",
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
    if not require_role(message, bot, ROLE_EMPLOYEE):
        return

    if message.text == 'Выбрать другое':
        if current_status == 'На проверке':
            show_review_tasks(message, bot)
        else:
            show_active_tasks(message, bot)

    elif current_status == 'В работе' and message.text == 'Обработать':
        if not require_role(message, bot, ROLE_TECHNICIAN):
            show_active_tasks(message, bot)
        else:
            markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
            markup.add("Вернуться")
            bot.send_message(message.chat.id, 'Напишите решение по проблеме (оно уйдет сотрудникам на проверку):', reply_markup=markup)
            bot.register_next_step_handler(message, commit_task, task_id, bot)

    elif current_status == 'На проверке':
        if message.text == '✅ Подтвердить решение':
            today = datetime.now(pytz.timezone('Europe/Moscow')).strftime('%Y-%m-%d')
            conn = sqlite3.connect('db/omgbot.sql')
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                "SELECT title, type, club FROM tasks WHERE id = ? AND status = 'На проверке'",
                (task_id,),
            )
            task = cur.fetchone()
            actor = task_actor_snapshot(get_user(message))
            cur.execute(
                "UPDATE tasks SET status = 'Выполнено', dtfb = ? WHERE id = ? AND status = 'На проверке'",
                (today, task_id),
            )
            changed = cur.rowcount
            if changed:
                record_task_event(conn, task_id, 'confirmed', actor=actor)
            conn.commit()
            cur.close()
            conn.close()
            if changed:
                bot.send_message(message.chat.id, "✅ Спасибо! Проблема окончательно закрыта и перенесена в архив.", reply_markup=types.ReplyKeyboardRemove())
                _send_task_notification(
                    bot, 'completed', task['type'], task['club'],
                    task['title'], actor=actor,
                )
            else:
                bot.send_message(message.chat.id, "⚠️ Статус задачи уже изменился.", reply_markup=types.ReplyKeyboardRemove())
            show_review_tasks(message, bot)

        elif message.text == '❌ Вернуть в работу':
            markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
            markup.add("Вернуться")
            bot.send_message(message.chat.id, "Опишите, почему решение не помогло или что осталось неисправным:", reply_markup=markup)
            bot.register_next_step_handler(message, return_task_to_work, task_id, bot)
        else:
            show_review_tasks(message, bot)
    else:
        show_active_tasks(message, bot)


def commit_task(message, task_id, bot):
    if not require_role(message, bot, ROLE_TECHNICIAN):
        return
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
    if not require_role(message, bot, ROLE_TECHNICIAN):
        return
    if message.text == 'Нет':
        show_active_tasks(message, bot)
    elif message.text == 'Да':
        now = datetime.now(pytz.timezone('Europe/Moscow'))
        today_short = now.strftime('%d.%m')
        review_since = now.strftime('%Y-%m-%d')
        actor = task_actor_snapshot(get_user(message))
        
        conn = sqlite3.connect('db/omgbot.sql')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT feedback, title, type, club FROM tasks WHERE id=? AND status='В работе'", (task_id,))
        task = cur.fetchone()
        if not task:
            cur.close()
            conn.close()
            bot.send_message(message.chat.id, "⚠️ Статус задачи уже изменился.")
            show_active_tasks(message, bot)
            return
        
        old_feedback = task['feedback'] if task['feedback'] else ""
        title = task['title']
        task_type = task['type']
        club_task = task['club']
        
        # Добавляем новый ответ админа к истории
        new_entry = f"<b>[{today_short}] Админ:</b> {answer}"
        new_feedback = f"{old_feedback}\n\n{new_entry}".strip()

        cur.execute(
            "UPDATE tasks SET status = 'На проверке', feedback = ?, dtfb = ? "
            "WHERE id = ? AND status = 'В работе'",
            (new_feedback, review_since, task_id),
        )
        changed = cur.rowcount
        if changed:
            record_task_event(
                conn, task_id, 'solution', event_at=now, actor=actor,
            )
        conn.commit()
        cur.close()
        conn.close()

        if not changed:
            bot.send_message(message.chat.id, "⚠️ Статус задачи уже изменился.")
            show_active_tasks(message, bot)
            return

        bot.send_message(message.chat.id, "✅ Решение отправлено! Задача ожидает подтверждения.", reply_markup=types.ReplyKeyboardRemove())
        
        # --- ФОРМИРОВАНИЕ УВЕДОМЛЕНИЙ ---
        _send_task_notification(
            bot, 'solution', task_type, club_task, title,
            message=answer, actor=actor,
        )
            
        show_active_tasks(message, bot)


def return_task_to_work(message, task_id, bot):
    if not require_role(message, bot, ROLE_EMPLOYEE):
        return
    if message.text == "Вернуться":
        show_review_tasks(message, bot)
        return

    reason = message.text
    today_short = datetime.today().strftime('%d.%m')
    actor = task_actor_snapshot(get_user(message))

    conn = sqlite3.connect('db/omgbot.sql')
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT feedback, title, type, club FROM tasks WHERE id=? AND status='На проверке'", (task_id,))
    task = cur.fetchone()
    if not task:
        cur.close()
        conn.close()
        bot.send_message(message.chat.id, "⚠️ Статус задачи уже изменился.")
        show_review_tasks(message, bot)
        return
    
    old_feedback = task['feedback'] if task['feedback'] else ""
    title = task['title']
    task_type = task['type']
    club_task = task['club']

    # Добавляем комментарий сотрудника к истории
    new_entry = f"<b>[{today_short}] Сотрудник:</b> {reason}"
    new_feedback = f"{old_feedback}\n\n{new_entry}".strip()

    cur.execute(
        "UPDATE tasks SET status = 'В работе', feedback = ?, dtfb = NULL "
        "WHERE id = ? AND status = 'На проверке'",
        (new_feedback, task_id),
    )
    changed = cur.rowcount
    if changed:
        record_task_event(conn, task_id, 'returned', actor=actor)
    conn.commit()
    cur.close()
    conn.close()

    if not changed:
        bot.send_message(message.chat.id, "⚠️ Статус задачи уже изменился.")
        show_review_tasks(message, bot)
        return

    bot.send_message(message.chat.id, "❌ Задача возвращена в работу. Админы увидят ваш комментарий.", reply_markup=types.ReplyKeyboardRemove())
    
    # --- ФОРМИРОВАНИЕ УВЕДОМЛЕНИЙ ---
    _send_task_notification(
        bot, 'returned', task_type, club_task, title,
        message=reason, actor=actor,
    )
        
    show_review_tasks(message, bot)


def auto_close_review_tasks(now=None, bot=None):
    """Закрывает задачи, которые 14 дней находятся на проверке."""
    now = now or datetime.now(pytz.timezone('Europe/Moscow'))
    today = now.date()
    deadline = today - timedelta(days=TASK_REVIEW_DAYS)
    today_iso = today.strftime('%Y-%m-%d')
    deadline_iso = deadline.strftime('%Y-%m-%d')
    today_short = today.strftime('%d.%m')
    actor = system_task_actor()

    conn = sqlite3.connect(TASK_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            # Старые задачи на проверке не имеют даты: запускаем им отсчёт сейчас.
            conn.execute(
                "UPDATE tasks SET dtfb=? WHERE status='На проверке' AND (dtfb IS NULL OR dtfb='')",
                (today_iso,),
            )
            tasks = conn.execute(
                """SELECT id, type, club, title, feedback FROM tasks
                   WHERE status='На проверке' AND date(dtfb) <= date(?)""",
                (deadline_iso,),
            ).fetchall()

            for task in tasks:
                old_feedback = task['feedback'] or ''
                entry = f"<b>[{today_short}] Система:</b> задача автоматически закрыта через {TASK_REVIEW_DAYS} дней без возврата."
                feedback = f"{old_feedback}\n\n{entry}".strip()
                conn.execute(
                    "UPDATE tasks SET status='Выполнено', dtfb=?, feedback=? WHERE id=?",
                    (today_iso, feedback, task['id']),
                )
                record_task_event(
                    conn, task['id'], 'confirmed', event_at=now, actor=actor,
                )
    finally:
        conn.close()

    if tasks:
        print(f"Автозакрытие задач: {len(tasks)}")
        if bot:
            for task in tasks:
                _send_task_notification(
                    bot, 'completed', task['type'], task['club'],
                    task['title'], actor=actor,
                )
    return len(tasks)


def send_shift_review_reminders(bot, now=None):
    """Напоминает сотрудникам сегодняшней смены проверить решения их клуба."""
    now = now or datetime.now(pytz.timezone('Europe/Moscow'))
    date_iso = now.strftime('%Y-%m-%d')

    conn = sqlite3.connect(TASK_DB_PATH)
    try:
        tasks = conn.execute(
            """SELECT id, type, club, title FROM tasks
               WHERE status='На проверке' ORDER BY club, id"""
        ).fetchall()
        if not tasks:
            return 0

        users = conn.execute(
            """SELECT login, chatid FROM users
               WHERE status >= 0 AND login IS NOT NULL AND login <> ''
                 AND chatid IS NOT NULL AND chatid <> ''"""
        ).fetchall()
    finally:
        conn.close()

    from rasp import fetch_schedule_from_api
    schedule_data = fetch_schedule_from_api(date_iso)
    if not schedule_data.get('ok'):
        print(f"Ошибка напоминаний Taskboard: {schedule_data.get('error', 'расписание недоступно')}")
        return 0

    def normalize_login(login):
        return str(login or '').strip().lstrip('@').lower()

    chats_by_login = {normalize_login(login): chatid for login, chatid in users}
    tasks_by_club = {}
    for task_id, task_type, club, title in tasks:
        tasks_by_club.setdefault(club, {})[task_id] = (task_type, title)

    reminders = {}
    for location in schedule_data.get('locations', []):
        club = location.get('title')
        club_tasks = tasks_by_club.get(club)
        if not club_tasks:
            continue
        for shift in location.get('shifts', []):
            chatid = chats_by_login.get(normalize_login(shift.get('telegram')))
            if not chatid:
                continue
            reminders.setdefault(str(chatid), {}).setdefault(club, {}).update(club_tasks)

    sent = 0
    for chatid, club_tasks in reminders.items():
        lines = ["👀 Проверьте решения по задачам вашей смены:"]
        for club, club_task_items in club_tasks.items():
            lines.append(f"\n{club}:")
            for number, (_task_id, (task_type, title)) in enumerate(club_task_items.items(), 1):
                lines.append(f"{number}) [{task_type}] {title}")
        lines.append("\nОткройте: «Доска проблем» → «👀 Рассматриваемые». Если решение не помогло, верните задачу в работу.")
        try:
            target_chatid = int(chatid) if chatid.lstrip('-').isdigit() else chatid
            bot.send_message(target_chatid, "\n".join(lines))
            sent += 1
        except Exception as e:
            print(f"Ошибка напоминания Taskboard для chatid={chatid}: {e}")

    return sent
    
##### done tasks

    
def show_done_tasks(message, page, bot):
    if not require_role(message, bot, ROLE_EMPLOYEE):
        return
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

def register_readonly_callback(bot):
    @bot.callback_query_handler(
        func=lambda call: str(call.data or '').startswith('readonly_')
    )
    def readonly_callback(call):
        if not require_role(call, bot, ROLE_MANAGER):
            return
        bot.answer_callback_query(call.id)
        if call.data == 'readonly_tasks:close':
            bot.clear_step_handler_by_chat_id(call.message.chat.id)
            try:
                bot.delete_message(call.message.chat.id, call.message.id)
            except Exception:
                bot.edit_message_reply_markup(
                    call.message.chat.id,
                    call.message.id,
                    reply_markup=None,
                )
            call.message.from_user = call.from_user
            from menu import hello
            hello(call.message.chat.id, bot)
            return
        if call.data.startswith('readonly_task:'):
            try:
                parts = call.data.split(':')
                task_id = int(parts[1])
                status_key = parts[2] if len(parts) > 2 else 'work'
                page = int(parts[3]) if len(parts) > 3 else 0
            except (IndexError, ValueError):
                bot.send_message(call.message.chat.id, 'Не удалось открыть заявку.')
                return
            bot.edit_message_reply_markup(
                call.message.chat.id,
                call.message.id,
                reply_markup=None,
            )
            show_readonly_task_detail(
                call.message, bot, task_id, status_key, page,
            )
            return
        parts = call.data.split(':')
        status_key = parts[1] if len(parts) > 1 else 'work'
        try:
            page = int(parts[2]) if len(parts) > 2 else 0
        except ValueError:
            page = 0
        show_readonly_tasks(
            call.message, bot, status_key=status_key, page=page, edit=True,
        )


def register_callback(bot):
    @bot.callback_query_handler(func=lambda call: call.data.startswith('all_'))
    def callback(call):
        if not require_role(call, bot, ROLE_EMPLOYEE):
            return
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
            elif (role_of(call) or ROLE_EMPLOYEE) >= ROLE_TECHNICIAN:
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
        if not require_role(call, bot, ROLE_EMPLOYEE):
            return
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
