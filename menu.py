from telebot import *
from html import escape
import os
import sqlite3
from constants import *
from admin_panel import sync_config
from permissions import (
    ROLE_EMPLOYEE,
    ROLE_MANAGER,
    ROLE_OWNER,
    disable_owner_employee_mode,
    enable_owner_employee_mode,
    get_user,
    is_owner_employee_mode,
    require_role,
)

HELP_MENU_BUTTONS = (
    '🚀 Быстрый старт',
    '🏷 KPI и хештеги',
    '🔗 Сервисы и таблицы',
    '🛟 Если что-то не работает',
    '⬅️ Вернуться',
)
OMG_SHIFT_URL = 'http://31.129.109.167/?page=settings'
GOOGLE_SHEET_LINKS = (
    ('📊 KPI OMG VR', 'https://docs.google.com/spreadsheets/d/1Jsz9im2ss9NIGfDcSLuIv37op_5QzaB03m6Z_JjDE9U/edit?gid=270994446#gid=270994446'),
    ('🧮 KPI helper', 'https://docs.google.com/spreadsheets/d/1McS0h3TxnxA-QqIWfR37LHBTN3D7TDSvzz3nitAaq98/edit?gid=0#gid=0'),
    ('👥 Сотрудники', 'https://docs.google.com/spreadsheets/d/1KyApsY0L_TL_WhpJDagB2VSZuvyxs4vb1aicgAHtUPk/edit?gid=0#gid=0'),
    ('⚙️ Виарыч', 'https://docs.google.com/spreadsheets/d/1LxBCPpWXtpS_EVhGUNuH2k4HtPnsu53ZF-4QaRET08Q/edit?gid=1951407525#gid=1951407525'),
    ('🚪 Открытия и закрытия', 'https://docs.google.com/spreadsheets/d/1JHOLFykKPbQ0Ou2zqq4GMPTVHFst8iFJxHkpMVjFlYk/edit?gid=972562992#gid=972562992'),
    ('📦 Расходники', 'https://docs.google.com/spreadsheets/d/1abZHTzME77-GHuU9L-32nANki671cSq3TPHIrWmXjZY/edit?gid=787957765#gid=787957765'),
)


def steamtracker_url():
    spreadsheet_id = os.getenv(
        'STEAMTRACKER_SPREADSHEET_ID',
        '1h_pCl6tpwYAhZveVSfUVGh4awl0r3yVCv1EwIIhJBZw',
    )
    return f'https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit'


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
    user = get_user(telegram_id=chatid)

    if not user or user['status'] not in funclist:
        bot.send_message(chatid, 'Доступ запрещен!')
    else:
        bot.send_message(chatid, f'Привет, {user["nick_name"]}!')
        
        markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
        buttons = list(funclist[user['status']])
        if is_owner_employee_mode(telegram_id=chatid):
            buttons.append(OWNER_MODE_BUTTON)
        elif user['status'] == ROLE_OWNER:
            buttons.append(OWNER_EMPLOYEE_MODE_BUTTON)
        markup.add(*buttons)

        msg = bot.send_message(chatid, 'Что вы хотите сделать? 👀', reply_markup=markup)
        bot.register_next_step_handler(msg, func, bot)

def func(message, bot):
    if not require_role(message, bot, ROLE_EMPLOYEE):
        return
    a = message.text

    if a == OWNER_EMPLOYEE_MODE_BUTTON:
        if enable_owner_employee_mode(message):
            bot.send_message(message.chat.id, 'Включён режим сотрудника с уровнем доступа 0.')
        hello(message.chat.id, bot)

    elif a == OWNER_MODE_BUTTON:
        if disable_owner_employee_mode(message):
            bot.send_message(message.chat.id, 'Режим владельца восстановлен.')
        hello(message.chat.id, bot)

    elif a == '👨🏻‍💻 Смена':
        from openclose import func_today
        func_today(message, bot)

    elif a == '🎮 Steam Tracker':
        from steamtracker.admin import promotion_admin_menu
        promotion_admin_menu(message, bot)

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
    markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add(*HELP_MENU_BUTTONS)
    sent = bot.send_message(
        message.chat.id,
        '<b>🆘 Помощь</b>\n\nВыбери нужный раздел — всё важное собрано здесь.',
        parse_mode='HTML',
        reply_markup=markup,
    )
    bot.register_next_step_handler(sent, help_handler, bot)


def kpi_help_text():
    try:
        from kpi import get_remote_hashtag_rules
        rules = get_remote_hashtag_rules()
    except Exception:
        rules = []

    lines = [
        '<b>🏷 KPI и хештеги</b>',
        '',
        '<b>#продление</b> — продление времени гостя',
        '<code>#продление Татьяна 15:00-16:00</code>',
        '',
        '<b>#инициатива</b> — полезная инициатива с описанием',
        '<code>#инициатива Помог настроить игру</code>',
        '',
        '<b>#серт</b> — номер от 3000 и сумма',
        '<b>#абик</b> — номер меньше 1000 и сумма',
        '<code>#серт 3123 5000</code>',
        '<code>#абик 512 3000</code>',
        '',
        '<b>#отзывы</b> — количество и источник',
        '<code>#отзывы 2 2ГИС</code>',
    ]

    if rules:
        lines.extend(['', '<b>Начисления OMG Shift</b>'])
        for rule in rules:
            hashtag = escape(str(rule.get('hashtag', '')))
            if rule.get('type') == 'double_hours':
                hint = 'количество часов и описание'
            elif rule.get('type') == 'message_bonus':
                hint = 'сумма и комментарий'
            else:
                hint = 'комментарий'
            lines.append(f'<b>{hashtag}</b> — {hint}')
    else:
        lines.extend([
            '',
            '<i>Список начислений OMG Shift сейчас недоступен.</i>',
        ])

    lines.extend([
        '',
        '<b>#штраф</b> доступен только руководству:',
        '<code>#штраф @логин причина</code>',
        '',
        'Клуб указывать не нужно — бот определит его по смене.',
    ])
    return '\n'.join(lines)


def resource_links_markup():
    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        telebot.types.InlineKeyboardButton('🌐 Открыть OMG Shift', url=OMG_SHIFT_URL)
    )
    links = [
        *GOOGLE_SHEET_LINKS,
        ('🎮 Steam Tracker', steamtracker_url()),
    ]
    for index in range(0, len(links), 2):
        markup.row(*[
            telebot.types.InlineKeyboardButton(title, url=url)
            for title, url in links[index:index + 2]
        ])
    return markup


def help_handler(message, bot):
    if not require_role(message, bot, ROLE_EMPLOYEE):
        return

    if message.text == '⬅️ Вернуться':
        hello(message.chat.id, bot)
        return

    if message.text == '🚀 Быстрый старт':
        text = (
            '<b>🚀 Быстрый старт</b>\n\n'
            '👨🏻‍💻 <b>Смена</b> — открыть или закрыть смену, отправить репорт.\n'
            '🗓 <b>Расписание</b> — посмотреть смены из OMG Shift.\n'
            '🚩 <b>Доска проблем</b> — сообщить о проблеме или проверить задачи.\n'
            '👤 <b>Аккаунт</b> — профиль, синхронизация и статистика.\n'
            '📦 <b>Расходники</b> — проверить или изменить остатки.\n\n'
            'Команды: /start, /weather, /today, /repair и /roll.'
        )
        reply_markup = None
    elif message.text == '🏷 KPI и хештеги':
        text = kpi_help_text()
        reply_markup = None
    elif message.text == '🔗 Сервисы и таблицы':
        text = (
            '<b>🔗 Сервисы и таблицы</b>\n\n'
            'OMG Shift и рабочие Google-таблицы открываются кнопками ниже. '
            'Доступ к таблицам определяется правами Google-аккаунта.'
        )
        reply_markup = resource_links_markup()
    elif message.text == '🛟 Если что-то не работает':
        text = (
            '<b>🛟 Если что-то не работает</b>\n\n'
            '• Перезапусти нужный раздел через главное меню.\n'
            '• Проверь Telegram username и синхронизацию в разделе «Аккаунт».\n'
            '• Для Google-таблиц проверь, что открыт нужный Google-аккаунт.\n'
            '• Ошибку расписания или профиля передай руководителю вместе со скриншотом.\n'
            '• Проблему клуба добавь через «Доска проблем».'
        )
        reply_markup = None
    else:
        help(bot, message)
        return

    sent = bot.send_message(
        message.chat.id,
        text,
        parse_mode='HTML',
        reply_markup=reply_markup,
    )
    bot.register_next_step_handler(sent, help_handler, bot)
