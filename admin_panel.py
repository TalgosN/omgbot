import pygsheets
import json
import os
from telebot import *
import sqlite3
import re
from constants import CHATS, clublist_task


# Путь к ключу (как в твоем sheets.py)
KEY_FILE = 'key/omgbot-430116-e9a4d9c69b7f.json'

def admin_func_handler(message, bot):
    a = message.text
    
    if a == '📢 Рассылки':
        from admin_panel import broadcast_menu
        broadcast_menu(message, bot)
        
    elif a == '⚙️ Обновить настройки':
        handle_update_config(message, bot)
        
    elif a == '⬅️ Вернуться':
        from menu import hello
        hello(message.chat.id, bot)
    
    elif a == '📊 Тест недельного отчета':
        msg = bot.send_message(message.chat.id, "⏳ Собираю данные из Aqsi и считаю динамику за 2 недели...")
        try:
            from finance import auto_weekly_report
            # Запускаем генерацию прямо в этот чат админа
            auto_weekly_report(bot, target_chat_id=message.chat.id)
            bot.delete_message(message.chat.id, msg.message_id)
        except Exception as e:
            bot.edit_message_text(chat_id=message.chat.id, message_id=msg.message_id, text=f"❌ Ошибка генерации: {e}")
            
        from menu import admin_menu
        admin_menu(message, bot)    
    
    elif a == '📦 Расходники (Админ)':
        admin_consumables_menu(message, bot)

    else:
        from menu import admin_menu
        admin_menu(message, bot)

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
    from menu import hello
    hello(message.chat.id, bot)


def sync_config():
    logs = []
    logs.append("🔄 Начинаю синхронизацию (pygsheets)...")

    try:
        # 1. Авторизация
        try:
            gc = pygsheets.authorize(service_file=KEY_FILE)
            sh = gc.open('Виарыч') # Открываем таблицу по имени
        except Exception as e:
            return f"❌ Ошибка подключения к Гуглу: {e}"

        # 2. Загрузка текущего JSON с диска
        try:
            with open('data/clubs.json', 'r', encoding='utf-8') as f:
                clubs_data = json.load(f)
        except FileNotFoundError:
            return "❌ Ошибка: Файл data/clubs.json не найден."

        # --- ОБНОВЛЕНИЕ ТЕГОВ (Вкладка 'Tags') ---
        try:
            wks_tags = sh.worksheet_by_title('Tags')
            # Получаем все записи как список словарей
            tags_records = wks_tags.get_all_records()
            
            count_tags = 0
            for row in tags_records:
                club = row.get('Club')
                tag = row.get('Tag')
                
                # Если такой клуб есть в JSON — обновляем тег
                if club and club in clubs_data:
                    clubs_data[club]['tag'] = tag
                    count_tags += 1
            
            logs.append(f"✅ Теги обновлены: {count_tags} шт.")
        except pygsheets.WorksheetNotFound:
            logs.append("⚠️ Вкладка 'Tags' не найдена.")
        except Exception as e:
            logs.append(f"⚠️ Ошибка в Tags: {e}")

        # --- ОБНОВЛЕНИЕ ВОПРОСОВ (Вкладка 'Questions') ---
        try:
            wks_q = sh.worksheet_by_title('Questions')
            q_records = wks_q.get_all_records()
            
            # Временная структура для сборки: temp_q[club][action][variant] = [список вопросов]
            temp_q = {}
            count_q = 0

            for row in q_records:
                club = row.get('Club')
                action = row.get('Action')
                q_text = row.get('Question')
                q_type = row.get('Type')
                
                # Пропускаем пустые строки
                if not club or not action or not q_text:
                    continue
                count_q += 1
                # Обработка варианта (может прийти как строка "0" или число 0)
                try:
                    variant = int(row.get('Variant', 0))
                except ValueError:
                    variant = 0

                # Строим структуру
                if club not in temp_q: temp_q[club] = {}
                if action not in temp_q[club]: temp_q[club][action] = {}
                if variant not in temp_q[club][action]: temp_q[club][action][variant] = []

                # Добавляем вопрос
                temp_q[club][action][variant].append({
                    "text": q_text,
                    "type": q_type
                })

            # Записываем собранные данные обратно в clubs_data
            for club, actions in temp_q.items():
                if club in clubs_data:
                    # Инициализируем секцию questions если её нет
                    if 'questions' not in clubs_data[club]:
                        clubs_data[club]['questions'] = {}
                    
                    for action, variants_dict in actions.items():
                        # Превращаем словарь вариантов {0: [...], 2: [...]} в список списков [[...], [], [...]]
                        if not variants_dict: continue
                        
                        max_v = max(variants_dict.keys())
                        # Создаем список нужной длины, заполненный пустыми списками
                        questions_list = [[] for _ in range(max_v + 1)]
                        
                        for v_idx, q_list in variants_dict.items():
                            questions_list[v_idx] = q_list
                        
                        clubs_data[club]['questions'][action] = questions_list
            
            logs.append(f"✅ Вопросы успешно обновлены ({count_q} строк).")

        except pygsheets.WorksheetNotFound:
            logs.append("⚠️ Вкладка 'Questions' не найдена.")
        except Exception as e:
            logs.append(f"⚠️ Ошибка в Questions: {e}")

        # 3. Сохранение файла
        with open('data/clubs.json', 'w', encoding='utf-8') as f:
            json.dump(clubs_data, f, ensure_ascii=False, indent=2)
        
        logs.append("💾 Конфиг сохранен на сервере!")
        return "\n".join(logs)

    except Exception as e:
        return f"🔥 Критическая ошибка: {e}"



def broadcast_menu(message, bot):
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add('➕ Создать рассылку', '📋 Текущие рассылки', '⬅️ Назад в админку')
    msg = bot.send_message(message.chat.id, "Раздел управления важными рассылками в канал 💌", reply_markup=markup)
    bot.register_next_step_handler(msg, broadcast_menu_handler, bot)

def broadcast_menu_handler(message, bot):
    a = message.text
    if a == '➕ Создать рассылку':
        bc_add_text(message, bot)
    elif a == '📋 Текущие рассылки':
        bc_show_active(message, bot)
    elif a == '⬅️ Назад в админку':
        from menu import admin_menu
        admin_menu(message, bot)
    else:
        broadcast_menu(message, bot)

def bc_add_text(message, bot):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add('Вернуться')
    text = (
        "Введите текст рассылки. Поддерживаются HTML-теги (нажми на код, чтобы скопировать):\n\n"
        "Жирный:\n<code>&lt;b&gt;текст&lt;/b&gt;</code>\n\n"
        "Курсив:\n<code>&lt;i&gt;текст&lt;/i&gt;</code>\n\n"
        "Ссылка:\n<code>&lt;a href=\"https://твой-сайт.ру\"&gt;Текст ссылки&lt;/a&gt;</code>"
    )
    msg = bot.send_message(message.chat.id, text, parse_mode='HTML', reply_markup=markup)
    bot.register_next_step_handler(msg, bc_save_text, bot)

def bc_save_text(message, bot):
    if message.text == 'Вернуться':
        broadcast_menu(message, bot)
        return
    if not message.text:
        bot.send_message(message.chat.id, "Текст не может быть пустым!")
        bc_add_text(message, bot)
        return
    
    text = message.text
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add('Без фoтo', 'Вернуться')
    msg = bot.send_message(message.chat.id, "Прикрепите фото или нажмите кнопку 'Без фoтo'", reply_markup=markup)
    bot.register_next_step_handler(msg, bc_save_photo, text, bot)

def bc_save_photo(message, text, bot):
    if message.text == 'Вернуться':
        broadcast_menu(message, bot)
        return
    
    photo_id = "None"
    if message.photo:
        photo_id = message.photo[-1].file_id
    elif message.text != 'Без фoтo':
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
        markup.add('Без фoтo', 'Вернуться')
        msg = bot.send_message(message.chat.id, "Пожалуйста, отправьте фото или нажмите кнопку", reply_markup=markup)
        bot.register_next_step_handler(msg, bc_save_photo, text, bot)
        return
        
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add('10:00', '15:00', '21:00', 'Вернуться')
    msg = bot.send_message(message.chat.id, "Введите время отправки в формате ЧЧ:ММ (например, 14:30)", reply_markup=markup)
    bot.register_next_step_handler(msg, bc_save_time, text, photo_id, bot)

def bc_save_time(message, text, photo_id, bot):
    if message.text == 'Вернуться':
        broadcast_menu(message, bot)
        return
    
    time_str = message.text.strip()
    if not re.match(r'^\d{2}:\d{2}$', time_str):
        bot.send_message(message.chat.id, "❌ Неверный формат времени! Напишите строго ЧЧ:ММ (например, 09:15).")
        bot.register_next_step_handler(message, bc_save_time, text, photo_id, bot)
        return
        
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add('Однократно', 'Каждый день', 'Вернуться')
    msg = bot.send_message(message.chat.id, "Выберите частоту выполнения рассылки", reply_markup=markup)
    bot.register_next_step_handler(msg, bc_save_freq, text, photo_id, time_str, bot)

def bc_save_freq(message, text, photo_id, time_str, bot):
    if message.text == 'Вернуться':
        broadcast_menu(message, bot)
        return
    
    if message.text == 'Однократно':
        freq = 0
    elif message.text == 'Каждый день':
        freq = 1
    else:
        bot.send_message(message.chat.id, "Пожалуйста, используйте кнопки на клавиатуре.")
        bot.register_next_step_handler(message, bc_save_freq, text, photo_id, time_str, bot)
        return
        
    try:
        conn = sqlite3.connect('db/omgbot.sql')
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO broadcasts (text, photo, trep, freq, status) VALUES (?, ?, ?, ?, ?)",
            (text, photo_id, time_str, freq, 1)
        )
        conn.commit()
        cur.close()
        conn.close()
        bot.send_message(message.chat.id, "✅ Важная рассылка успешно создана и добавлена в расписание!")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Ошибка сохранения в базу данных: {e}")
        
    broadcast_menu(message, bot)

def bc_show_active(message, bot):
    try:
        conn = sqlite3.connect('db/omgbot.sql')
        cur = conn.cursor()
        cur.execute("SELECT ID, text, trep, freq, status FROM broadcasts")
        broadcasts = cur.fetchall()
        cur.close()
        conn.close()
    except Exception as e:
        bot.send_message(message.chat.id, f"Ошибка загрузки: {e}")
        broadcast_menu(message, bot)
        return

    if not broadcasts:
        bot.send_message(message.chat.id, "Запланированных рассылок пока нет.")
        broadcast_menu(message, bot)
        return

    markup = types.InlineKeyboardMarkup()
    text_lines = ["📋 <b>Список всех запланированных рассылок:</b>\n\n"]
    
    for b_id, b_text, b_time, b_freq, b_status in broadcasts:
        freq_label = "🗓 Ежедневно" if b_freq == 1 else "⏱ Однократно"
        status_label = "🟢 Активна" if b_status == 1 else "⏸ На паузе"

        # Вырезаем все HTML-теги из текста через регулярное выражение только для превью
        clean_text = re.sub(r'<[^>]+>', '', b_text)

        # На всякий случай экранируем спецсимволы, чтобы они не сломали разметку меню
        clean_text = clean_text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        
        preview = clean_text[:25] + "..." if len(clean_text) > 25 else clean_text
        
        text_lines.append(f"<b># {b_id}</b> | {b_time} | {freq_label} | {status_label}\n💬 <i>{preview}</i>\n\n")
        markup.add(types.InlineKeyboardButton(text=f"Управлять #{b_id} ({b_time})", callback_data=f"bc_manage_{b_id}"))
        
    markup.add(types.InlineKeyboardButton(text="⬅️ Закрыть список", callback_data="bc_back"))
    bot.send_message(message.chat.id, "".join(text_lines), reply_markup=markup, parse_mode='HTML')
    bot.send_message(message.chat.id, "Выберите нужную рассылку 👆", reply_markup=types.ReplyKeyboardRemove())

def bc_view_card(message, b_id, bot):
    try:
        conn = sqlite3.connect('db/omgbot.sql')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM broadcasts WHERE ID=?", (b_id,))
        b = cur.fetchone()
        cur.close()
        conn.close()
    except Exception as e:
        bot.send_message(message.chat.id, f"Ошибка карточки: {e}")
        return

    if not b:
        bot.send_message(message.chat.id, "Рассылка не найдена.")
        broadcast_menu(message, bot)
        return

    freq_label = "🗓 Ежедневно" if b['freq'] == 1 else "⏱ Однократно"
    status_label = "🟢 Активна" if b['status'] == 1 else "⏸ На паузе"
    toggle_btn_text = "⏸ Поставить на паузу" if b['status'] == 1 else "▶️ Активировать"

    card_text = (
        f"📢 <b>Управление рассылкой #{b_id}</b>\n\n"
        f"<b>Время старта:</b> {b['trep']}\n"
        f"<b>Повторение:</b> {freq_label}\n"
        f"<b>Статус:</b> {status_label}\n\n"
        f"<b>Текст сообщения:</b>\n{b['text']}"
    )

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(text=toggle_btn_text, callback_data=f"bc_toggle_{b_id}"))
    markup.add(types.InlineKeyboardButton(text="🗑 Полностью удалить", callback_data=f"bc_delete_{b_id}"))
    markup.add(
        types.InlineKeyboardButton(text="✏️ Изменить текст", callback_data=f"bc_edittxt_{b_id}"),
        types.InlineKeyboardButton(text="🕒 Изменить время", callback_data=f"bc_edittime_{b_id}"))
    markup.add(types.InlineKeyboardButton(text="⬅️ Вернуться к списку", callback_data=f"bc_back_list"))

    if b['photo'] and b['photo'] != "None":
        bot.send_photo(message.chat.id, photo=b['photo'], caption=card_text, reply_markup=markup, parse_mode='HTML')
    else:
        bot.send_message(message.chat.id, card_text, reply_markup=markup, parse_mode='HTML')

    bot.send_message(message.chat.id, "Выберите действие 👆", reply_markup=types.ReplyKeyboardRemove())
    
def register_broadcast_callbacks(bot):
    @bot.callback_query_handler(func=lambda call: call.data.startswith('bc_'))
    def bc_callback(call):
        try:
            bot.answer_callback_query(call.id)
            data = call.data[3:]
            
            if data == "back":
                bot.delete_message(call.message.chat.id, call.message.id)
                broadcast_menu(call.message, bot)
                return
                
            if data == "back_list":
                bot.delete_message(call.message.chat.id, call.message.id)
                bc_show_active(call.message, bot)
                return
            
            if data.startswith("toggle_"):
                b_id = int(data.split("_")[1])
                conn = sqlite3.connect('db/omgbot.sql')
                cur = conn.cursor()
                cur.execute("SELECT status FROM broadcasts WHERE ID=?", (b_id,))
                res = cur.fetchone()
                if res:
                    new_status = 0 if res[0] == 1 else 1
                    cur.execute("UPDATE broadcasts SET status=? WHERE ID=?", (new_status, b_id))
                    conn.commit()
                cur.close()
                conn.close()
                bot.delete_message(call.message.chat.id, call.message.id)
                bc_view_card(call.message, b_id, bot)
                return
                
            if data.startswith("delete_"):
                b_id = int(data.split("_")[1])
                conn = sqlite3.connect('db/omgbot.sql')
                cur = conn.cursor()
                cur.execute("DELETE FROM broadcasts WHERE ID=?", (b_id,))
                conn.commit()
                cur.close()
                conn.close()
                bot.delete_message(call.message.chat.id, call.message.id)
                bot.send_message(call.message.chat.id, "🗑 Запись о рассылке полностью стерта.")
                broadcast_menu(call.message, bot)
                return
            
            if data.startswith("manage_"):
                b_id = int(data.split("_")[1])
                bot.delete_message(call.message.chat.id, call.message.id)
                bc_view_card(call.message, b_id, bot)
                return
            
            if data.startswith("edittxt_"):
                b_id = int(data.split("_")[1])
                bot.delete_message(call.message.chat.id, call.message.id)
                
                markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
                markup.add('Вернуться')
                msg = bot.send_message(call.message.chat.id, "Введите новый текст рассылки", reply_markup=markup)
                bot.register_next_step_handler(msg, bc_save_new_text, b_id, bot)
                return

            if data.startswith("edittime_"):
                b_id = int(data.split("_")[1])
                bot.delete_message(call.message.chat.id, call.message.id)
                
                markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
                markup.add('Вернуться')
                msg = bot.send_message(call.message.chat.id, "Введите новое время в формате ЧЧ:ММ", reply_markup=markup)
                bot.register_next_step_handler(msg, bc_save_new_time, b_id, bot)
                return   
            
        except Exception as e:
            print(f"Ошибка колбэка рассылок: {e}")

def bc_save_new_text(message, b_id, bot):
    if message.text == 'Вернуться':
        bc_view_card(message, b_id, bot)
        return
    
    conn = sqlite3.connect('db/omgbot.sql')
    cur = conn.cursor()
    cur.execute("UPDATE broadcasts SET text=? WHERE ID=?", (message.text, b_id))
    conn.commit()
    cur.close()
    conn.close()
    
    bot.send_message(message.chat.id, "✅ Текст рассылки успешно обновлен!")
    bc_view_card(message, b_id, bot)

def bc_save_new_time(message, b_id, bot):
    if message.text == 'Вернуться':
        bc_view_card(message, b_id, bot)
        return
        
    time_str = message.text.strip()
    if not re.match(r'^\d{2}:\d{2}$', time_str):
        msg = bot.send_message(message.chat.id, "❌ Неверный формат! Введите строго ЧЧ:ММ (например, 16:45)")
        bot.register_next_step_handler(msg, bc_save_new_time, b_id, bot)
        return
        
    conn = sqlite3.connect('db/omgbot.sql')
    cur = conn.cursor()
    cur.execute("UPDATE broadcasts SET trep=? WHERE ID=?", (time_str, b_id))
    conn.commit()
    cur.close()
    conn.close()
    
    bot.send_message(message.chat.id, "✅ Время отправки обновлено!")
    bc_view_card(message, b_id, bot)


###### Модуль расходников

def get_allowed_clubs():
    """Динамически загружает конфиг и оставляет только клубы с require_geo = True"""
    try:
        from sheets import get_clubs
        current_clubs = get_clubs()
        return [club for club in current_clubs if current_clubs[club].get('require_geo', False)]
    except Exception as e:
        print(f"Ошибка чтения require_geo из конфига: {e}")
        # Если конфиг пуст или сломался — возвращаем пустой список во избежание падения
        return []

def admin_consumables_menu(message, bot):
    """Главное меню управления расходниками для админа"""
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add('➕ Добавить расходник', '📋 Управление расходниками', '⬅️ Назад в админку')
    
    # Получаем объект сообщения (поддержка вызова из разных контекстов)
    chat_id = message.chat.id if hasattr(message, 'chat') else message
    msg = bot.send_message(chat_id, "Управление расходниками (Панель Администратора):", reply_markup=markup)
    bot.register_next_step_handler(msg, admin_consumables_handler, bot)

def admin_consumables_handler(message, bot):
    a = message.text
    if a == '➕ Добавить расходник':
        ac_select_club_for_add(message, bot)
    elif a == '📋 Управление расходниками':
        ac_select_club_for_manage(message, bot)
    elif a == '⬅️ Назад в админку':
        from menu import admin_menu
        admin_menu(message, bot)
    else:
        admin_consumables_menu(message, bot)

# --- БЛОК ДОБАВЛЕНИЯ НОВОЙ ПОЗИЦИИ ---

def ac_select_club_for_add(message, bot):
    allowed_clubs = get_allowed_clubs()
    if not allowed_clubs:
        bot.send_message(message.chat.id, "В конфиге нет доступных клубов с require_geo: true!")
        admin_consumables_menu(message, bot)
        return
        
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(*allowed_clubs, 'Отмена')
    msg = bot.send_message(message.chat.id, "Выберите клуб для добавления нового расходника:", reply_markup=markup)
    bot.register_next_step_handler(msg, ac_get_name, bot)

def ac_get_name(message, bot):
    if message.text == 'Отмена':
        admin_consumables_menu(message, bot)
        return
        
    club = message.text
    if club not in get_allowed_clubs():
        bot.send_message(message.chat.id, "Неверный клуб. Используйте клавиатуру.")
        ac_select_club_for_add(message, bot)
        return
        
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add('Отмена')
    msg = bot.send_message(message.chat.id, f"Выбран клуб: <b>{club}</b>\n\nВведите название нового расходника:", parse_mode='HTML', reply_markup=markup)
    bot.register_next_step_handler(msg, ac_get_limit, club, bot)

def ac_get_limit(message, club, bot):
    if message.text == 'Отмена':
        admin_consumables_menu(message, bot)
        return
        
    item_name = message.text.strip()
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add('Отмена')
    msg = bot.send_message(message.chat.id, f"Расходник: <b>{item_name}</b>\n\nВведите минимальный лимит (число):", parse_mode='HTML', reply_markup=markup)
    bot.register_next_step_handler(msg, ac_save_item, club, item_name, bot)

def ac_save_item(message, club, item_name, bot):
    if message.text == 'Отмена':
        admin_consumables_menu(message, bot)
        return
        
    if not message.text.isdigit():
        msg = bot.send_message(message.chat.id, "Лимит должен быть числом! Введите еще раз:")
        bot.register_next_step_handler(msg, ac_save_item, club, item_name, bot)
        return
        
    min_limit = int(message.text)
    
    try:
        conn = sqlite3.connect('db/omgbot.sql')
        cur = conn.cursor()
        cur.execute("SELECT id FROM consumables WHERE club=? AND name=?", (club, item_name))
        if cur.fetchone():
            bot.send_message(message.chat.id, f"❌ Позиция {item_name} уже заведена в этом клубе.")
        else:
            cur.execute("INSERT INTO consumables (club, name, quantity, min_limit) VALUES (?, ?, 0, ?)", (club, item_name, min_limit))
            conn.commit()
            bot.send_message(message.chat.id, f"✅ Расходник {item_name} успешно добавлен в базу.")
            try:
                from consumables import sync_consumables_to_sheets
                sync_consumables_to_sheets()
            except: pass
        cur.close()
        conn.close()
    except Exception as e:
        bot.send_message(message.chat.id, f"Ошибка БД: {e}")
        
    admin_consumables_menu(message, bot)

# --- БЛОК ПРОСМОТРА И КАРТОЧЕК ---

def ac_select_club_for_manage(message, bot):
    allowed_clubs = get_allowed_clubs()
    if not allowed_clubs:
        bot.send_message(message.chat.id, "В конфиге нет доступных клубов с require_geo: true!")
        admin_consumables_menu(message, bot)
        return
        
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(*allowed_clubs, 'Отмена')
    msg = bot.send_message(message.chat.id, "Выберите клуб для просмотра списка остатков:", reply_markup=markup)
    bot.register_next_step_handler(msg, ac_load_club_items, bot)

def ac_load_club_items(message, bot):
    if message.text == 'Отмена':
        admin_consumables_menu(message, bot)
        return
        
    club = message.text
    if club not in get_allowed_clubs():
        bot.send_message(message.chat.id, "Неверный клуб. Используйте клавиатуру.")
        ac_select_club_for_manage(message, bot)
        return
        
    admin_show_club_items(message.chat.id, club, bot)

def admin_show_club_items(chat_id, club, bot):
    conn = sqlite3.connect('db/omgbot.sql')
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM consumables WHERE club=?", (club,))
    items = cur.fetchall()
    cur.close()
    conn.close()

    if not items:
        bot.send_message(chat_id, f"В клубе {club} пока нет заведенных расходников.")
        admin_consumables_menu(chat_id, bot)
        return

    markup = types.InlineKeyboardMarkup()
    text_lines = [f"📋 <b>Расходники клуба {club} (Администрирование):</b>\n"]

    for item in items:
        status = "sub" if item['quantity'] <= item['min_limit'] else "ok"
        status_label = "🔴 МАЛО" if status == "sub" else "🟢"
        text_lines.append(f"{status_label} <b>{item['name']}</b>: {item['quantity']} шт. (минимум: {item['min_limit']})")
        markup.add(types.InlineKeyboardButton(text=f"⚙️ Управление {item['name']}", callback_data=f"admcons_view_{item['id']}"))

    markup.add(types.InlineKeyboardButton(text="⬅️ Сменить клуб", callback_data="admcons_backclubs"))
    bot.send_message(chat_id, "\n".join(text_lines), reply_markup=markup, parse_mode='HTML')
    bot.send_message(chat_id, "Выберите позицию для изменения параметров 👆", reply_markup=types.ReplyKeyboardRemove())

def admin_view_item_card(chat_id, item_id, bot):
    """Генерация карточки конкретного расходника с кнопками управления"""
    conn = sqlite3.connect('db/omgbot.sql')
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM consumables WHERE id=?", (item_id,))
    item = cur.fetchone()
    cur.close()
    conn.close()

    if not item:
        bot.send_message(chat_id, "Позиция не найдена в базе данных.")
        admin_consumables_menu(chat_id, bot)
        return

    status_label = "🚨 ТРЕБУЕТСЯ ПОПОЛНЕНИЕ" if item['quantity'] <= item['min_limit'] else "✅ В ПРЕДЕЛАХ НОРМЫ"
    card_text = (
        f"📦 <b>Карточка расходника #{item['id']}</b>\n\n"
        f"📍 <b>Клуб:</b> {item['club']}\n"
        f"🏷 <b>Название:</b> {item['name']}\n"
        f"🔢 <b>Текущее количество:</b> {item['quantity']} шт.\n"
        f"📉 <b>Минимальный порог:</b> {item['min_limit']} шт.\n"
        f"📊 <b>Состояние:</b> {status_label}"
    )

    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton(text="✏️ Изменить остаток", callback_data=f"admcons_editqty_{item_id}"),
        types.InlineKeyboardButton(text="📉 Изменить лимит", callback_data=f"admcons_editmin_{item_id}")
    )
    markup.add(types.InlineKeyboardButton(text="🗑 Удалить расходник", callback_data=f"admcons_del_{item_id}"))
    markup.add(types.InlineKeyboardButton(text="⬅️ Вернуться к списку", callback_data=f"admcons_backto_{item['club']}"))

    bot.send_message(chat_id, card_text, reply_markup=markup, parse_mode='HTML')

# --- СОХРАНЕНИЕ И ОБРАБОТЧИКИ ОПЕРАЦИЙ ---

def admcons_save_qty(message, item_id, bot):
    conn = sqlite3.connect('db/omgbot.sql')
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM consumables WHERE id=?", (item_id,))
    item = cur.fetchone()
    
    if message.text == 'Отмена' or not item:
        cur.close()
        conn.close()
        if item: admin_view_item_card(message.chat.id, item_id, bot)
        else: admin_consumables_menu(message, bot)
        return

    if not message.text.isdigit():
        cur.close()
        conn.close()
        msg = bot.send_message(message.chat.id, "Ошибка! Введите целое число:")
        bot.register_next_step_handler(msg, admcons_save_qty, item_id, bot)
        return

    new_qty = int(message.text)
    old_qty = item['quantity']
    user_name = f"@{message.from_user.username}" if message.from_user.username else message.from_user.first_name
    
    from datetime import datetime
    import pytz
    now_time = datetime.now(pytz.timezone('Europe/Moscow')).strftime('%Y-%m-%d %H:%M:%S')

    cur.execute("UPDATE consumables SET quantity=? WHERE id=?", (new_qty, item_id))
    cur.execute('''
        INSERT INTO consumables_history (item_id, club, name, user_name, old_qty, new_qty, updated_at) 
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (item_id, item['club'], item['name'], f"{user_name} (Admin)", old_qty, new_qty, now_time))
    
    conn.commit()
    cur.close()
    conn.close()

    bot.send_message(message.chat.id, "✅ Текущий остаток успешно изменен.")
    try:
        from consumables import sync_consumables_to_sheets
        sync_consumables_to_sheets()
    except: pass
    admin_view_item_card(message.chat.id, item_id, bot)

def admcons_save_min(message, item_id, bot):
    conn = sqlite3.connect('db/omgbot.sql')
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM consumables WHERE id=?", (item_id,))
    item = cur.fetchone()
    
    if message.text == 'Отмена' or not item:
        cur.close()
        conn.close()
        if item: admin_view_item_card(message.chat.id, item_id, bot)
        else: admin_consumables_menu(message, bot)
        return

    if not message.text.isdigit():
        cur.close()
        conn.close()
        msg = bot.send_message(message.chat.id, "Ошибка! Введите число:")
        bot.register_next_step_handler(msg, admcons_save_min, item_id, bot)
        return

    new_min = int(message.text)
    cur.execute("UPDATE consumables SET min_limit=? WHERE id=?", (new_min, item_id))
    conn.commit()
    cur.close()
    conn.close()

    bot.send_message(message.chat.id, "✅ Минимальный порог обновлен.")
    try:
        from consumables import sync_consumables_to_sheets
        sync_consumables_to_sheets()
    except: pass
    admin_view_item_card(message.chat.id, item_id, bot)

def register_admin_consumables_callbacks(bot):
    @bot.callback_query_handler(func=lambda call: call.data.startswith('admcons_'))
    def admcons_callback(call):
        try:
            bot.answer_callback_query(call.id)
            data = call.data[8:]

            if data == "backclubs":
                bot.delete_message(call.message.chat.id, call.message.id)
                admin_consumables_menu(call.message, bot)
                return

            if data.startswith("view_"):
                item_id = int(data.split('_')[2])
                bot.delete_message(call.message.chat.id, call.message.id)
                admin_view_item_card(call.message.chat.id, item_id, bot)
                return

            if data.startswith("editqty_"):
                item_id = int(data.split('_')[2])
                bot.delete_message(call.message.chat.id, call.message.id)
                markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
                markup.add('Отмена')
                msg = bot.send_message(call.message.chat.id, "Укажите новое текущее количество расходника на складе:", reply_markup=markup)
                bot.register_next_step_handler(msg, admcons_save_qty, item_id, bot)
                return

            if data.startswith("editmin_"):
                item_id = int(data.split('_')[2])
                bot.delete_message(call.message.chat.id, call.message.id)
                markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
                markup.add('Отмена')
                msg = bot.send_message(call.message.chat.id, "Укажите новый минимальный лимит для уведомлений менеджера:", reply_markup=markup)
                bot.register_next_step_handler(msg, admcons_save_min, item_id, bot)
                return

            if data.startswith("del_"):
                item_id = int(data.split('_')[2])
                bot.delete_message(call.message.chat.id, call.message.id)
                
                conn = sqlite3.connect('db/omgbot.sql')
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute("SELECT club FROM consumables WHERE id=?", (item_id,))
                row = cur.fetchone()
                if row:
                    club_name = row['club']
                    cur.execute("DELETE FROM consumables WHERE id=?", (item_id,))
                    conn.commit()
                    bot.send_message(call.message.chat.id, "🗑 Позиция полностью удалена из базы данных.")
                    try:
                        from consumables import sync_consumables_to_sheets
                        sync_consumables_to_sheets()
                    except: pass
                    admin_show_club_items(call.message.chat.id, club_name, bot)
                else:
                    admin_consumables_menu(call.message, bot)
                cur.close()
                conn.close()
                return

            if data.startswith("backto_"):
                club = data.split('_')[2]
                bot.delete_message(call.message.chat.id, call.message.id)
                admin_show_club_items(call.message.chat.id, club, bot)
                return

        except Exception as e:
            print(f"Ошибка колбэка админ-расходников: {e}")
              
# Для теста запуска напрямую
if __name__ == "__main__":
    print(sync_config())