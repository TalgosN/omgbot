import html
import re
import sqlite3
from datetime import date, datetime

import pytz
import telebot

import sql_scripts
from constants import funclist_acc
from kpi_calculator import active_kpi_employee_logins, calculate_monthly_kpi
from sheets import update_table_open, update_users
from permissions import ROLE_EMPLOYEE, ROLE_MANAGER, require_role


DB_PATH = 'db/omgbot.sql'
BACK_BUTTON = '⬅️ Вернуться'
EDIT_BACK_BUTTON = '⬅️ В аккаунт'
OTHER_STATS_BUTTON = '🔎 Статистика сотрудника'
PROFILE_FIELDS = {
    '💬 Ник': 'nick_name',
    '🎂 День рождения': 'bday',
    '📱 Телефон': 'phone',
    '✉️ Email': 'email',
}
LOGIN_REFERENCES = {
    'activity': 'login',
    'afterparty': 'who',
    'birthday': 'who',
    'initiative': 'who',
    'sert': 'who',
    'abik': 'who',
    'reviews': 'who',
    'autosim': 'who',
    'activation': 'who',
    'double': 'who',
    'hashtag_events': 'telegram',
    'penalty': 'name',
    'consumables_history': 'user_name',
    'shifts': 'shift_login',
    'payroll_rates': 'login',
}


def normalize_login(username):
    username = str(username or '').strip()
    if not username:
        return None
    return username if username.startswith('@') else f'@{username}'


def parse_omg_employee_name(employee):
    if isinstance(employee, dict):
        employee = employee.get('full_name') or employee.get('name')
    parts = str(employee or '').strip().split()
    if len(parts) < 2:
        raise ValueError('OMG Shift не вернул полные имя и фамилию сотрудника')
    return ' '.join(parts[1:]), parts[0]


def table_columns(conn, table):
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    if not exists:
        return set()
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def get_user_by_chat_id(chat_id):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            'SELECT * FROM users WHERE CAST(chatid AS TEXT)=CAST(? AS TEXT)',
            (chat_id,),
        ).fetchone()
    finally:
        conn.close()


def get_user_by_login(login):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            '''SELECT * FROM users
               WHERE lower(login)=lower(?) AND status>=?
               ORDER BY ID LIMIT 1''',
            (normalize_login(login), ROLE_EMPLOYEE),
        ).fetchone()
    finally:
        conn.close()


def apply_omg_identity(chat_id, login, employee):
    """Атомарно меняет Telegram login и принимает официальное ФИО из OMG Shift."""
    login = normalize_login(login)
    if not login:
        raise ValueError('В Telegram не задан username')
    first_name, second_name = parse_omg_employee_name(employee)
    employee_id = str(employee.get('id') or '').strip() if isinstance(employee, dict) else ''

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            user = conn.execute(
                'SELECT * FROM users WHERE CAST(chatid AS TEXT)=CAST(? AS TEXT)',
                (chat_id,),
            ).fetchone()
            if not user:
                raise ValueError('Пользователь не найден в БД')

            duplicate = conn.execute(
                'SELECT ID FROM users WHERE lower(login)=lower(?) AND ID<>?',
                (login, user['ID']),
            ).fetchone()
            if duplicate:
                raise ValueError('Этот Telegram username уже принадлежит другому пользователю')
            if employee_id and 'omg_shift_employee_id' in table_columns(conn, 'users'):
                duplicate = conn.execute(
                    '''SELECT ID FROM users
                       WHERE omg_shift_employee_id=? AND ID<>?''',
                    (employee_id, user['ID']),
                ).fetchone()
                if duplicate:
                    raise ValueError('Эта карточка OMG Shift уже связана с другим пользователем')

            old_login = user['login']
            if old_login:
                for table, column in LOGIN_REFERENCES.items():
                    if column not in table_columns(conn, table):
                        continue
                    conn.execute(
                        f'UPDATE "{table}" SET "{column}"=? WHERE lower("{column}")=lower(?)',
                        (login, old_login),
                    )

            if 'omg_shift_employee_id' in table_columns(conn, 'users'):
                conn.execute(
                    """UPDATE users
                       SET login=?, first_name=?, second_name=?, chatid=?,
                           omg_shift_employee_id=COALESCE(NULLIF(?, ''), omg_shift_employee_id)
                       WHERE ID=?""",
                    (login, first_name, second_name, str(chat_id), employee_id, user['ID']),
                )
            else:
                conn.execute(
                    """UPDATE users
                       SET login=?, first_name=?, second_name=?, chatid=?
                       WHERE ID=?""",
                    (login, first_name, second_name, str(chat_id), user['ID']),
                )

        return {
            'old_login': old_login,
            'login': login,
            'first_name': first_name,
            'second_name': second_name,
            'changed': (
                old_login != login
                or user['first_name'] != first_name
                or user['second_name'] != second_name
            ),
        }
    finally:
        conn.close()


def apply_omg_employee_identity(conn, employee):
    """Связывает запись API с users, не изменяя роль и локальные поля профиля."""
    employee_id = str(employee.get('id') or '').strip()
    login = normalize_login(employee.get('telegram'))
    if not employee_id:
        raise ValueError('OMG Shift вернул сотрудника без ID')

    user = conn.execute(
        'SELECT * FROM users WHERE omg_shift_employee_id=? ORDER BY ID LIMIT 1',
        (employee_id,),
    ).fetchone()
    if not user and login:
        user = conn.execute(
            'SELECT * FROM users WHERE lower(login)=lower(?) ORDER BY ID LIMIT 1',
            (login,),
        ).fetchone()
    if not user:
        return {'status': 'unlinked', 'employee_id': employee_id, 'login': login}

    first_name, second_name = parse_omg_employee_name(employee)
    target_login = login or user['login']
    if not target_login:
        return {'status': 'unlinked', 'employee_id': employee_id, 'login': None}

    duplicate = conn.execute(
        'SELECT ID FROM users WHERE lower(login)=lower(?) AND ID<>?',
        (target_login, user['ID']),
    ).fetchone()
    if duplicate:
        raise ValueError(f'Telegram {target_login} уже связан с другим пользователем')

    old_login = user['login']
    if old_login and old_login.lower() != target_login.lower():
        for table, column in LOGIN_REFERENCES.items():
            if column not in table_columns(conn, table):
                continue
            conn.execute(
                f'UPDATE "{table}" SET "{column}"=? WHERE lower("{column}")=lower(?)',
                (target_login, old_login),
            )

    conn.execute(
        """UPDATE users
           SET login=?, first_name=?, second_name=?, omg_shift_employee_id=?
           WHERE ID=?""",
        (target_login, first_name, second_name, employee_id, user['ID']),
    )
    changed = (
        old_login != target_login
        or user['first_name'] != first_name
        or user['second_name'] != second_name
        or user['omg_shift_employee_id'] != employee_id
    )
    return {
        'status': 'linked',
        'employee_id': employee_id,
        'login': target_login,
        'old_login': old_login,
        'local_status': user['status'],
        'changed': changed,
    }


def sync_google_dependencies(full=False):
    """Обновляет все найденные Google-зависимости после изменения профиля."""
    operations = [('Сотрудники', update_users)]
    if full:
        operations.append(('Открытия и закрытия', update_table_open))

        try:
            from consumables import sync_consumables_to_sheets
            operations.append(('Расходники', sync_consumables_to_sheets))
        except ImportError:
            pass

    errors = []
    for title, operation in operations:
        try:
            result = operation()
            if isinstance(result, str) and result.startswith('❌'):
                errors.append(f'{title}: {result}')
        except Exception as exc:
            errors.append(f'{title}: {exc}')
    return errors


def account_settings(message, bot):
    user = require_role(message, bot, ROLE_EMPLOYEE)
    if not user:
        return
    from birthday_greetings import birthday_public_enabled

    markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    buttons = list(funclist_acc)
    employee_id = user.get('ID') if hasattr(user, 'get') else user['ID']
    birthday_status = (
        'включено'
        if employee_id is None or birthday_public_enabled(employee_id)
        else 'выключено'
    )
    buttons.insert(-1, f'🎂 Поздравление: {birthday_status}')
    if int(user['status']) >= ROLE_MANAGER:
        buttons.insert(-1, OTHER_STATS_BUTTON)
    markup.add(*buttons)
    sent = bot.send_message(
        message.chat.id,
        'Здесь можно посмотреть профиль, синхронизировать имя с OMG Shift и изменить личные данные.',
        reply_markup=markup,
    )
    bot.register_next_step_handler(sent, func_acc, bot)


def func_acc(message, bot):
    if not require_role(message, bot, ROLE_EMPLOYEE):
        return
    if message.text == '👤 Мои данные':
        show_profile(message, bot)
    elif message.text == '🔄 Синхронизация с OMG Shift':
        sync_omg_identity_handler(message, bot)
    elif message.text == '✏️ Изменить данные':
        edit_profile_menu(message, bot)
    elif message.text == '👤 Я сменил юзернейм':
        sync_omg_identity_handler(message, bot)
    elif message.text == '📊 Статистика':
        stats_handler(message, bot)
    elif str(message.text or '').startswith('🎂 Поздравление:'):
        from birthday_greetings import toggle_birthday_public

        user = get_user_by_chat_id(message.from_user.id)
        if not user:
            bot.send_message(message.chat.id, 'Профиль не найден.')
            returnback(message, bot)
            return
        enabled = toggle_birthday_public(user['ID'])
        bot.send_message(
            message.chat.id,
            'Публичное поздравление с днём рождения '
            f'{"включено" if enabled else "выключено"}.',
        )
        account_settings(message, bot)
    elif message.text == OTHER_STATS_BUTTON:
        other_stats_prompt(message, bot)
    elif message.text == BACK_BUTTON:
        returnback(message, bot)
    else:
        account_settings(message, bot)


def returnback(message, bot):
    from menu import hello
    hello(message.chat.id, bot)


def format_birthday(value):
    if not value:
        return 'не указано'
    try:
        return datetime.strptime(str(value).split()[0], '%Y-%m-%d').strftime('%d.%m.%Y')
    except ValueError:
        return str(value)


def show_profile(message, bot):
    if not require_role(message, bot, ROLE_EMPLOYEE):
        return
    user = get_user_by_chat_id(message.from_user.id)
    if not user:
        bot.send_message(message.chat.id, 'Профиль не найден.')
        returnback(message, bot)
        return
    text = (
        '👤 Твои данные\n\n'
        f'Имя: {user["first_name"] or "не указано"}\n'
        f'Фамилия: {user["second_name"] or "не указано"}\n'
        f'Ник: {user["nick_name"] or "не указано"}\n'
        f'Telegram: {user["login"] or "не указан"}\n'
        f'День рождения: {format_birthday(user["bday"])}\n'
        f'Телефон: {user["phone"] or "не указан"}\n'
        f'Email: {user["email"] or "не указан"}\n\n'
        'Имя и фамилия принимаются из OMG Shift.'
    )
    bot.send_message(message.chat.id, text)
    account_settings(message, bot)


def edit_profile_menu(message, bot):
    if not require_role(message, bot, ROLE_EMPLOYEE):
        return
    markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add(*PROFILE_FIELDS.keys(), EDIT_BACK_BUTTON)
    sent = bot.send_message(message.chat.id, 'Что изменить?', reply_markup=markup)
    bot.register_next_step_handler(sent, select_profile_field, bot)


def select_profile_field(message, bot):
    if not require_role(message, bot, ROLE_EMPLOYEE):
        return
    if message.text == EDIT_BACK_BUTTON:
        account_settings(message, bot)
        return
    field = PROFILE_FIELDS.get(message.text)
    if not field:
        edit_profile_menu(message, bot)
        return

    prompts = {
        'nick_name': 'Введи новый ник (до 50 символов):',
        'bday': 'Введи дату рождения в формате ДД.ММ.ГГГГ:',
        'phone': 'Введи телефон в формате +79991112233:',
        'email': 'Введи email:',
    }
    markup = telebot.types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
    markup.add(EDIT_BACK_BUTTON)
    sent = bot.send_message(message.chat.id, prompts[field], reply_markup=markup)
    bot.register_next_step_handler(sent, save_profile_field, bot, field)


def validate_profile_value(field, value):
    value = str(value or '').strip()
    if field == 'nick_name':
        if not value or len(value) > 50:
            raise ValueError('Ник должен содержать от 1 до 50 символов')
        return value
    if field == 'bday':
        try:
            birthday = datetime.strptime(value, '%d.%m.%Y').date()
        except ValueError as exc:
            raise ValueError('Нужен формат ДД.ММ.ГГГГ') from exc
        if birthday > date.today():
            raise ValueError('Дата рождения не может быть в будущем')
        return birthday.isoformat()
    if field == 'phone':
        if not re.fullmatch(r'\+7\d{10}', value):
            raise ValueError('Телефон должен быть в формате +79991112233')
        return value
    if field == 'email':
        if len(value) > 254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value):
            raise ValueError('Некорректный email')
        return value
    raise ValueError('Неизвестное поле профиля')


def save_profile_field(message, bot, field):
    if not require_role(message, bot, ROLE_EMPLOYEE):
        return
    if message.text == EDIT_BACK_BUTTON:
        edit_profile_menu(message, bot)
        return
    try:
        value = validate_profile_value(field, message.text)
        conn = sqlite3.connect(DB_PATH)
        try:
            if field == 'nick_name':
                duplicate = conn.execute(
                    """SELECT 1 FROM users
                       WHERE lower(nick_name)=lower(?)
                         AND CAST(chatid AS TEXT)<>CAST(? AS TEXT)""",
                    (value, message.from_user.id),
                ).fetchone()
                if duplicate:
                    raise ValueError('Такой ник уже занят')
            with conn:
                cur = conn.execute(
                    f'UPDATE users SET "{field}"=? WHERE CAST(chatid AS TEXT)=CAST(? AS TEXT)',
                    (value, message.from_user.id),
                )
                if cur.rowcount == 0:
                    raise ValueError('Профиль не найден')
        finally:
            conn.close()
    except ValueError as exc:
        bot.send_message(message.chat.id, str(exc))
        edit_profile_menu(message, bot)
        return

    errors = sync_google_dependencies(full=False)
    if errors:
        bot.send_message(message.chat.id, 'Данные сохранены в БД, но Google Sheets пока не обновились.')
    else:
        bot.send_message(message.chat.id, 'Готово, данные обновлены.')
    edit_profile_menu(message, bot)


def sync_omg_identity_handler(message, bot):
    if not require_role(message, bot, ROLE_EMPLOYEE):
        return
    login = normalize_login(getattr(message.from_user, 'username', None))
    if not login:
        bot.send_message(message.chat.id, 'Сначала установи публичный username в Telegram.')
        account_settings(message, bot)
        return

    from rasp import register_shifton_chat
    result = register_shifton_chat(login, message.from_user.id)
    if not result.get('ok'):
        bot.send_message(
            message.chat.id,
            f'OMG Shift не нашёл сотрудника {login}: {result.get("error", "unknown_error")}',
        )
        account_settings(message, bot)
        return

    try:
        identity = apply_omg_identity(message.from_user.id, login, result.get('employee'))
    except ValueError as exc:
        bot.send_message(message.chat.id, str(exc))
        account_settings(message, bot)
        return

    errors = sync_google_dependencies(full=identity['changed'])
    text = (
        f'Готово: {identity["second_name"]} {identity["first_name"]}, '
        f'{identity["login"]}. Данные подтверждены OMG Shift.'
    )
    if errors:
        text += '\nИзменения сохранены в БД, но часть Google-таблиц обновится при следующей синхронизации.'
    bot.send_message(message.chat.id, text)
    account_settings(message, bot)


def stats_handler(message, bot):
    if not require_role(message, bot, ROLE_EMPLOYEE):
        return
    markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add('📊 За месяц', '⭐ За всё время', EDIT_BACK_BUTTON)
    sent = bot.send_message(message.chat.id, 'Какую статистику показать?', reply_markup=markup)
    bot.register_next_step_handler(sent, stats_show, bot)


def stats_show(message, bot):
    if not require_role(message, bot, ROLE_EMPLOYEE):
        return
    if message.text == EDIT_BACK_BUTTON:
        account_settings(message, bot)
    elif message.text == '📊 За месяц':
        stats_acc(message, bot)
        stats_handler(message, bot)
    elif message.text == '⭐ За всё время':
        statsall_acc(message, bot)
        stats_handler(message, bot)
    else:
        stats_handler(message, bot)


def other_stats_prompt(message, bot):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    markup = telebot.types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
    markup.add(EDIT_BACK_BUTTON)
    sent = bot.send_message(
        message.chat.id,
        '🔎 Введи Telegram username сотрудника, например @username:',
        reply_markup=markup,
    )
    bot.register_next_step_handler(sent, other_stats_select_user, bot)


def other_stats_select_user(message, bot):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    if message.text == EDIT_BACK_BUTTON:
        account_settings(message, bot)
        return
    target = get_user_by_login(message.text)
    if not target:
        bot.send_message(message.chat.id, 'Сотрудник с таким Telegram username не найден.')
        other_stats_prompt(message, bot)
        return

    display_name = target['nick_name'] or target['first_name'] or target['login']
    markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add('📊 За месяц', '⭐ За всё время', EDIT_BACK_BUTTON)
    sent = bot.send_message(
        message.chat.id,
        f'📊 Какую статистику показать для {target["login"]}?',
        reply_markup=markup,
    )
    bot.register_next_step_handler(
        sent,
        other_stats_show,
        bot,
        target['login'],
        display_name,
    )


def other_stats_show(message, bot, login, display_name):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    if message.text == EDIT_BACK_BUTTON:
        account_settings(message, bot)
        return
    if message.text == '📊 За месяц':
        send_monthly_stats(message, bot, login, display_name)
    elif message.text == '⭐ За всё время':
        send_all_time_stats(message, bot, login, display_name)
    else:
        bot.send_message(message.chat.id, 'Выбери период с помощью кнопок.')
        other_stats_prompt(message, bot)
        return
    account_settings(message, bot)


def get_main_kpi(login, now=None):
    now = now or datetime.now(pytz.timezone('Europe/Moscow'))
    selected_date = now.strftime('%Y-%m-%d')
    normalized_login = str(login or '').strip().lower()
    if normalized_login not in set(active_kpi_employee_logins(DB_PATH)):
        raise ValueError('Сотрудник не найден среди активных участников KPI')
    rows = calculate_monthly_kpi(
        now.replace(day=1).strftime('%Y-%m-%d'),
        db_path=DB_PATH,
        employee_logins=[normalized_login],
        period_end=selected_date,
    )
    row = next(
        (item for item in rows if item['login'] == normalized_login),
        None,
    )
    if not row:
        raise ValueError('Не удалось рассчитать KPI сотрудника')
    return {**row, 'date': selected_date}


def get_database_stats(login, start=None, end=None):
    conn = sqlite3.connect(DB_PATH)
    try:
        date_filter = ''
        params = [login]
        if start and end:
            date_filter = ' AND date(dt_rep) BETWEEN date(?) AND date(?)'
            params.extend([start, end])

        union_sql = sql_scripts.union.strip().rstrip(';')
        rows = conn.execute(
            f"""SELECT kpi, SUM(fact)
                FROM ({union_sql}) source
                WHERE lower(s_name)=lower(?) {date_filter}
                GROUP BY kpi""",
            params,
        ).fetchall()
        result = {name: value or 0 for name, value in rows}

        shift_params = [login]
        shift_date_filter = ''
        if start and end:
            shift_date_filter = (
                ' AND date(substr(dt_shift, 1, 10)) BETWEEN date(?) AND date(?)'
            )
            shift_params.extend([start, end])
        shift_row = conn.execute(
            f"""SELECT COALESCE(SUM(dur),0), COALESCE(SUM(dur)/6.0,0)
                FROM shifts
                WHERE lower(shift_login)=lower(?) {shift_date_filter}""",
            shift_params,
        ).fetchone()
        result['Часы'] = shift_row[0]
        result['Смены'] = shift_row[1]

        extra_hashtags = {
            'Автосимы': '#автосим',
            'Активации': '#активация',
            'Двойные часы': '#двойная',
        }
        hashtag_columns = table_columns(conn, 'hashtag_events')
        for label, hashtag in extra_hashtags.items():
            if not {'telegram', 'hashtag', 'value', 'event_date', 'status'}.issubset(hashtag_columns):
                result[label] = 0
                continue
            extra_params = [login]
            extra_filter = ''
            if start and end:
                extra_filter = ' AND date(event_date) BETWEEN date(?) AND date(?)'
                extra_params.extend([start, end])
            value = conn.execute(
                f'''SELECT COALESCE(SUM(value),0)
                    FROM hashtag_events
                    WHERE lower(telegram)=lower(?)
                      AND hashtag=? AND status='applied' {extra_filter}''',
                [extra_params[0], hashtag, *extra_params[1:]],
            ).fetchone()[0]
            result[label] = value or 0
        return result
    finally:
        conn.close()


def format_number(value):
    try:
        number = float(value)
        return f'{number:g}'
    except (TypeError, ValueError):
        return str(value)


def format_kpi_percent(value):
    raw = str(value or '').strip()
    if raw.endswith('%'):
        return raw
    try:
        percent = float(value or 0) * 100
    except (TypeError, ValueError):
        return raw
    return f'{percent:.1f}'.rstrip('0').rstrip('.') + '%'


def escape_html(value):
    return html.escape(str(value))


def format_report_date(value):
    raw_value = str(value or '').strip()
    for date_format in ('%d.%m.%Y', '%Y-%m-%d'):
        try:
            parsed = datetime.strptime(raw_value, date_format)
            months = (
                'январь', 'февраль', 'март', 'апрель', 'май', 'июнь',
                'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь',
            )
            return f'{months[parsed.month - 1]} {parsed.year}', parsed.strftime('%d.%m.%Y')
        except ValueError:
            continue
    return 'текущий месяц', raw_value


def format_main_kpi(data, extras=None):
    extras = extras or {}
    period, report_date = format_report_date(data['date'])
    return (
        f'📊 <b>KPI за {escape_html(period)}</b>\n'
        f'👤 <b>{escape_html(data["nickname"])}</b>\n'
        f'📅 <i>Расчётная дата: {escape_html(report_date)}</i>\n\n'
        f'📈 <b>Основные показатели</b>\n\n'
        f'🕒 Смены: <b>{escape_html(format_number(data["shifts"]))}</b> '
        f'<i>({escape_html(format_number(data["weighted_shifts"]))} взв.)</i>\n'
        f'⭐ Отзывы: <b>{escape_html(format_number(data["reviews"]))}</b> '
        f'<i>({escape_html(format_kpi_percent(data["reviews_pct"]))})</i>\n'
        f'📝 Анкеты: <b>{escape_html(format_number(data["forms"]))}</b> '
        f'<i>({escape_html(format_kpi_percent(data["forms_pct"]))})</i>\n'
        f'🔄 Продления: <b>{escape_html(format_number(data["extensions"]))}</b> '
        f'<i>({escape_html(format_kpi_percent(data["extensions_pct"]))})</i>\n'
        f'🎫 Сертификаты: <b>{escape_html(format_number(data["certificates"]))}</b> '
        f'<i>({escape_html(format_kpi_percent(data["certificates_pct"]))})</i>\n'
        f'💳 Абонементы: <b>{escape_html(format_number(data["subscriptions"]))}</b> '
        f'<i>({escape_html(format_kpi_percent(data["subscriptions_pct"]))})</i>\n'
        f'💡 Инициативы: <b>{escape_html(format_number(data["initiatives"]))}</b> '
        f'<i>({escape_html(format_kpi_percent(data["initiatives_pct"]))})</i>\n'
        f'⚠️ Штрафы: <b>{escape_html(format_number(data["penalties"]))}</b>\n\n'
        f'🏆 <b>Результат</b>\n\n'
        f'🎯 Итого KPI: <b>{escape_html(format_kpi_percent(data["total_pct"]))}</b> '
        f'<i>({escape_html(format_kpi_percent(data["weighted_pct"]))} взв.)</i>\n'
        f'🥇 Рейтинг: <b>{escape_html(data["rank"] or "нет")}</b>\n'
        '\n'
        f'🚀 <b>Дополнительные показатели</b>\n\n'
        f'🚘 Автосимы: <b>{escape_html(format_number(extras.get("Автосимы", 0)))}</b>\n'
        f'⚡ Активации: <b>{escape_html(format_number(extras.get("Активации", 0)))}</b>\n'
        f'⏱️ Двойные часы: <b>{escape_html(format_number(extras.get("Двойные часы", 0)))}</b>\n\n'
        f'ℹ️ <i>В скобках указан процент выполнения. Пометка «взв.» означает '
        f'значение с учётом коэффициентов.</i>'
    )


def format_database_stats(stats, title, nickname=None):
    lines = [f'<b>{escape_html(title)}</b>']
    if nickname:
        lines.extend([f'👤 <b>{escape_html(nickname)}</b>'])
    lines.extend([
        '',
        '⏱️ <b>Рабочее время</b>',
        '',
        f'🕒 Часы: <b>{escape_html(format_number(stats.get("Часы", 0)))}</b>',
        f'📆 Смены: <b>{escape_html(format_number(stats.get("Смены", 0)))}</b>',
        '',
        '📈 <b>Показатели KPI</b>',
        '',
    ])
    kpi_icons = {
        'Отзывы': '⭐',
        'Анкеты': '📝',
        'Продления': '🔄',
        'Сертификаты': '🎫',
        'Абонементы': '💳',
        'Инициативы': '💡',
        'ДР': '🎂',
        'Штрафы': '⚠️',
    }
    for name, icon in kpi_icons.items():
        label = 'Дни рождения' if name == 'ДР' else name
        lines.append(
            f'{icon} {label}: <b>{escape_html(format_number(stats.get(name, 0)))}</b>'
        )
    lines.extend([
        '',
        '🚀 <b>Дополнительные показатели</b>',
        '',
        f'🚘 Автосимы: <b>{escape_html(format_number(stats.get("Автосимы", 0)))}</b>',
        f'⚡ Активации: <b>{escape_html(format_number(stats.get("Активации", 0)))}</b>',
        f'⏱️ Двойные часы: <b>{escape_html(format_number(stats.get("Двойные часы", 0)))}</b>',
    ])
    return '\n'.join(lines)


def send_monthly_stats(message, bot, login, display_name):
    try:
        data = get_main_kpi(login)
        now = datetime.now(pytz.timezone('Europe/Moscow'))
        start = now.replace(day=1).strftime('%Y-%m-%d')
        end = now.strftime('%Y-%m-%d')
        extras = get_database_stats(login, start, end)
        text = format_main_kpi(data, extras)
    except Exception as exc:
        now = datetime.now(pytz.timezone('Europe/Moscow'))
        start = now.replace(day=1).strftime('%Y-%m-%d')
        end = now.strftime('%Y-%m-%d')
        stats = get_database_stats(login, start, end)
        text = format_database_stats(
            stats,
            '📊 Статистика за текущий месяц',
            display_name,
        )
        text += (
            '\n\n⚠️ <i>Не удалось выполнить внутренний расчёт KPI: '
            f'{escape_html(exc)}</i>'
        )
    bot.send_message(message.chat.id, text, parse_mode='HTML')


def send_all_time_stats(message, bot, login, display_name):
    stats = get_database_stats(login)
    text = format_database_stats(stats, '⭐ Статистика за всё время', display_name)
    bot.send_message(message.chat.id, text, parse_mode='HTML')


def stats_acc(message, bot):
    if not require_role(message, bot, ROLE_EMPLOYEE):
        return
    user = get_user_by_chat_id(message.from_user.id)
    if not user or not user['login']:
        bot.send_message(message.chat.id, 'Сначала синхронизируй профиль с OMG Shift.')
        return
    display_name = user['nick_name'] or user['first_name'] or user['login']
    send_monthly_stats(message, bot, user['login'], display_name)


def statsall_acc(message, bot):
    if not require_role(message, bot, ROLE_EMPLOYEE):
        return
    user = get_user_by_chat_id(message.from_user.id)
    if not user or not user['login']:
        bot.send_message(message.chat.id, 'Сначала синхронизируй профиль с OMG Shift.')
        return
    display_name = user['nick_name'] or user['first_name'] or user['login']
    send_all_time_stats(message, bot, user['login'], display_name)
