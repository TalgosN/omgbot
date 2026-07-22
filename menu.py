from telebot import *
import sqlite3
from constants import *
from admin_panel import sync_config
from permissions import ROLE_EMPLOYEE, ROLE_MANAGER, ROLE_OWNER, require_role

def chatid_to_users(chatid):
    conn = sqlite3.connect('db/omgbot.sql')
    cur = conn.cursor()
    # ИСПРАВЛЕНО: Безопасный запрос через ?
    cur.execute("SELECT * FROM users WHERE CAST(chatid AS TEXT)=CAST(? AS TEXT)", (chatid,))
    users = cur.fetchall()
    cur.close()
    conn.close()
    return users

def hello(chatid, bot):
    bot.clear_step_handler_by_chat_id(chatid) 
    users = chatid_to_users(chatid)

    if len(users) == 0 or users[0][8] not in funclist:
        bot.send_message(chatid, 'Доступ запрещен!')
    else:
        # users[0][4] - это имя, users[0][8] - это роль (индекс списка кнопок)
        bot.send_message(chatid, f'Привет, {users[0][4]}!')
        
        markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
        buttons = funclist[users[0][8]]
        markup.add(*buttons)

        msg = bot.send_message(chatid, 'Что вы хотите сделать? 👀', reply_markup=markup)
        bot.register_next_step_handler(msg, func, bot)

def func(message, bot):
    if not require_role(message, bot, ROLE_EMPLOYEE):
        return
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
        if require_role(message, bot, ROLE_OWNER):
            from finance import finance
            finance(message, bot)
    
    elif a == "🧑🏻‍💻 Админ панель": # Кнопка для админов
        if require_role(message, bot, ROLE_MANAGER):
            admin_menu(message, bot)
    
    elif a == '📦 Расходники':
        from consumables import consumables_menu
        consumables_menu(message, bot)

    elif a == '🆘 Помощь':
        help(bot, message)
        hello(message.chat.id, bot)
    else:
        # Если прислали что-то левое — возвращаем в меню
        hello(message.chat.id, bot)

def admin_menu(message, bot):
    user = require_role(message, bot, ROLE_MANAGER)
    if not user:
        return
    from constants import admin_funclist, owner_admin_funclist
    from admin_panel import admin_func_handler # Импортируем обработчик из нового файла
    
    markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    buttons = owner_admin_funclist if user['status'] >= ROLE_OWNER else admin_funclist
    markup.add(*buttons)
    msg = bot.send_message(message.chat.id, 'Панель администратора 🧑🏻‍💻', reply_markup=markup)
    bot.register_next_step_handler(msg, admin_func_handler, bot)

def help(bot, message):
    bot.send_message(message.chat.id,
                     'Ну, так уж и быть, помогу! Смотри, какие у меня есть команды!\n\n*/start* - Начать работу со мной (только в ЛС!)\n*/weather* - Показать погоду\n*/today* - Показать расписание на сегодня\n*/repair* - Показать список проблем\n*/roll* - Разрешу любой спор', parse_mode="Markdown")
    
    bot.send_message(message.chat.id,
                     'Хочешь отличиться? Пиши хештеги!\n\n*#продление*\n*#др*\n*#инициатива*\n\nДобавь чёткое описание. Клуб указывать не нужно — я определю его автоматически по твоей смене на сегодня. Сейчас покажу...', parse_mode="Markdown")
    bot.send_message(message.chat.id,
                     "```Правильно!\n#продление Татьяна 15:00-16:00```", parse_mode='MarkdownV2')
    
    bot.send_message(message.chat.id,
                     'Продал сертификат или абонемент? Запиши, чтобы не забыть!\n\n*#серт* — номер от 3000\n*#абик* — номер меньше 1000\n\nНомер и сумма должны состоять только из цифр. Вот например...', parse_mode="Markdown")
    bot.send_message(message.chat.id, "```Правильно!\n#серт *номер* *сумма*\n#абик *номер* *сумма*```", parse_mode='MarkdownV2')
    
    bot.send_message(message.chat.id,
                     'О тебе много пишут в интернете?\n\n*#отзывы* - команда для тебя!\n\nВот например...', parse_mode="Markdown")
    bot.send_message(message.chat.id, "```Правильно!\n#отзывы *количество* *описание* (2гис, яндекс)*```", parse_mode='MarkdownV2')

    bot.send_message(message.chat.id,
                     'Хотел поесть, но пришло много клиентов?\n\nПиши *#двойная*! Можно указать целое или дробное количество часов.\n\nВот например...', parse_mode="Markdown")
    bot.send_message(message.chat.id, "```Правильно!\n#двойная *количество часов* *описание*```", parse_mode='MarkdownV2')

    bot.send_message(message.chat.id,
                     'Продал автосимулятор или провёл активацию?\n\n*#автосим*\n*#активация*\n\nПосле хештега укажи сумму бонуса.', parse_mode="Markdown")
    bot.send_message(message.chat.id, "```Правильно!\n#автосим *сумма бонуса*\n#активация *сумма бонуса*```", parse_mode='MarkdownV2')

    bot.send_message(message.chat.id,
                     '*#штраф* доступен только руководству. Укажи Telegram-логин сотрудника из базы и причину.', parse_mode="Markdown")
    bot.send_message(message.chat.id, "```Правильно!\n#штраф *@логин* *причина*```", parse_mode='MarkdownV2')

    bot.send_message(message.chat.id, "Надеюсь помог тебе! До встречи 🌍")
