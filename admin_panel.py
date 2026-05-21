import pygsheets
import json
import os
from telebot import *
import sqlite3
import re
from constants import CHATS


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
    msg = bot.send_message(message.chat.id, "Введите текст рассылки (поддерживаются HTML-теги <b> </b>, <i> </i>)", reply_markup=markup)
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
        preview = b_text[:25] + "..." if len(b_text) > 25 else b_text
        
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

# Для теста запуска напрямую
if __name__ == "__main__":
    print(sync_config())