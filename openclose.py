import sqlite3
import pytz
import telebot
from telebot import types
from constants import funclist_today, CHATS, tags_main
from datetime import datetime
import threading
from permissions import ROLE_EMPLOYEE, require_role

DB_PATH = 'db/omgbot.sql'
_club_status_dashboard_lock = threading.Lock()


def initialize_club_status_dashboard_schema(db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute(
                '''CREATE TABLE IF NOT EXISTS club_status_dashboard (
                       id INTEGER PRIMARY KEY CHECK (id = 1),
                       message_id INTEGER
                   )'''
            )
            conn.execute(
                '''CREATE TABLE IF NOT EXISTS club_status_updates (
                       club TEXT PRIMARY KEY,
                       changed_at TEXT NOT NULL
                   )'''
            )
            columns = {row[1] for row in conn.execute('PRAGMA table_info(club_status_updates)')}
            if 'active_run_id' not in columns:
                conn.execute('ALTER TABLE club_status_updates ADD COLUMN active_run_id TEXT')
    finally:
        conn.close()


def _club_status_dashboard_text(conn):
    rows = conn.execute(
        '''SELECT c.club, c.status, u.changed_at
             FROM clubs AS c
             LEFT JOIN club_status_updates AS u ON u.club = c.club
            ORDER BY c.club COLLATE NOCASE'''
    ).fetchall()
    now = datetime.now(pytz.timezone('Europe/Moscow')).strftime('%d.%m.%Y %H:%M')
    lines = ['🏢 Статусы клубов', f'Обновлено: {now}', '']
    for club, status, changed_at in rows:
        if status == 'Открыт':
            icon, label = '🟢', 'открыт'
        elif status == 'Подготовка к открытию':
            icon, label = '🟡', 'подготовка к открытию'
        elif status == 'Закрывается':
            icon, label = '🟠', 'закрывается'
        elif status == 'Закрыт':
            icon, label = '🔴', 'закрыт'
        else:
            icon, label = '⚪', 'статус неизвестен'
        changed_label = (
            f'{changed_at[8:10]}.{changed_at[5:7]} {changed_at[11:16]}'
            if changed_at else 'время неизвестно'
        )
        lines.append(f'{icon} {club} — {label} ({changed_label})')
    return '\n'.join(lines)


def _dashboard_message_missing(error):
    text = str(error).lower()
    return 'message to edit not found' in text or 'message_id_invalid' in text


def refresh_club_status_dashboard(bot, db_path=DB_PATH):
    """Обновляет закреп со статусами, не прерывая основной сценарий смены при ошибке."""
    with _club_status_dashboard_lock:
        conn = None
        try:
            initialize_club_status_dashboard_schema(db_path)
            conn = sqlite3.connect(db_path)
            text = _club_status_dashboard_text(conn)
            row = conn.execute(
                'SELECT message_id FROM club_status_dashboard WHERE id=1'
            ).fetchone()
            message_id = row[0] if row else None

            if message_id:
                try:
                    bot.edit_message_text(
                        text=text,
                        chat_id=CHATS['reports'],
                        message_id=message_id,
                    )
                except Exception as error:
                    if 'message is not modified' in str(error).lower():
                        pass
                    elif _dashboard_message_missing(error):
                        message_id = None
                    else:
                        print(f'Ошибка обновления статусов клубов: {error}')
                        return False

            if not message_id:
                message = bot.send_message(CHATS['reports'], text)
                message_id = message.message_id
                with conn:
                    conn.execute(
                        '''INSERT INTO club_status_dashboard (id, message_id)
                           VALUES (1, ?)
                           ON CONFLICT(id) DO UPDATE SET message_id=excluded.message_id''',
                        (message_id,),
                    )

            try:
                bot.pin_chat_message(
                    CHATS['reports'],
                    message_id,
                    disable_notification=True,
                )
            except Exception as error:
                print(f'Ошибка закрепления статусов клубов: {error}')
            return True
        except Exception as error:
            print(f'Ошибка формирования статусов клубов: {error}')
            return False
        finally:
            if conn is not None:
                conn.close()


def _record_club_status_change(cur, club, changed_at):
    cur.execute(
        '''INSERT INTO club_status_updates (club, changed_at)
           VALUES (?, ?)
           ON CONFLICT(club) DO UPDATE SET changed_at=excluded.changed_at, active_run_id=NULL''',
        (club, changed_at),
    )

############################# core openclose

def func_today (message,bot):
    if not require_role(message, bot, ROLE_EMPLOYEE):
        return
    markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add(*funclist_today)
    bot.send_message(message.chat.id, 'О, так ты на смене? Что хочешь сделать?', reply_markup=markup)
    bot.register_next_step_handler(message, func_today_2,bot)

def func_today_2 (message,bot):
    if not require_role(message, bot, ROLE_EMPLOYEE):
        return
    a = message.text
    if a== '✅ Открыть смену' or a == '🚫 Закрыть смену':
        choose_shift_flow(message, a, bot)

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


def choose_shift_flow(message, action, bot):
    if not require_role(message, bot, ROLE_EMPLOYEE):
        return
    from menu import _webapp_url
    action_code = 'open' if action == '✅ Открыть смену' else 'close'
    url = _webapp_url(f'shift-report?action={action_code}')
    markup = types.InlineKeyboardMarkup(row_width=1)
    if url:
        markup.add(types.InlineKeyboardButton(
            '📱 Открыть в приложении', web_app=types.WebAppInfo(url),
        ))
    markup.add(types.InlineKeyboardButton('⬅️ Отмена', callback_data='shift_flow:cancel'))
    bot.send_message(message.chat.id,
                     'Открытие и закрытие смены доступны только в приложении.'
                     if url else 'Приложение временно не настроено. Обратитесь к руководителю.',
                     reply_markup=markup)


def warn_legacy_shift_flow(message, action, bot):
    choose_shift_flow(message, action, bot)


def do_report(message,bot):
    user = require_role(message, bot, ROLE_EMPLOYEE)
    if not user:
        return
    name = user['nick_name'] or user['first_name'] or 'Сотрудник'

    if message.photo:
        if message.text==None:
            text = ""
        else:
            text = f'\n\n{message.text}'
        photo1 = [types.InputMediaPhoto(message.photo[0].file_id, caption=f"🔺 {name} (@{message.from_user.username}) репортит!{text}" )]
        
        bot.send_media_group(CHATS['reports'], media=(photo1))

        func_today(message,bot)

    elif message.text == '⬅️ Вернуться':
        func_today(message,bot)

    else:

        text= message.text
        text_to_send = f'🔺 {name} (@{message.from_user.username}) репортит!\n\n{text}'
        bot.send_message (CHATS['reports'], text_to_send)
        func_today(message,bot)
    
    
def check_club(message, a, bot):
    return choose_shift_flow(message, a, bot)

# --- 2. РОУТЕР (Распределяет на Гео или Ручной ввод) ---
def geo_router(message, a, tooearly, bot):
    return choose_shift_flow(message, a, bot)

# --- 3. АВТО-ПОИСК (Твой старый код, чуть доработанный) ---
def find_club_by_geo(message, a, tooearly, bot):
    return choose_shift_flow(message, a, bot)

# --- 4. РУЧНОЙ ВЫБОР (ТОТ САМЫЙ СКИП) ---
def manual_club_selection(message, a, tooearly, bot):
    return choose_shift_flow(message, a, bot)

def manual_selection_handler(message, a, tooearly, bot):
    return choose_shift_flow(message, a, bot)

# --- 5. ФИНАЛЬНАЯ ЛОГИКА (С ПРОВЕРКОЙ ФЛАГА) ---
def check_club_status_logic(message, a, club, tooearly, is_geo_verified, bot):
    return choose_shift_flow(message, a, bot)

def is_early(message, a, club, is_geo_verified, bot):
    return choose_shift_flow(message, a, bot)

def closeconfirm(message, a, club, is_geo_verified, bot):
    return choose_shift_flow(message, a, bot)

def enter_club(message, a, club, tooearly, is_geo_verified, bot):
    return choose_shift_flow(message, a, bot)


def confirm_enter(message, a, club, tooearly, is_geo_verified, bot):
    return choose_shift_flow(message, a, bot)

def run_step(message, bot, a, club, remaining_questions, answers, photos, start_time, tooearly, expected_type=None, current_q_text=None):
    return choose_shift_flow(message, a, bot)

def finish_report(message, bot, a, club, answers, photos, start_time, tooearly):
    return choose_shift_flow(message, a, bot)


# --- ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ РАСЧЕТА ---


############################# common functions

##### openclose

# schedule module

def send_status_close(club,bot):
   conn=sqlite3.connect('db/omgbot.sql')
   cur = conn.cursor()
   cur.execute("SELECT * FROM clubs WHERE status IN ('Открыт', 'Подготовка к открытию', 'Закрывается') and club=?", (club,))
   clubs = cur.fetchall()
   cur.close()
   conn.close()
   if len(clubs)!=0:
    	bot.send_message(CHATS['reports'], f'Не прислан отчет о закрытии: {club}') #CHATS['reports']
        

def send_status_open(club, bot):
    conn = sqlite3.connect(DB_PATH)
    try:
        has_reports = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='shift_webapp_runs'"
        ).fetchone()
        if has_reports:
            today = datetime.now(pytz.timezone('Europe/Moscow')).date().isoformat()
            arrived = conn.execute(
                "SELECT 1 FROM shift_webapp_runs WHERE club=? AND shift_date=? AND action='open' LIMIT 1",
                (club, today),
            ).fetchone()
            missing = not arrived
        else:
            missing = conn.execute("SELECT 1 FROM clubs WHERE status='Закрыт' AND club=?", (club,)).fetchone()
    finally:
        conn.close()
    if missing:
        bot.send_message(CHATS['main_group'], f'{tags_main}\n{club}: приход сотрудника ещё не зафиксирован.')


def close_club (club,bot):
   conn=sqlite3.connect('db/omgbot.sql')
   cur = conn.cursor()
   cur.execute("UPDATE clubs SET status='Закрыт' WHERE status IN ('Открыт', 'Подготовка к открытию', 'Закрывается') and club=?", (club,))
   rows_affected = cur.rowcount  # Количество измененных строк
   if rows_affected > 0:
       _record_club_status_change(
           cur,
           club,
           datetime.now(pytz.timezone('Europe/Moscow')).strftime('%Y-%m-%d %H:%M:%S'),
       )
   conn.commit()
   cur.close()
   conn.close()

   # Отправляем сообщение только если были изменения (rows_affected > 0)
   if rows_affected > 0:
       refresh_club_status_dashboard(bot)
       bot.send_message(CHATS['main_group'], f'Закрыл {club} за тебя. Да-да, я к тебе обращаюсь 🙄')
