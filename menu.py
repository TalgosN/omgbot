from telebot import *
import sqlite3
from constants import *
from sync_clubs import sync_config

def chatid_to_users(chatid):
    conn = sqlite3.connect('db/omgbot.sql')
    cur = conn.cursor()
    # ИСПРАВЛЕНО: Безопасный запрос через ?
    cur.execute("SELECT * FROM users_new WHERE chatid=?", (chatid,))
    users = cur.fetchall()
    cur.close()
    conn.close()
    return users

def hello(chatid, bot): 
    users = chatid_to_users(chatid)

    if len(users) == 0:
        bot.send_message(chatid, 'Доступ запрещен!')
    else:
        # users[0][4] - это имя, users[0][8] - это роль (индекс списка кнопок)
        bot.send_message(chatid, f'Привет, {users[0][4]}!')
        
        markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
        try:
            buttons = funclist[users[0][8]]
            markup.add(*buttons)
        except IndexError:
            # Защита, если роль в базе больше, чем есть вариантов меню
            markup.add("🆘 Помощь")

        msg = bot.send_message(chatid, 'Что вы хотите сделать? 👀', reply_markup=markup)
        bot.register_next_step_handler(msg, func, bot)

def func(message, bot):
    a = message.text

    if a == '👨🏻‍💻 Смена':
        from openclose import func_today
        func_today(message, bot)

    elif a == '🚩 Доска проблем':
        from taskboard import task_board
        task_board(message, bot)
        
    elif a == '👤 Аккаунт':
        from account import account_settings
        account_settings(message, bot)
        
    elif a == '🗓 Расписание':
        from rasp import rasp
        rasp(message, bot)
        
    elif a == '💲 Финансы':
        conn = sqlite3.connect('db/omgbot.sql')
        cur = conn.cursor()
        # ИСПРАВЛЕНО: Безопасный запрос через ?
        cur.execute("SELECT status from users_new WHERE login=?", ("@" + message.from_user.username,))
        users = cur.fetchall()
        cur.close()
        conn.close()
        
        # Проверка прав (status >= 2)
        if len(users) == 0 or users[0][0] < 2:
            bot.send_message(message.chat.id, "У вас недостаточно прав!")
            hello(message.chat.id, bot)
        else:
            from finance import finance
            finance(message, bot)
    
    elif a == "⚙️ Обновить настройки": # Кнопка для админов
        conn = sqlite3.connect('db/omgbot.sql')
        cur = conn.cursor()
        cur.execute("SELECT status from users_new WHERE login=?", ("@" + message.from_user.username,))
        users = cur.fetchall()
        cur.close()
        conn.close()
        
        # Проверка прав (status >= 1, т.е. админы и выше)
        if len(users) == 0 or users[0][0] < 1:
            bot.send_message(message.chat.id, "У вас недостаточно прав!")
            hello(message.chat.id, bot)
        else:
            handle_update_config(message, bot)
        
    elif a == '🆘 Помощь':
        help(bot, message)
        hello(message.chat.id, bot)
    else:
        # Если прислали что-то левое — возвращаем в меню
        hello(message.chat.id, bot)

# ИСПРАВЛЕНО: Порядок аргументов (message, bot) теперь совпадает с вызовом
def handle_update_config(message, bot):
    # 1. Сообщение "Ждите"
    msg = bot.send_message(message.chat.id, "⏳ Подключаюсь к таблице 'Виарыч'...")
    
    # 2. Запуск функции
    try:
        report = sync_config() # Вызываем функцию из sync_clubs.py
        
        # 3. Редактируем сообщение с результатом
        bot.edit_message_text(chat_id=message.chat.id, message_id=msg.message_id, text=report)
        
    except Exception as e:
        # Если msg не успел создаться или другая ошибка
        try:
            bot.edit_message_text(chat_id=message.chat.id, message_id=msg.message_id, text=f"Ошибка скрипта: {e}")
        except:
            bot.send_message(message.chat.id, f"Ошибка скрипта: {e}")
        
    # Возвращаем меню
    hello(message.chat.id, bot)

def help(bot, message):
    bot.send_message(message.chat.id,
                     'Ну, так уж и быть, помогу! Смотри, какие у меня есть команды!\n\n*/start* - Начать работу со мной (только в ЛС!)\n*/help* - Ну ты вроде и так понял что к чему\n*/stats* - Расскажу о твоих достижениях! (или о достижениях другого если напишешь *тег* после команды)\n*/roll* - Разрешу любой спор', parse_mode="Markdown")
    
    bot.send_message(message.chat.id,
                     'Хочешь отличиться? Пиши хештеги!\n\n*#продление*\n*#др*\n*#инициатива*\n\nНе забудь дописать название клуба и добавить четкое описание! Сейчас покажу...', parse_mode="Markdown")
    bot.send_message(message.chat.id,
                     "```KPI\n#продление лен Татьяна 15:00-16:00```", parse_mode='MarkdownV2')
    
    bot.send_message(message.chat.id,
                     'Продал сертификат или абонемент? Запиши, чтобы не забыть!\n\n*#серт*\n*#абик*\n\nВот например...', parse_mode="Markdown")
    bot.send_message(message.chat.id, "```Правильно!\n#серт *номер* *сумма*```", parse_mode='MarkdownV2')
    
    bot.send_message(message.chat.id,
                     'О тебе много пишут в интернете?\n\n*#отзывы* - команда для тебя!\n\nВот например...', parse_mode="Markdown")
    bot.send_message(message.chat.id, "```Правильно!\n#отзывы *количество* *описание* (2гис, яндекс)*```", parse_mode='MarkdownV2')

    bot.send_message(message.chat.id,
                     'Хотел поесть, но пришло много клиентов?\n\nПиши *#двойная*!\n\nВот например...', parse_mode="Markdown")
    bot.send_message(message.chat.id, "```Правильно!\n#двойная *количество целых часов* *описание*```", parse_mode='MarkdownV2')

    bot.send_message(message.chat.id,
                     'Какой-то кожанный мешок не повинуется?\n\n*#штраф* - покажи ему кто тут хозяин!\n\nВот например...', parse_mode="Markdown")
    bot.send_message(message.chat.id, "```Правильно!\n#штраф *@тег* *описание*```", parse_mode='MarkdownV2')

    bot.send_message(message.chat.id, "Надеюсь помог тебе! До встречи 🌍")