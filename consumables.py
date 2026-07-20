import sqlite3
import pygsheets
from telebot import types
from constants import CHATS, clublist_task
from datetime import datetime, timedelta
import pytz

# Гугл док
KEY_FILE = 'key/omgbot-430116-e9a4d9c69b7f.json'
SHEET_NAME = 'Расходники'

def consumables_menu(message, bot):
    from admin_panel import get_allowed_clubs
    allowed_clubs = get_allowed_clubs()
    
    if not allowed_clubs:
        bot.send_message(message.chat.id, "Нет доступных клубов для просмотра расходников.")
        from menu import hello
        hello(message.chat.id, bot)
        return

    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(*allowed_clubs, '⬅️ Вернуться')
    msg = bot.send_message(message.chat.id, "Выбери клуб, чтобы посмотреть или обновить остатки:", reply_markup=markup)
    bot.register_next_step_handler(msg, c_select_club, bot)

def c_select_club(message, bot):
    if message.text == '⬅️ Вернуться':
        from menu import hello
        hello(message.chat.id, bot)
        return

    club = message.text
    if club not in clublist_task:
        consumables_menu(message, bot)
        return

    show_club_items(message.chat.id, club, bot)

def show_club_items(chat_id, club, bot):
    conn = sqlite3.connect('db/omgbot.sql')
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM consumables WHERE club=?", (club,))
    items = cur.fetchall()
    cur.close()
    conn.close()

    if not items:
        bot.send_message(chat_id, f"В клубе {club} пока нет заведенных расходников.")
        from menu import hello
        hello(chat_id, bot)
        return

    markup = types.InlineKeyboardMarkup()
    text_lines = [f"📦 <b>Остатки в клубе {club}:</b>\n"]

    for item in items:
        status = "🔴 МАЛО" if item['quantity'] <= item['min_limit'] else "🟢"
        text_lines.append(f"{status} <b>{item['name']}</b>: {item['quantity']} шт. (мин: {item['min_limit']})")
        markup.add(types.InlineKeyboardButton(text=f"Изменить {item['name']}", callback_data=f"consedit_{item['id']}"))

    markup.add(types.InlineKeyboardButton(text="⬅️ Закрыть", callback_data="cons_close"))
    bot.send_message(chat_id, "\n".join(text_lines), reply_markup=markup, parse_mode='HTML')
    bot.send_message(chat_id, "Выбери расходник для обновления количества 👆", reply_markup=types.ReplyKeyboardRemove())

def register_consumables_callbacks(bot):
    @bot.callback_query_handler(func=lambda call: call.data.startswith('cons'))
    def cons_callback(call):
        try:
            bot.answer_callback_query(call.id)
            data = call.data

            if data == "cons_close":
                bot.delete_message(call.message.chat.id, call.message.id)
                from menu import hello
                hello(call.message.chat.id, bot)
                return

            if data.startswith("consedit_"):
                item_id = int(data.split('_')[1])

                conn = sqlite3.connect('db/omgbot.sql')
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute("SELECT name, club, quantity FROM consumables WHERE id=?", (item_id,))
                item = cur.fetchone()
                cur.close()
                conn.close()

                if not item:
                    bot.send_message(call.message.chat.id, "Расходник не найден.")
                    return

                bot.delete_message(call.message.chat.id, call.message.id)
                markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
                markup.add('Отмена')
                msg = bot.send_message(call.message.chat.id, f"Введи новое количество для <b>{item['name']}</b> (сейчас {item['quantity']} шт.):", parse_mode='HTML', reply_markup=markup)
                bot.register_next_step_handler(msg, c_save_qty, item_id, item['name'], item['club'], bot)

        except Exception as e:
            print(f"Ошибка колбэка расходников: {e}")

def c_save_qty(message, item_id, item_name, club, bot):
    if message.text == 'Отмена':
        show_club_items(message.chat.id, club, bot)
        return

    if not message.text.isdigit():
        msg = bot.send_message(message.chat.id, "Нужно ввести целое число! Попробуй еще раз:")
        bot.register_next_step_handler(msg, c_save_qty, item_id, item_name, club, bot)
        return

    new_qty = int(message.text)
    user_name = f"@{message.from_user.username}" if message.from_user.username else message.from_user.first_name
    now_time = datetime.now(pytz.timezone('Europe/Moscow')).strftime('%Y-%m-%d %H:%M:%S')

    conn = sqlite3.connect('db/omgbot.sql')
    cur = conn.cursor()
    
    # Получаем старое количество и лимит перед обновлением
    cur.execute("SELECT quantity, min_limit FROM consumables WHERE id=?", (item_id,))
    row = cur.fetchone()
    old_qty = row[0]
    min_limit = row[1]

    # Обновляем количество
    cur.execute("UPDATE consumables SET quantity=? WHERE id=?", (new_qty, item_id))

    # Записываем шаг в историю
    cur.execute('''
        INSERT INTO consumables_history (item_id, club, name, user_name, old_qty, new_qty, updated_at) 
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (item_id, club, item_name, user_name, old_qty, new_qty, now_time))

    conn.commit()
    cur.close()
    conn.close()

    bot.send_message(message.chat.id, f"✅ Количество <b>{item_name}</b> обновлено: было {old_qty}, стало {new_qty} шт.", parse_mode='HTML')

    # Проверка на минимальный остаток
    if new_qty <= min_limit:
        alert_text = f"⚠️ <b>ВНИМАНИЕ! Заканчивается расходник!</b>\nКлуб: {club}\nПозиция: {item_name}\nОстаток: {new_qty} шт. (Мин: {min_limit})"
        bot.send_message(CHATS['reports'], alert_text, parse_mode='HTML')

    bot.send_message(message.chat.id, "⏳ Синхронизирую с Google Таблицей...")
    sync_res = sync_consumables_to_sheets()
    if "❌" in sync_res:
        bot.send_message(message.chat.id, sync_res)

    show_club_items(message.chat.id, club, bot)

def sync_consumables_to_sheets():
    try:
        gc = pygsheets.authorize(service_file=KEY_FILE)
        sh = gc.open(SHEET_NAME)
    except Exception as e:
        return f"❌ Ошибка гугла: {e}"

    conn = sqlite3.connect('db/omgbot.sql')
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    # Получаем остатки
    cur.execute("SELECT * FROM consumables")
    all_items = cur.fetchall()
    
    # Получаем историю (последние 1000 записей, чтобы не перегружать таблицу)
    cur.execute("SELECT * FROM consumables_history ORDER BY updated_at DESC LIMIT 1000")
    history_items = cur.fetchall()
    
    cur.close()
    conn.close()

    # --- 1. Обновляем листы клубов ---
    clubs_data = {}
    for row in all_items:
        c = row['club']
        if c not in clubs_data:
            clubs_data[c] = []
        clubs_data[c].append(row)

    for club, items in clubs_data.items():
        try:
            wks = sh.worksheet_by_title(club)
        except pygsheets.WorksheetNotFound:
            wks = sh.add_worksheet(title=club, rows=100, cols=10)

        matrix = [["ID", "Название", "Остаток", "Минимум", "Статус"]]
        for it in items:
            status = "🚨 НИЖЕ МИНИМУМА" if it['quantity'] <= it['min_limit'] else "✅ НОРМА"
            matrix.append([it['id'], it['name'], it['quantity'], it['min_limit'], status])

        wks.clear(start='A1')
        wks.update_values(crange='A1', values=matrix)

    # --- 2. Обновляем лист Истории ---
    try:
        wks_hist = sh.worksheet_by_title("История")
    except pygsheets.WorksheetNotFound:
        wks_hist = sh.add_worksheet(title="История", rows=1000, cols=6)

    hist_matrix = [["Дата и Время", "Сотрудник", "Клуб", "Расходник", "Было", "Стало", "Разница"]]
    for h in history_items:
        diff = h['new_qty'] - h['old_qty']
        diff_str = f"+{diff}" if diff > 0 else str(diff)
        hist_matrix.append([
            h['updated_at'], 
            h['user_name'], 
            h['club'], 
            h['name'], 
            h['old_qty'], 
            h['new_qty'], 
            diff_str
        ])

    wks_hist.clear(start='A1')
    wks_hist.update_values(crange='A1', values=hist_matrix)

    return "✅"

def auto_consumables_report(bot, target_chat_id):
    """Еженедельный отчет: только то, что заканчивается или достигло минимума"""
    conn = sqlite3.connect('db/omgbot.sql')
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    # Выбираем все позиции, где остаток <= минимума
    cur.execute("SELECT * FROM consumables WHERE quantity <= min_limit")
    low_stock_items = cur.fetchall()
    cur.close()
    conn.close()

    if not low_stock_items:
        # Можно отправлять сообщение, что всё ок, или вообще ничего не слать
        return

    text = "📦 <b>Еженедельный отчет по расходникам</b>\n\n"
    text += "⚠️ <b>Требуют пополнения:</b>\n"
    
    current_club = ""
    for item in low_stock_items:
        if item['club'] != current_club:
            current_club = item['club']
            text += f"\n<u>{current_club}:</u>\n"
        
        text += f"• <b>{item['name']}</b>: {item['quantity']} шт. (мин: {item['min_limit']})\n"

    text += "\n<i>* Список сформирован на основе текущих остатков.</i>"

    try:
        from constants import CHATS
        bot.send_message(target_chat_id, text, parse_mode='HTML')
    except Exception as e:
        print(f"Ошибка отправки отчета по расходникам: {e}")