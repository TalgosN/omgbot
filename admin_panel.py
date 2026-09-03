import pygsheets
import html
import os
from telebot import *
import sqlite3
import re
import threading
from datetime import datetime
from statistics import median
import requests
import pytz
import constants as app_constants
from constants import CHATS, SHIFTON_API_URL, SHIFTON_API_TOKEN, validate_config
from club_config import get_club_config_status, get_clubs, save_clubs
from club_config_sync import (
    ConfigValidationError,
    VALIDATION_SHEET,
    config_diff,
    count_config,
    read_config,
    write_validation,
)
from sender import safe_send
from kpi_calculator import active_kpi_employee_logins, calculate_monthly_kpi
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
CONFIG_SPREADSHEET_ID = '1LxBCPpWXtpS_EVhGUNuH2k4HtPnsu53ZF-4QaRET08Q'
temp_broadcasts = {}
health_check_lock = threading.Lock()
config_sync_lock = threading.Lock()

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

    elif a == '✅ Задачи смены':
        from shift_tasks import show_task_templates
        show_task_templates(message, bot)

    elif a in {'🎮 Steam Tracker', '📣 Промо'}:
        from steamtracker.admin import promotion_admin_menu
        promotion_admin_menu(message, bot)

    elif a in {'⚙️ Система и тесты', '🧰 Дополнительно'}:
        admin_extra_menu(message, bot)

    elif a == '👥 Сотрудники и роли':
        staff_management_menu(message, bot)
        
    elif a == '⚙️ Обновить настройки':
        handle_update_config(message, bot)

    elif a == '🩺 Статус систем':
        handle_system_health(message, bot)

    elif a == '🔄 Сотрудники OMG Shift':
        handle_shifton_employee_sync(message, bot)

    elif a == '📊 KPI сотрудников':
        handle_monthly_kpi_report(message, bot)

    elif a == '🎂 Тест поздравления':
        birthday_test_prompt(message, bot)

    elif a == '📝 Сценарии смен':
        open_shift_config(message, bot)
        
    elif a in {'🏠 Главное меню', '⬅️ Вернуться'}:
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
            
        admin_extra_menu(message, bot)
  
    elif a == '📦 Тест отчета по расходникам':
            msg = bot.send_message(message.chat.id, "⏳ Анализирую остатки по клубам...")
            try:
                from consumables import auto_consumables_report
                # Вызываем функцию с передачей текущего чата в качестве цели
                auto_consumables_report(bot, target_chat_id=message.chat.id)
                bot.delete_message(message.chat.id, msg.message_id)
            except Exception as e:
                bot.edit_message_text(chat_id=message.chat.id, message_id=msg.message_id, text=f"❌ Ошибка отчета расходников: {e}")
            
            admin_extra_menu(message, bot)

    elif a == '📦 Расходники (Админ)':
        admin_consumables_menu(message, bot)

    elif a == '👥 Управление ролями':
        role_management_menu(message, bot)

    else:
        from menu import admin_menu
        admin_menu(message, bot)


def open_shift_config(message, bot):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    webapp_url = getattr(app_constants, 'KPI_WEBAPP_URL', '')
    runtime_url_path = os.path.join('data', 'kpi_webapp_url.txt')
    if not webapp_url and os.path.exists(runtime_url_path):
        with open(runtime_url_path, 'r', encoding='utf-8') as runtime_url_file:
            webapp_url = runtime_url_file.read().strip()
    if not webapp_url:
        bot.send_message(message.chat.id, 'Mini App ещё не подключён к HTTPS-адресу.')
        from menu import admin_menu
        admin_menu(message, bot)
        return
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(
        '📝 Открыть редактор сценариев',
        web_app=types.WebAppInfo(f'{webapp_url.rstrip("/")}/shift-config'),
    ))
    markup.add(types.InlineKeyboardButton(
        '⬅️ В управление',
        callback_data='nav:admin',
    ))
    from menu import hide_reply_keyboard
    hide_reply_keyboard(message.chat.id, bot)
    bot.send_message(
        message.chat.id,
        'Вопросы и чек-листы открытия и закрытия смен:',
        reply_markup=markup,
    )


def admin_extra_menu(message, bot):
    user = require_role(message, bot, ROLE_MANAGER)
    if not user:
        return
    from constants import admin_extra_funclist, owner_admin_extra_funclist

    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    buttons = owner_admin_extra_funclist if user['status'] >= ROLE_OWNER else admin_extra_funclist
    markup.add(*buttons)
    msg = bot.send_message(
        message.chat.id,
        '⚙️ Система и тесты',
        reply_markup=markup,
    )
    bot.register_next_step_handler(msg, admin_extra_menu_handler, bot)


def admin_extra_menu_handler(message, bot):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    if message.text in {'⬅️ Назад в управление', '⬅️ Назад в админку'}:
        from menu import admin_menu
        admin_menu(message, bot)
        return
    if message.text in {
        '⚙️ Обновить настройки',
        '🔄 Сотрудники OMG Shift',
        '🩺 Статус систем',
        '🎂 Тест поздравления',
        '📊 Тест недельного отчета',
        '📦 Тест отчета по расходникам',
    }:
        admin_func_handler(message, bot)
        return
    admin_extra_menu(message, bot)


def staff_management_menu(message, bot):
    if not require_role(message, bot, ROLE_OWNER):
        return
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add(
        '🔄 Сотрудники OMG Shift',
        '👥 Управление ролями',
        '⬅️ Назад в управление',
    )
    sent = bot.send_message(
        message.chat.id,
        '👥 Сотрудники и роли',
        reply_markup=markup,
    )
    bot.register_next_step_handler(sent, staff_management_handler, bot)


def staff_management_handler(message, bot):
    if not require_role(message, bot, ROLE_OWNER):
        return
    if message.text == '🔄 Сотрудники OMG Shift':
        handle_shifton_employee_sync(message, bot)
    elif message.text == '👥 Управление ролями':
        role_management_menu(message, bot)
    elif message.text == '⬅️ Назад в управление':
        from menu import admin_menu
        admin_menu(message, bot)
    else:
        staff_management_menu(message, bot)


def birthday_test_prompt(message, bot):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add('⬅️ Назад')
    sent = bot.send_message(
        message.chat.id,
        'Отправь Telegram-тег активного сотрудника, например @username.\n\n'
        'Поздравление придёт только сюда и не будет отмечено отправленным.',
        reply_markup=markup,
    )
    bot.register_next_step_handler(sent, birthday_test_generate, bot)


def birthday_test_generate(message, bot):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    if message.text == '⬅️ Назад':
        admin_extra_menu(message, bot)
        return
    login = str(message.text or '').strip()
    if not re.fullmatch(r'@?[A-Za-z0-9_]{5,32}', login):
        bot.send_message(message.chat.id, 'Нужен корректный Telegram-тег: @username.')
        birthday_test_prompt(message, bot)
        return
    loading = bot.send_message(
        message.chat.id,
        '⏳ Считаю личный год и готовлю поздравление от Виарыча…',
        reply_markup=types.ReplyKeyboardRemove(),
    )
    try:
        from birthday_greetings import build_birthday_preview

        user, preview = build_birthday_preview(login)
        try:
            bot.delete_message(message.chat.id, loading.message_id)
        except Exception:
            pass
        if preview['source'] == 'openrouter':
            source_note = 'OpenRouter'
        else:
            reason = preview.get('generation_error')
            source_note = 'резервный шаблон — OpenRouter недоступен'
            if reason:
                source_note += f' ({reason})'
        bot.send_message(
            message.chat.id,
            f'🧪 Тест для {user["login"]}\n'
            f'Источник: {source_note}\n\n{preview["text"]}',
        )
    except Exception as error:
        try:
            bot.edit_message_text(
                f'❌ Не удалось создать поздравление: {error}',
                chat_id=message.chat.id,
                message_id=loading.message_id,
            )
        except Exception:
            bot.send_message(
                message.chat.id,
                f'❌ Не удалось создать поздравление: {error}',
            )
    admin_extra_menu(message, bot)


def parse_report_number(value):
    normalized = str(value or '').replace('%', '').replace(' ', '').replace(',', '.').strip()
    try:
        return float(normalized)
    except ValueError:
        return 0.0


KPI_SHADOW_FIELD_LABELS = {
    'employee': 'Сотрудник',
    'shifts': 'Смены',
    'weighted_shifts': 'Взвешенные смены',
    'reviews': 'Отзывы',
    'reviews_pct': 'Отзывы, %',
    'forms': 'Анкеты',
    'forms_pct': 'Анкеты, %',
    'extensions': 'Продления',
    'extensions_pct': 'Продления, %',
    'certificates': 'Сертификаты',
    'certificates_pct': 'Сертификаты, %',
    'subscriptions': 'Абонементы',
    'subscriptions_pct': 'Абонементы, %',
    'initiatives': 'Инициативы',
    'initiatives_pct': 'Инициативы, %',
    'penalties': 'Штрафы',
    'total_pct': 'Итоговый KPI',
    'weighted_pct': 'Взвешенный KPI',
    'rank': 'Рейтинг',
}


def build_kpi_shadow_report(comparison, controls):
    differences = comparison.get('differences', [])
    sheet_health = comparison.get('sheet_health', {})
    field_counts = {}
    affected_employees = set()
    for difference in differences:
        field = difference.get('field', 'unknown')
        field_counts[field] = field_counts.get(field, 0) + 1
        if difference.get('login'):
            affected_employees.add(difference['login'])

    lines = [
        '🧪 <b>Диагностика теневого KPI</b>',
        f"📅 Проверяется только месяц: <b>{html.escape(str(comparison.get('period_month', '—')))}</b>",
        f"👥 Сотрудников проверено: <b>{comparison.get('employees', 0)}</b>",
        '',
    ]
    if differences:
        lines.extend([
            f'⚠️ Расхождений: <b>{len(differences)}</b>',
            f'👤 Затронуто сотрудников: <b>{len(affected_employees)}</b>',
        ])
        duplicate_rows = sheet_health.get('data_duplicate_rows', 0)
        if duplicate_rows:
            lines.extend([
                '',
                '<b>Обнаруженная причина:</b>',
                f'❌ В листе data найдено лишних дублей: <b>{duplicate_rows}</b>',
                (
                    f"Строк месяца: {sheet_health.get('data_rows', 0)}, "
                    f"уникальных: {sheet_health.get('data_unique_rows', 0)}."
                ),
            ])
        if field_counts.get('shifts') or field_counts.get('weighted_shifts'):
            lines.append(
                '⚠️ Срез смен в Google отличается от текущего состояния SQLite.'
            )
        derived_fields = {
            'reviews_pct', 'forms_pct', 'extensions_pct',
            'certificates_pct', 'subscriptions_pct',
            'initiatives_pct', 'total_pct', 'weighted_pct', 'rank',
        }
        if any(field in field_counts for field in derived_fields):
            lines.append(
                'ℹ️ Проценты, итоговый KPI и рейтинг расходятся '
                'как следствие исходных фактов и смен.'
            )

        lines.extend(['', '<b>По показателям:</b>'])
        for field, count in sorted(
            field_counts.items(),
            key=lambda item: (-item[1], item[0]),
        ):
            label = KPI_SHADOW_FIELD_LABELS.get(field, field)
            lines.append(f'• {html.escape(label)}: <b>{count}</b>')

        source_fields = {
            'shifts', 'weighted_shifts', 'reviews', 'forms', 'extensions',
            'certificates', 'subscriptions', 'initiatives', 'penalties',
        }
        examples = [
            difference
            for difference in differences
            if difference.get('field') in source_fields
        ][:6]
        if examples:
            lines.extend(['', '<b>Примеры: сервер → Google</b>'])

            def format_example_value(value):
                try:
                    return f'{float(value):.4g}'
                except (TypeError, ValueError):
                    return str(value if value is not None else '—')

            for difference in examples:
                label = KPI_SHADOW_FIELD_LABELS.get(
                    difference['field'],
                    difference['field'],
                )
                server_value = format_example_value(difference.get('server'))
                sheet_value = format_example_value(difference.get('sheet'))
                lines.append(
                    f"• {html.escape(str(difference.get('login', '—')))}, "
                    f"{html.escape(label)}: "
                    f"<b>{server_value} → {sheet_value}</b>"
                )
    else:
        lines.append('✅ Серверный расчёт совпадает с Google Sheets.')

    sources = controls.get('penalty_sources', {})
    lines.extend([
        '',
        '<b>Перенос управляющих данных:</b>',
        f"• Штрафы из Google Sheets: <b>{sources.get('legacy_google_sheet', 0)}</b>",
        f"• Штрафы из старой БД: <b>{sources.get('legacy_db', 0)}</b>",
        f"• Активные штрафы месяца: <b>{controls.get('current_penalties', 0)}</b>",
        f"• Трансляции месяца: <b>{controls.get('current_streams', 0)}</b>",
        '',
        '<i>Диагностика ничего не переключает и не изменяет рабочий отчёт.</i>',
    ])
    return '\n'.join(lines)


def format_kpi_report_percent(value):
    percent = float(value or 0) * 100
    return f'{percent:.1f}'.rstrip('0').rstrip('.') + '%'


def build_monthly_kpi_report(rows, selected_date):
    employees = []
    for row in rows:
        name = str(row.get('nickname') or row.get('login') or '').strip()
        shifts = float(row.get('shifts') or 0)
        weighted_shifts = float(row.get('weighted_shifts') or 0)
        if not name or shifts <= 0 or weighted_shifts <= 0:
            continue
        total_pct = float(row.get('total_pct') or 0)
        weighted_pct = float(row.get('weighted_pct') or 0)
        employees.append({
            'name': name,
            'shifts': shifts,
            'total_pct': total_pct,
            'weighted_pct': weighted_pct,
            'sort_pct': total_pct,
        })

    employees.sort(key=lambda employee: (employee['sort_pct'], employee['name'].lower()))
    non_zero_results = [employee['sort_pct'] for employee in employees if employee['sort_pct'] > 0]
    average_pct = sum(non_zero_results) / len(non_zero_results) if non_zero_results else 0
    median_pct = median(non_zero_results) if non_zero_results else 0

    header = (
        '📊 <b>KPI сотрудников за месяц</b>\n'
        f'📅 <i>Расчётная дата: {html.escape(str(selected_date))}</i>\n'
        f'👥 Сотрудников в отчёте: <b>{len(employees)}</b>\n\n'
        f'📈 Средний KPI: <b>{format_kpi_report_percent(average_pct)}</b>\n'
        f'📐 Медианный KPI: <b>{format_kpi_report_percent(median_pct)}</b>\n'
        f'ℹ️ <i>Среднее и медиана рассчитаны без нулевых KPI.</i>'
    )
    if not employees:
        return [f'{header}\n\nℹ️ За выбранный месяц нет сотрудников со сменами.']

    weakest = employees[:3]

    def employee_line(employee, icon):
        return (
            f'{icon} <b>{html.escape(str(employee["name"]))}</b>: '
            f'KPI <b>{format_kpi_report_percent(employee["total_pct"])}</b> '
            f'<i>({format_kpi_report_percent(employee["weighted_pct"])} взв.)</i> | '
            f'📆 Смен: <b>{employee["shifts"]:g}</b>'
        )

    messages = [
        f'{header}\n\n🔻 <b>Красная зона: 3 самых слабых результата</b>\n\n'
        + '\n'.join(employee_line(employee, '🔴') for employee in weakest)
    ]
    remaining = employees[3:]
    if remaining:
        lines = ['📋 <b>Остальные сотрудники</b>', '']
        for employee in remaining:
            line = employee_line(employee, '🔹')
            if len('\n'.join(lines + [line])) > 3500:
                messages.append('\n'.join(lines))
                lines = ['📋 <b>Продолжение отчёта</b>', '', line]
            else:
                lines.append(line)
        messages.append('\n'.join(lines))
    return messages


def handle_monthly_kpi_report(message, bot):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    wait_message = bot.send_message(message.chat.id, '⏳ Собираю KPI сотрудников...')
    try:
        now = datetime.now(pytz.timezone('Europe/Moscow'))
        selected_date = now.strftime('%Y-%m-%d')
        rows = calculate_monthly_kpi(
            now.replace(day=1).strftime('%Y-%m-%d'),
            employee_logins=active_kpi_employee_logins(),
            period_end=selected_date,
        )
        reports = build_monthly_kpi_report(
            rows,
            now.strftime('%d.%m.%Y'),
        )
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
    markup.add('📜 Последние изменения', '⬅️ Назад к сотрудникам')
    msg = bot.send_message(message.chat.id, 'Введите Telegram username сотрудника, например @username:', reply_markup=markup)
    bot.register_next_step_handler(msg, role_select_user, bot)


def role_select_user(message, bot):
    if not require_role(message, bot, ROLE_OWNER):
        return
    if message.text in {'⬅️ Назад к сотрудникам', '⬅️ Назад в админку'}:
        staff_management_menu(message, bot)
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
            'SELECT * FROM users WHERE lower(login)=lower(?) ORDER BY ID LIMIT 1',
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
    markup.add(*ROLE_BUTTONS.keys(), '⬅️ Назад к сотрудникам')
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
    if message.text in {'⬅️ Назад к сотрудникам', '⬅️ Назад в админку'}:
        role_management_menu(message, bot)
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
        users_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        conn.close()
        lines.append(f"✅ SQLite: доступна, сотрудников {users_count}")
    except Exception as e:
        lines.append(f"❌ SQLite: {str(e)[:120]}")

    try:
        validate_config()
        lines.append("✅ Конфигурация: обязательные параметры заданы")
        club_status = get_club_config_status()
        lines.append(
            f"✅ Клубы: локальная версия {club_status['version']}, "
            f"источник {club_status['source']}"
        )
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
        gc.open_by_key(CONFIG_SPREADSHEET_ID)
        lines.append("✅ Google Sheets: Виарыч доступен")
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
        employee_sync = runtime.get("last_employee_sync") or "ещё не выполнялась"
        lines.append(f"ℹ️ Очередь уведомлений: последняя проверка {last_check}")
        sync_result = runtime.get("last_chat_sync_result") or "результат отсутствует"
        lines.append(f"ℹ️ Синхронизация чатов: {last_sync}, {sync_result}")
        employee_result = runtime.get("last_employee_sync_result") or "результат отсутствует"
        lines.append(f"ℹ️ Сотрудники OMG Shift: {employee_sync}, {employee_result}")
        if runtime.get("last_employee_sync_error"):
            lines.append(
                f"⚠️ Ошибка синхронизации сотрудников: "
                f"{runtime['last_employee_sync_error'][:120]}"
            )
        if runtime.get("last_notification_error"):
            lines.append(f"⚠️ Последняя ошибка очереди: {runtime['last_notification_error'][:120]}")
    except Exception as e:
        lines.append(f"⚠️ Состояние уведомлений недоступно: {str(e)[:120]}")

    lines.extend(collect_steamtracker_health())
    return "\n".join(lines)


def handle_shifton_employee_sync(message, bot):
    if not require_role(message, bot, ROLE_OWNER):
        return
    progress = bot.send_message(
        message.chat.id,
        '⏳ Синхронизирую сотрудников и ставки с OMG Shift...',
    )
    try:
        from rasp import sync_shifton_employees
        result = sync_shifton_employees()
        lines = [
            '✅ <b>Сотрудники OMG Shift синхронизированы</b>',
            '',
            f'👥 Всего: <b>{result["total"]}</b>',
            f'🟢 Активных: <b>{result["active"]}</b>',
            f'📦 Архивных: <b>{result["archived"]}</b>',
            f'🔗 Связано с ботом: <b>{result["linked"]}</b>',
            f'✏️ Профилей изменено: <b>{result["changed"]}</b>',
            f'💵 Периодов ставок: <b>{result["rate_rows"]}</b>',
        ]
        attention = (
            result['unlinked']
            + result['identity_conflicts']
            + result['access_mismatches']
        )
        if attention:
            lines.extend(['', '⚠️ <b>Требуют внимания:</b>'])
            for item in attention[:15]:
                identity = item.get('telegram') or item.get('name') or item['employee_id']
                reason = item.get('error') or 'нет связи с users'
                lines.append(f'• {html.escape(str(identity))}: {html.escape(str(reason))}')
        if result['rate_conflicts']:
            lines.append(
                f'⚠️ Существующие ставки сохранены в '
                f'<b>{len(result["rate_conflicts"])}</b> совпадающих периодах.'
            )
        if result['google_errors']:
            lines.append(
                f'⚠️ Ошибок обновления Google Sheets: '
                f'<b>{len(result["google_errors"])}</b>.'
            )
        bot.edit_message_text(
            '\n'.join(lines),
            chat_id=message.chat.id,
            message_id=progress.message_id,
            parse_mode='HTML',
        )
    except Exception as error:
        bot.edit_message_text(
            f'❌ Не удалось синхронизировать сотрудников: {html.escape(str(error))}',
            chat_id=message.chat.id,
            message_id=progress.message_id,
            parse_mode='HTML',
        )
    staff_management_menu(message, bot)


def collect_openrouter_health(api_key, model=None, session=requests):
    if not api_key:
        return ["❌ OpenRouter: API-ключ не задан"]

    try:
        response = session.get(
            "https://openrouter.ai/api/v1/key",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=5,
        )
        response.raise_for_status()
        key_data = response.json().get("data") or {}
    except Exception as error:
        return [f"❌ OpenRouter: {str(error)[:120]}"]

    lines = [
        "✅ OpenRouter: ключ доступен, "
        f"модель {model or 'по умолчанию'}"
    ]
    key_limit = key_data.get("limit")
    key_remaining = key_data.get("limit_remaining")
    if key_limit is not None and key_remaining is not None:
        lines.append(
            "🔑 Лимит ключа OpenRouter: "
            f"осталось ${float(key_remaining):.2f} "
            f"из ${float(key_limit):.2f}"
        )

    try:
        response = session.get(
            "https://openrouter.ai/api/v1/credits",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=5,
        )
        response.raise_for_status()
        credits = response.json().get("data") or {}
        total_credits = float(credits["total_credits"])
        total_usage = float(credits["total_usage"])
        lines.append(
            "💳 Аккаунт OpenRouter: "
            f"остаток ${max(total_credits - total_usage, 0):.2f} "
            f"из ${total_credits:.2f}, "
            f"использовано ${total_usage:.2f}"
        )
    except Exception as error:
        lines.append(
            "⚠️ Баланс OpenRouter недоступен: "
            f"{str(error)[:120]}"
        )
    return lines


def collect_steamtracker_health():
    lines = ["", "🎮 Steam Tracker"]
    try:
        from steamtracker.config import Settings
        from steamtracker.db import TrackerStorage
        from steamtracker.sheets import GoogleSheetsManager
        from steamtracker.weekly import setting_enabled

        settings = Settings.from_env()
        storage = TrackerStorage(settings.db_path)
        storage.initialize()
        summary = storage.summary()
        with storage.connect() as conn:
            last_license_sync = conn.execute(
                "SELECT MAX(updated_at) FROM accounts"
            ).fetchone()[0]
            last_enrichment = conn.execute(
                "SELECT MAX(updated_at) FROM game_metadata"
            ).fetchone()[0]
            last_promotion = conn.execute(
                "SELECT MAX(updated_at) FROM promotions"
            ).fetchone()[0]
            outbox_errors = conn.execute(
                "SELECT COUNT(*) FROM outbox WHERE status = 'error'"
            ).fetchone()[0]

        lines.append(
            "✅ База: "
            f"{summary['accounts']} аккаунтов, "
            f"{summary['approved_games']} игр, "
            f"{summary['owned_licenses']} лицензий"
        )
        lines.append(
            "ℹ️ Последняя проверка лицензий: "
            f"{last_license_sync or 'ещё не выполнялась'}"
        )
        lines.append(
            "ℹ️ Обновление описаний: "
            f"{last_enrichment or 'ещё не выполнялось'}"
        )
        lines.append(
            "ℹ️ Последнее изменение промо: "
            f"{last_promotion or 'промо пока нет'}"
        )
        if outbox_errors:
            lines.append(f"⚠️ Ошибок технической очереди: {outbox_errors}")
        else:
            lines.append("✅ Техническая очередь: без ошибок")

        tracker_settings = storage.tracker_settings()
        bot_enabled = setting_enabled(
            tracker_settings.get("weekly_promo_enabled")
        )
        fully_enabled = settings.weekly_promo_enabled and bot_enabled
        lines.append(
            "ℹ️ Игра недели: "
            f"режим {'автоматический' if fully_enabled else 'ручной'}"
        )
        if settings.employee_delivery_enabled:
            if settings.employee_chat_id is None:
                lines.append(
                    "❌ Рассылка сотрудникам: не задан ID рабочего чата"
                )
            else:
                lines.append("✅ Рассылка сотрудникам: включена")
        else:
            lines.append("ℹ️ Рассылка сотрудникам: dry-run")
        lines.append("ℹ️ Публикация Telegram и VK: dry-run")
        if settings.google_export_enabled:
            try:
                GoogleSheetsManager(settings).open()
                lines.append("✅ Google Steam Tracker: отчёты доступны")
            except Exception as error:
                lines.append(
                    f"❌ Google Steam Tracker: {str(error)[:120]}"
                )
        else:
            lines.append("ℹ️ Google Steam Tracker: выгрузка отключена")

        if settings.generator_provider == "openrouter":
            lines.extend(collect_openrouter_health(
                settings.openrouter_api_key,
                settings.openrouter_model,
            ))
        else:
            lines.append(
                f"ℹ️ Генератор: {settings.generator_provider} "
                "(проверка OpenRouter не требуется)"
            )
    except Exception as error:
        lines.append(f"❌ Steam Tracker: {str(error)[:120]}")
    return lines


def handle_system_health(message, bot):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    if not health_check_lock.acquire(blocking=False):
        bot.send_message(message.chat.id, "⏳ Проверка систем уже выполняется.")
        admin_extra_menu(message, bot)
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
            admin_extra_menu(message, bot)

    threading.Thread(target=worker, daemon=True).start()


def handle_update_config(message, bot):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    if not config_sync_lock.acquire(blocking=False):
        bot.send_message(message.chat.id, "⏳ Синхронизация настроек уже выполняется.")
        admin_extra_menu(message, bot)
        return

    msg = bot.send_message(message.chat.id, "⏳ Подключаюсь к таблице 'Виарыч'...")

    def worker():
        try:
            report = sync_config()
            bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=msg.message_id,
                text=report,
            )
        except Exception as error:
            try:
                bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=msg.message_id,
                    text=f"❌ Ошибка синхронизации: {error}",
                )
            except Exception:
                bot.send_message(message.chat.id, f"❌ Ошибка синхронизации: {error}")
        finally:
            config_sync_lock.release()
            admin_extra_menu(message, bot)

    threading.Thread(target=worker, daemon=True).start()


def sync_config():
    try:
        client = pygsheets.authorize(service_file=KEY_FILE)
        spreadsheet = client.open_by_key(CONFIG_SPREADSHEET_ID)
        current_config = get_clubs()
        new_config, worksheets = read_config(spreadsheet, current_config)
        changes = config_diff(current_config, new_config)
        clubs_count, questions_count, checklists_count = count_config(new_config)
        save_clubs(new_config, source='google')
        status = get_club_config_status()

        validation_warning = None
        if VALIDATION_SHEET in worksheets:
            try:
                write_validation(
                    worksheets[VALIDATION_SHEET],
                    f'OK, опубликована версия {status["version"]}',
                )
            except Exception as error:
                validation_warning = str(error)

        lines = [
            '✅ Конфигурация применена',
            '',
            f'🏢 Клубов: {clubs_count}',
            f'❓ Вопросов: {questions_count}',
            f'📋 Пунктов чек-листов: {checklists_count}',
            f'🔖 Версия: {status["version"]}',
        ]
        if changes['added']:
            lines.append(f'➕ Добавлены клубы: {", ".join(changes["added"])}')
        if changes['removed']:
            lines.append(f'➖ Удалены клубы: {", ".join(changes["removed"])}')
        if changes['changed']:
            lines.append(f'✏️ Изменены клубы: {", ".join(changes["changed"])}')
        if not any(changes.values()):
            lines.append('ℹ️ Изменений относительно локальной версии нет')
        if validation_warning:
            lines.append(f'⚠️ Не удалось обновить лист проверки: {validation_warning}')
        lines.extend(['', '💾 clubs.json обновлён атомарно, резервная копия сохранена.'])
        return '\n'.join(lines)
    except ConfigValidationError as error:
        return (
            '❌ Конфигурация не применена\n\n'
            f'{error}\n\n'
            'Локальный clubs.json и работающий бот не изменены.'
        )
    except Exception as error:
        return (
            '❌ Не удалось синхронизировать конфигурацию\n\n'
            f'{error}\n\n'
            'Бот продолжает использовать последнюю локальную версию.'
        )



def broadcast_menu(message, bot):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add(
        '➕ Инфо-рассылка', '📣 Текущие рассылки',
        '⬅️ Назад в управление',
    )
    msg = bot.send_message(
        message.chat.id,
        "Информационные рассылки 💌",
        reply_markup=markup,
    )
    bot.register_next_step_handler(msg, broadcast_menu_handler, bot)

def broadcast_menu_handler(message, bot):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    a = message.text
    if a in {'➕ Создать рассылку', '➕ Инфо-рассылка'}:
        bc_add_text(message, bot)
    elif a in {'📋 Текущие рассылки', '📣 Текущие рассылки'}:
        bc_show_active(message, bot)
    elif a in {'⬅️ Назад в управление', '⬅️ Назад в админку'}:
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
    from menu import hide_reply_keyboard
    hide_reply_keyboard(message.chat.id, bot)
    bot.send_message(message.chat.id, "Выберите дни недели для рассылки:", reply_markup=generate_days_keyboard(""))


def bc_show_active(message, bot):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    try:
        conn = sqlite3.connect('db/omgbot.sql')
        cur = conn.cursor()
        cur.execute(
            """SELECT ID, text, time, freq_type, freq_days, status
               FROM broadcasts
               WHERE COALESCE(kind, 'information')='information'"""
        )
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
        cur.execute(
            """SELECT * FROM broadcasts WHERE ID=?
               AND COALESCE(kind, 'information')='information'""",
            (b_id,),
        )
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
    markup.add(types.InlineKeyboardButton(
        text="✅ Преобразовать в задачу",
        callback_data=f"bc_convert_{b_id}",
    ))
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
                cur.execute(
                    """SELECT status FROM broadcasts WHERE ID=?
                       AND COALESCE(kind, 'information')='information'""",
                    (b_id,),
                )
                res = cur.fetchone()
                if res:
                    new_status = 0 if res[0] == 1 else 1
                    cur.execute(
                        """UPDATE broadcasts SET status=? WHERE ID=?
                           AND COALESCE(kind, 'information')='information'""",
                        (new_status, b_id),
                    )
                    conn.commit()
                cur.close()
                conn.close()
                bot.delete_message(call.message.chat.id, call.message.id)
                bc_view_card(call.message, b_id, bot)
                return
                
            if data.startswith("delete_"):
                b_id = int(data.split("_")[1])
                markup = types.InlineKeyboardMarkup(row_width=2)
                markup.add(
                    types.InlineKeyboardButton(
                        text="🗑 Да, удалить",
                        callback_data=f"bc_confirmdelete_{b_id}",
                    ),
                    types.InlineKeyboardButton(
                        text="⬅️ Отмена",
                        callback_data=f"bc_manage_{b_id}",
                    ),
                )
                bot.edit_message_reply_markup(
                    call.message.chat.id,
                    call.message.id,
                    reply_markup=markup,
                )
                return

            if data.startswith("confirmdelete_"):
                b_id = int(data.split("_")[1])
                conn = sqlite3.connect('db/omgbot.sql')
                cur = conn.cursor()
                cur.execute(
                    """DELETE FROM broadcasts WHERE ID=?
                       AND COALESCE(kind, 'information')='information'""",
                    (b_id,),
                )
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

            if data.startswith("convert_"):
                b_id = int(data.split("_")[1])
                bot.delete_message(call.message.chat.id, call.message.id)
                from shift_tasks import start_task_conversion
                start_task_conversion(call.message, bot, b_id)
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
                "INSERT INTO broadcasts (text, photo, time, freq_type, freq_days, status) VALUES (?, ?, ?, ?, ?, ?)",
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
                """UPDATE broadcasts SET freq_type=?, freq_days=? WHERE id=?
                   AND COALESCE(kind, 'information')='information'""",
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
    cur.execute(
        """UPDATE broadcasts SET text=? WHERE ID=?
           AND COALESCE(kind, 'information')='information'""",
        (message.text, b_id),
    )
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
    cur.execute(
        """UPDATE broadcasts SET time=? WHERE ID=?
           AND COALESCE(kind, 'information')='information'""",
        (time_str, b_id),
    )
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
