import pygsheets
import html
import json
import os
from telebot import *
import sqlite3
import re
import threading
from datetime import datetime
import requests
import pytz
from constants import CHATS, clublist_task, SHIFTON_API_URL, SHIFTON_API_TOKEN, validate_config
from sender import safe_send
from permissions import (
    ROLE_BLOCKED,
    ROLE_EMPLOYEE,
    ROLE_MANAGER,
    ROLE_NAMES,
    ROLE_OWNER,
    ROLE_TECHNICIAN,
    change_role,
    require_role,
)

# Путь к ключу (как в твоем sheets.py)
KEY_FILE = 'key/omgbot-430116-e9a4d9c69b7f.json'
temp_broadcasts = {}
health_check_lock = threading.Lock()

def generate_days_keyboard(selected_days=""):
    markup = types.InlineKeyboardMarkup()
    days_map = {'0': 'Пн', '1': 'Вт', '2': 'Ср', '3': 'Чт', '4': 'Пт', '5': 'Сб', '6': 'Вс'}
    
    row = []
    for d_num, name in days_map.items():
        text = f"✅ {name}" if d_num in selected_days else name
        new_days = selected_days.replace(d_num, "") if d_num in selected_days else selected_days + d_num
        row.append(types.InlineKeyboardButton(text=text, callback_data=f"bcfreq_toggle_{new_days}"))
        
        if len(row) == 4:
            markup.add(*row)
            row = []
    if row: markup.add(*row)

    markup.add(
        types.InlineKeyboardButton(text="⏱ Однократно", callback_data="bcfreq_once"),
        types.InlineKeyboardButton(text="🗓 Каждый день", callback_data="bcfreq_daily")
    )
    if selected_days:
        markup.add(types.InlineKeyboardButton(text="💾 Сохранить выбранные дни", callback_data=f"bcfreq_custom_{selected_days}"))
    
    markup.add(types.InlineKeyboardButton(text="❌ Отмена", callback_data="bc_back"))
    return markup

def generate_edit_days_keyboard(b_id, selected_days=""):
    markup = types.InlineKeyboardMarkup()
    days_map = {'0': 'Пн', '1': 'Вт', '2': 'Ср', '3': 'Чт', '4': 'Пт', '5': 'Сб', '6': 'Вс'}
    
    row = []
    for d_num, name in days_map.items():
        text = f"✅ {name}" if d_num in selected_days else name
        new_days = selected_days.replace(d_num, "") if d_num in selected_days else selected_days + d_num
        row.append(types.InlineKeyboardButton(text=text, callback_data=f"bcef_toggle_{b_id}_{new_days}"))
        
        if len(row) == 4:
            markup.add(*row)
            row = []
    if row: markup.add(*row)

    markup.add(
        types.InlineKeyboardButton(text="⏱ Однократно", callback_data=f"bcef_once_{b_id}"),
        types.InlineKeyboardButton(text="🗓 Каждый день", callback_data=f"bcef_daily_{b_id}")
    )
    if selected_days:
        markup.add(types.InlineKeyboardButton(text="💾 Сохранить", callback_data=f"bcef_custom_{b_id}_{selected_days}"))
    
    # Кнопка отмены возвращает обратно в карточку этой же рассылки
    markup.add(types.InlineKeyboardButton(text="❌ Отмена", callback_data=f"bc_manage_{b_id}"))
    return markup

def admin_func_handler(message, bot):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    a = message.text
    
    if a == '📢 Рассылки':
        from admin_panel import broadcast_menu
        broadcast_menu(message, bot)
        
    elif a == '⚙️ Обновить настройки':
        handle_update_config(message, bot)

    elif a == '🩺 Статус систем':
        handle_system_health(message, bot)

    elif a == '📊 KPI сотрудников':
        handle_monthly_kpi_report(message, bot)
        
    elif a == '⬅️ Вернуться':
        from menu import hello
        hello(message.chat.id, bot)
    
    elif a == '📊 Тест недельного отчета':
        if not require_role(message, bot, ROLE_OWNER):
            from menu import admin_menu
            admin_menu(message, bot)
            return
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
  
    elif a == '📦 Тест отчета по расходникам':
            msg = bot.send_message(message.chat.id, "⏳ Анализирую остатки по клубам...")
            try:
                from consumables import auto_consumables_report
                # Вызываем функцию с передачей текущего чата в качестве цели
                auto_consumables_report(bot, target_chat_id=message.chat.id)
                bot.delete_message(message.chat.id, msg.message_id)
            except Exception as e:
                bot.edit_message_text(chat_id=message.chat.id, message_id=msg.message_id, text=f"❌ Ошибка отчета расходников: {e}")
            
            from menu import admin_menu
            admin_menu(message, bot)

    elif a == '📦 Расходники (Админ)':
        admin_consumables_menu(message, bot)

    elif a == '👥 Управление ролями':
        role_management_menu(message, bot)

    else:
        from menu import admin_menu
        admin_menu(message, bot)


def parse_report_number(value):
    normalized = str(value or '').replace('%', '').replace(' ', '').replace(',', '.').strip()
    try:
        return float(normalized)
    except ValueError:
        return 0.0


def build_monthly_kpi_report(values):
    selected_date = values[0][2] if values and len(values[0]) > 2 else 'текущий месяц'
    employees = []
    for row in values[7:]:
        name = str(row[1]).strip() if len(row) > 1 else ''
        shifts = row[2] if len(row) > 2 and row[2] not in ('', None) else '0'
        if not name or parse_report_number(shifts) <= 0:
            continue
        total_pct = row[20] if len(row) > 20 and row[20] not in ('', None) else '0%'
        weighted_pct = row[21] if len(row) > 21 and row[21] not in ('', None) else '0%'
        rank = row[24] if len(row) > 24 and row[24] not in ('', None) else 'нет'
        employees.append({
            'name': name,
            'shifts': shifts,
            'total_pct': total_pct,
            'weighted_pct': weighted_pct,
            'rank': rank,
            'sort_pct': parse_report_number(total_pct),
        })

    employees.sort(key=lambda employee: (employee['sort_pct'], employee['name'].lower()))
    header = (
        '📊 <b>KPI сотрудников за месяц</b>\n'
        f'📅 <i>Расчётная дата: {html.escape(str(selected_date))}</i>\n'
        f'👥 Сотрудников со сменами: <b>{len(employees)}</b>'
    )
    if not employees:
        return [f'{header}\n\nℹ️ За выбранный месяц нет сотрудников со сменами.']

    weakest = employees[:3]

    def employee_line(employee):
        return (
            f'🔴 <b>{html.escape(str(employee["name"]))}</b>: '
            f'KPI <b>{html.escape(str(employee["total_pct"]))}</b> '
            f'<i>({html.escape(str(employee["weighted_pct"]))} взв.)</i> | '
            f'🕒 {html.escape(str(employee["shifts"]))} | '
            f'🥇 {html.escape(str(employee["rank"]))}'
        )

    return [
        f'{header}\n\n🔻 <b>Три самых слабых результата</b>\n\n'
        + '\n'.join(employee_line(employee) for employee in weakest)
    ]


def handle_monthly_kpi_report(message, bot):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    wait_message = bot.send_message(message.chat.id, '⏳ Собираю KPI сотрудников...')
    try:
        client = pygsheets.authorize(service_file=KEY_FILE)
        spreadsheet = client.open('KPI OMG VR')
        values = spreadsheet.worksheet_by_title('Главный').get_values(
            start='A1', end='AA60', returnas='matrix'
        )
        reports = build_monthly_kpi_report(values)
        bot.delete_message(message.chat.id, wait_message.message_id)
        for report in reports:
            bot.send_message(message.chat.id, report, parse_mode='HTML')
    except Exception as exc:
        bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=wait_message.message_id,
            text=f'❌ Не удалось собрать KPI сотрудников: {exc}',
        )
    from menu import admin_menu
    admin_menu(message, bot)


ROLE_BUTTONS = {
    '🚫 -1 · Заблокирован': ROLE_BLOCKED,
    '👤 0 · Сотрудник': ROLE_EMPLOYEE,
    '🛠 1 · Ремонтник': ROLE_TECHNICIAN,
    '🧑🏻‍💻 2 · Менеджер': ROLE_MANAGER,
    '👑 3 · Руководство': ROLE_OWNER,
}


def role_management_menu(message, bot):
    if not require_role(message, bot, ROLE_OWNER):
        return
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add('📜 Последние изменения', '⬅️ Назад в админку')
    msg = bot.send_message(message.chat.id, 'Введите Telegram username сотрудника, например @username:', reply_markup=markup)
    bot.register_next_step_handler(msg, role_select_user, bot)


def role_select_user(message, bot):
    if not require_role(message, bot, ROLE_OWNER):
        return
    if message.text == '⬅️ Назад в админку':
        from menu import admin_menu
        admin_menu(message, bot)
        return
    if message.text == '📜 Последние изменения':
        show_role_audit(message, bot)
        return

    login = str(message.text or '').strip()
    if not login.startswith('@'):
        login = f'@{login}'
    conn = sqlite3.connect('db/omgbot.sql')
    conn.row_factory = sqlite3.Row
    try:
        target = conn.execute(
            'SELECT * FROM users_new WHERE lower(login)=lower(?) ORDER BY ID LIMIT 1',
            (login,),
        ).fetchone()
    finally:
        conn.close()
    if not target:
        bot.send_message(message.chat.id, 'Пользователь не найден.')
        role_management_menu(message, bot)
        return

    current_name = ROLE_NAMES.get(target['status'], 'Не назначена')
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add(*ROLE_BUTTONS.keys(), '⬅️ Назад в админку')
    msg = bot.send_message(
        message.chat.id,
        f'{target["second_name"] or ""} {target["first_name"] or ""} ({target["login"]})\n'
        f'Текущая роль: {target["status"]} · {current_name}\n\nВыберите новую роль:',
        reply_markup=markup,
    )
    bot.register_next_step_handler(msg, role_apply, target['ID'], bot)


def role_apply(message, target_id, bot):
    if not require_role(message, bot, ROLE_OWNER):
        return
    if message.text == '⬅️ Назад в админку':
        from menu import admin_menu
        admin_menu(message, bot)
        return
    if message.text not in ROLE_BUTTONS:
        bot.send_message(message.chat.id, 'Выберите роль с клавиатуры.')
        role_management_menu(message, bot)
        return

    new_status = ROLE_BUTTONS[message.text]
    try:
        target, old_status = change_role(message, target_id, new_status)
    except (PermissionError, ValueError) as e:
        bot.send_message(message.chat.id, str(e))
        role_management_menu(message, bot)
        return

    bot.send_message(
        message.chat.id,
        f'Роль {target.get("login")}: {old_status} → {new_status} ({ROLE_NAMES[new_status]}).',
    )
    if target.get('chatid') and str(target.get('chatid')) != str(message.from_user.id):
        try:
            bot.send_message(target['chatid'], f'Ваша роль изменена: {ROLE_NAMES[new_status]} ({new_status}).')
        except Exception:
            pass
    role_management_menu(message, bot)


def show_role_audit(message, bot):
    if not require_role(message, bot, ROLE_OWNER):
        return
    conn = sqlite3.connect('db/omgbot.sql')
    try:
        rows = conn.execute(
            '''SELECT changed_at, actor_login, actor_chatid, target_login,
                      old_status, new_status
               FROM role_audit ORDER BY id DESC LIMIT 15'''
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        text = 'Журнал изменений ролей пока пуст.'
    else:
        lines = ['📜 Последние изменения ролей:', '']
        for changed_at, actor_login, actor_chatid, target_login, old_status, new_status in rows:
            actor = actor_login or actor_chatid
            lines.append(f'{changed_at}: {actor} → {target_login}: {old_status} → {new_status}')
        text = '\n'.join(lines)
    bot.send_message(message.chat.id, text)
    role_management_menu(message, bot)

def collect_system_health(bot):
    moscow_now = datetime.now(pytz.timezone('Europe/Moscow'))
    lines = [f"🩺 Статус систем на {moscow_now.strftime('%d.%m.%Y %H:%M:%S')}"]

    try:
        me = bot.get_me()
        lines.append(f"✅ Telegram: @{me.username}")
    except Exception as e:
        lines.append(f"❌ Telegram: {str(e)[:120]}")

    try:
        conn = sqlite3.connect('db/omgbot.sql', timeout=5)
        users_count = conn.execute("SELECT COUNT(*) FROM users_new").fetchone()[0]
        conn.close()
        lines.append(f"✅ SQLite: доступна, сотрудников {users_count}")
    except Exception as e:
        lines.append(f"❌ SQLite: {str(e)[:120]}")

    try:
        validate_config()
        lines.append("✅ Конфигурация: обязательные параметры заданы")
    except Exception as e:
        lines.append(f"❌ Конфигурация: {str(e)[:120]}")

    try:
        today = moscow_now.strftime('%Y-%m-%d')
        response = requests.get(
            f"{SHIFTON_API_URL}/api/bot/schedule?date={today}",
            headers={"Authorization": f"Bearer {SHIFTON_API_TOKEN}"},
            timeout=5
        )
        data = response.json()
        if data.get("ok"):
            lines.append("✅ OMG Shift API: доступен")
        else:
            lines.append(f"❌ OMG Shift API: {data.get('error', 'неизвестная ошибка')}")
    except Exception as e:
        lines.append(f"❌ OMG Shift API: {str(e)[:120]}")

    try:
        gc = pygsheets.authorize(service_file=KEY_FILE)
        gc.open('KPI OMG VR')
        lines.append("✅ Google Sheets: доступен")
    except Exception as e:
        lines.append(f"❌ Google Sheets: {str(e)[:120]}")

    scheduler = next((thread for thread in threading.enumerate() if thread.name == "omgbot-scheduler"), None)
    if scheduler and scheduler.is_alive():
        lines.append("✅ Планировщик: работает")
    else:
        lines.append("❌ Планировщик: поток не найден")

    try:
        from rasp import get_shifton_runtime_status
        runtime = get_shifton_runtime_status()
        last_check = runtime.get("last_notification_check") or "ещё не выполнялась"
        last_sync = runtime.get("last_chat_sync") or "ещё не выполнялась"
        lines.append(f"ℹ️ Очередь уведомлений: последняя проверка {last_check}")
        sync_result = runtime.get("last_chat_sync_result") or "результат отсутствует"
        lines.append(f"ℹ️ Синхронизация чатов: {last_sync}, {sync_result}")
        if runtime.get("last_notification_error"):
            lines.append(f"⚠️ Последняя ошибка очереди: {runtime['last_notification_error'][:120]}")
    except Exception as e:
        lines.append(f"⚠️ Состояние уведомлений недоступно: {str(e)[:120]}")

    return "\n".join(lines)

def handle_system_health(message, bot):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    if not health_check_lock.acquire(blocking=False):
        bot.send_message(message.chat.id, "⏳ Проверка систем уже выполняется.")
        return

    msg = bot.send_message(message.chat.id, "⏳ Проверяю Telegram, SQLite, OMG Shift и Google Sheets...")

    def worker():
        try:
            report = collect_system_health(bot)
            bot.edit_message_text(chat_id=message.chat.id, message_id=msg.message_id, text=report)
        except Exception as e:
            bot.edit_message_text(chat_id=message.chat.id, message_id=msg.message_id, text=f"❌ Ошибка проверки систем: {e}")
        finally:
            health_check_lock.release()
            from menu import admin_menu
            admin_menu(message, bot)

    threading.Thread(target=worker, daemon=True).start()

def handle_update_config(message, bot):
    if not require_role(message, bot, ROLE_MANAGER):
        return
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
    if not require_role(message, bot, ROLE_MANAGER):
        return
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add('➕ Создать рассылку', '📋 Текущие рассылки', '⬅️ Назад в админку')
    msg = bot.send_message(message.chat.id, "Раздел управления важными рассылками в канал 💌", reply_markup=markup)
    bot.register_next_step_handler(msg, broadcast_menu_handler, bot)

def broadcast_menu_handler(message, bot):
    if not require_role(message, bot, ROLE_MANAGER):
        return
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
    if not require_role(message, bot, ROLE_MANAGER):
        return
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
    if not require_role(message, bot, ROLE_MANAGER):
        return
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
    if not require_role(message, bot, ROLE_MANAGER):
        return
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
    if not require_role(message, bot, ROLE_MANAGER):
        return
    if message.text == 'Вернуться':
        broadcast_menu(message, bot)
        return
    
    time_str = message.text.strip()
    import re
    if not re.match(r'^\d{2}:\d{2}$', time_str):
        msg = bot.send_message(message.chat.id, "❌ Неверный формат времени! Напишите строго ЧЧ:ММ (например, 09:15).")
        bot.register_next_step_handler(msg, bc_save_time, text, photo_id, bot)
        return
        
    # Сохраняем введенные данные во временный словарь
    temp_broadcasts[message.chat.id] = {'text': text, 'photo': photo_id, 'time': time_str}
    
    # Отправляем новую инлайн-клавиатуру с днями
    bot.send_message(message.chat.id, "Выберите дни недели для рассылки:", reply_markup=generate_days_keyboard(""))


def bc_show_active(message, bot):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    try:
        conn = sqlite3.connect('db/omgbot.sql')
        cur = conn.cursor()
        cur.execute("SELECT ID, text, time, freq_type, freq_days, status FROM broadcasts_new")
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
    
    for b_id, b_text, b_time, b_freq_type, b_freq_days, b_status in broadcasts:
        # Логика расшифровки дней для карточки и списка:
        if b_freq_type == "once":
            freq_label = "⏱ Однократно"
        elif b_freq_type == "daily":
            freq_label = "🗓 Ежедневно"
        elif b_freq_type == "custom":
            days_map = {'0':'Пн', '1':'Вт', '2':'Ср', '3':'Чт', '4':'Пт', '5':'Сб', '6':'Вс'}
            selected = [days_map[d] for d in b_freq_days if d in days_map]
            freq_label = f"📅 {', '.join(selected)}"
        else:
            freq_label = "Неизвестно"

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
        cur.execute("SELECT * FROM broadcasts_new WHERE ID=?", (b_id,))
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

    # Логика расшифровки дней для карточки и списка:
    freq_type = b['freq_type']
    freq_days = b['freq_days']

    if freq_type == "once":
        freq_label = "⏱ Однократно"
    elif freq_type == "daily":
        freq_label = "🗓 Ежедневно"
    elif freq_type == "custom":
        days_map = {'0':'Пн', '1':'Вт', '2':'Ср', '3':'Чт', '4':'Пт', '5':'Сб', '6':'Вс'}
        selected = [days_map[d] for d in freq_days if d in days_map]
        freq_label = f"📅 {', '.join(selected)}"
    else:
        freq_label = "Неизвестно"

    status_label = "🟢 Активна" if b['status'] == 1 else "⏸ На паузе"
    toggle_btn_text = "⏸ Поставить на паузу" if b['status'] == 1 else "▶️ Активировать"

    card_text = (
        f"📢 <b>Управление рассылкой #{b_id}</b>\n\n"
        f"<b>Время старта:</b> {b['time']}\n"
        f"<b>Повторение:</b> {freq_label}\n"
        f"<b>Статус:</b> {status_label}\n\n"
        f"<b>Текст сообщения:</b>\n{b['text']}"
    )

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(text=toggle_btn_text, callback_data=f"bc_toggle_{b_id}"))
    markup.add(types.InlineKeyboardButton(text="🗑 Полностью удалить", callback_data=f"bc_delete_{b_id}"))
    markup.add(
        types.InlineKeyboardButton(text="✏️ Изменить текст", callback_data=f"bc_edittxt_{b_id}"),
        types.InlineKeyboardButton(text="🕒 Изменить время", callback_data=f"bc_edittime_{b_id}")
    )
    # Добавили новую кнопку изменения частоты:
    markup.add(types.InlineKeyboardButton(text="🔄 Изменить частоту", callback_data=f"bc_editfreq_{b_id}"))
    markup.add(types.InlineKeyboardButton(text="⬅️ Вернуться к списку", callback_data=f"bc_back_list"))

    if b['photo'] and b['photo'] != "None":
        bot.send_photo(message.chat.id, photo=b['photo'], caption=card_text, reply_markup=markup, parse_mode='HTML')
    else:
        bot.send_message(message.chat.id, card_text, reply_markup=markup, parse_mode='HTML')

    bot.send_message(message.chat.id, "Выберите действие 👆", reply_markup=types.ReplyKeyboardRemove())
    
def register_broadcast_callbacks(bot):
    @bot.callback_query_handler(func=lambda call: call.data.startswith('bc_'))
    def bc_callback(call):
        if not require_role(call, bot, ROLE_MANAGER):
            return
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
                cur.execute("SELECT status FROM broadcasts_new WHERE ID=?", (b_id,))
                res = cur.fetchone()
                if res:
                    new_status = 0 if res[0] == 1 else 1
                    cur.execute("UPDATE broadcasts_new SET status=? WHERE ID=?", (new_status, b_id))
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
                cur.execute("DELETE FROM broadcasts_new WHERE ID=?", (b_id,))
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
            
            if data.startswith("editfreq_"):
                b_id = int(data.split("_")[1])
                bot.delete_message(call.message.chat.id, call.message.id)
                bot.send_message(
                    call.message.chat.id, 
                    "Выберите новую частоту или дни недели для этой рассылки:", 
                    reply_markup=generate_edit_days_keyboard(b_id, "")
                )
                return
            
        except Exception as e:
            print(f"Ошибка колбэка рассылок: {e}")
    
    @bot.callback_query_handler(func=lambda call: call.data.startswith('bcfreq_'))
    def bcfreq_callback(call):
        if not require_role(call, bot, ROLE_MANAGER):
            return
        chat_id = call.message.chat.id
        if chat_id not in temp_broadcasts:
            bot.answer_callback_query(call.id, "Данные устарели. Начните заново.", show_alert=True)
            return

        parts = call.data.split('_')
        action = parts[1]
        
        # Обработка нажатий на галочки
        if action == 'toggle':
            new_days = parts[2] if len(parts) > 2 else ""
            bot.edit_message_reply_markup(chat_id, call.message.id, reply_markup=generate_days_keyboard(new_days))
            return

        # Подготовка к сохранению в новую БД
        data = temp_broadcasts.pop(chat_id)
        freq_type = ""
        freq_days = ""

        if action == 'once':
            freq_type = "once"
        elif action == 'daily':
            freq_type = "daily"
        elif action == 'custom':
            freq_type = "custom"
            freq_days = parts[2] if len(parts) > 2 else ""

        try:
            conn = sqlite3.connect('db/omgbot.sql')
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO broadcasts_new (text, photo, time, freq_type, freq_days, status) VALUES (?, ?, ?, ?, ?, ?)",
                (data['text'], data['photo'], data['time'], freq_type, freq_days, 1)
            )
            conn.commit()
            cur.close()
            conn.close()
            
            bot.delete_message(chat_id, call.message.id)
            bot.send_message(chat_id, "✅ Рассылка успешно создана и сохранена!")
            broadcast_menu(call.message, bot)
        except Exception as e:
            bot.send_message(chat_id, f"❌ Ошибка БД: {e}")

    @bot.callback_query_handler(func=lambda call: call.data.startswith('bcef_'))
    def bcef_callback(call):
        if not require_role(call, bot, ROLE_MANAGER):
            return
        chat_id = call.message.chat.id
        parts = call.data.split('_')
        action = parts[1]
        b_id = int(parts[2])
        
        # Обработка нажатий на галочки при редактировании
        if action == 'toggle':
            new_days = parts[3] if len(parts) > 3 else ""
            bot.edit_message_reply_markup(chat_id, call.message.id, reply_markup=generate_edit_days_keyboard(b_id, new_days))
            return

        # Подготовка к перезаписи базы
        freq_type = ""
        freq_days = ""

        if action == 'once':
            freq_type = "once"
        elif action == 'daily':
            freq_type = "daily"
        elif action == 'custom':
            freq_type = "custom"
            freq_days = parts[3] if len(parts) > 3 else ""

        try:
            conn = sqlite3.connect('db/omgbot.sql')
            cur = conn.cursor()
            # Перезаписываем данные КОНКРЕТНОЙ рассылки
            cur.execute(
                "UPDATE broadcasts_new SET freq_type=?, freq_days=? WHERE id=?",
                (freq_type, freq_days, b_id)
            )
            conn.commit()
            cur.close()
            conn.close()
            
            bot.delete_message(chat_id, call.message.id)
            bot.send_message(chat_id, "✅ Частота рассылки успешно обновлена!")
            
            # Возвращаем пользователя в красивую карточку этой же рассылки
            bc_view_card(call.message, b_id, bot)
        except Exception as e:
            bot.send_message(chat_id, f"❌ Ошибка БД: {e}")
            
def bc_save_new_text(message, b_id, bot):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    if message.text == 'Вернуться':
        bc_view_card(message, b_id, bot)
        return
    
    conn = sqlite3.connect('db/omgbot.sql')
    cur = conn.cursor()
    cur.execute("UPDATE broadcasts_new SET text=? WHERE ID=?", (message.text, b_id))
    conn.commit()
    cur.close()
    conn.close()
    
    bot.send_message(message.chat.id, "✅ Текст рассылки успешно обновлен!")
    bc_view_card(message, b_id, bot)

def bc_save_new_time(message, b_id, bot):
    if not require_role(message, bot, ROLE_MANAGER):
        return
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
    cur.execute("UPDATE broadcasts_new SET time=? WHERE ID=?", (time_str, b_id))
    conn.commit()
    cur.close()
    conn.close()
    
    bot.send_message(message.chat.id, "✅ Время отправки обновлено!")
    bc_view_card(message, b_id, bot)


###### Модуль расходников

def get_allowed_clubs():
    """Динамически загружает конфиг и оставляет только клубы с require_geo = True"""
    try:
        from constants import get_clubs
        current_clubs = get_clubs()
        return [club for club in current_clubs if current_clubs[club].get('require_geo', False)]
    except Exception as e:
        print(f"Ошибка чтения require_geo из конфига: {e}")
        # Если конфиг пуст или сломался — возвращаем пустой список во избежание падения
        return []

def admin_consumables_menu(message, bot):
    """Главное меню управления расходниками для админа"""
    if not require_role(message, bot, ROLE_MANAGER):
        return
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add('➕ Добавить расходник', '📋 Управление расходниками', '⬅️ Назад в админку')
    
    # Получаем объект сообщения (поддержка вызова из разных контекстов)
    chat_id = message.chat.id if hasattr(message, 'chat') else message
    msg = bot.send_message(chat_id, "Управление расходниками (Панель Администратора):", reply_markup=markup)
    bot.register_next_step_handler(msg, admin_consumables_handler, bot)

def admin_consumables_handler(message, bot):
    if not require_role(message, bot, ROLE_MANAGER):
        return
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
    if not require_role(message, bot, ROLE_MANAGER):
        return
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
    if not require_role(message, bot, ROLE_MANAGER):
        return
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
    if not require_role(message, bot, ROLE_MANAGER):
        return
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
        if not require_role(call, bot, ROLE_MANAGER):
            return
        try:
            bot.answer_callback_query(call.id)
            data = call.data[8:]

            if data == "backclubs":
                bot.delete_message(call.message.chat.id, call.message.id)
                admin_consumables_menu(call.message, bot)
                return

            if data.startswith("view_"):
                item_id = int(data.split('_')[1])
                bot.delete_message(call.message.chat.id, call.message.id)
                admin_view_item_card(call.message.chat.id, item_id, bot)
                return

            if data.startswith("editqty_"):
                item_id = int(data.split('_')[1])
                bot.delete_message(call.message.chat.id, call.message.id)
                markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
                markup.add('Отмена')
                msg = bot.send_message(call.message.chat.id, "Укажите новое текущее количество расходника на складе:", reply_markup=markup)
                bot.register_next_step_handler(msg, admcons_save_qty, item_id, bot)
                return

            if data.startswith("editmin_"):
                item_id = int(data.split('_')[1])
                bot.delete_message(call.message.chat.id, call.message.id)
                markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
                markup.add('Отмена')
                msg = bot.send_message(call.message.chat.id, "Укажите новый минимальный лимит для уведомлений менеджера:", reply_markup=markup)
                bot.register_next_step_handler(msg, admcons_save_min, item_id, bot)
                return

            if data.startswith("del_"):
                item_id = int(data.split('_')[1])
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
                club = data.split('_')[1]
                bot.delete_message(call.message.chat.id, call.message.id)
                admin_show_club_items(call.message.chat.id, club, bot)
                return

        except Exception as e:
            print(f"Ошибка колбэка админ-расходников: {e}")
            
# Для теста запуска напрямую
if __name__ == "__main__":
    print(sync_config())
