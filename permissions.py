import sqlite3
from datetime import datetime, timedelta, timezone


DB_PATH = 'db/omgbot.sql'

ROLE_BLOCKED = -1
ROLE_EMPLOYEE = 0
ROLE_TECHNICIAN = 1
ROLE_MANAGER = 2
ROLE_OWNER = 3

ROLE_NAMES = {
    ROLE_BLOCKED: 'Заблокирован',
    ROLE_EMPLOYEE: 'Сотрудник',
    ROLE_TECHNICIAN: 'Ремонтник',
    ROLE_MANAGER: 'Менеджер',
    ROLE_OWNER: 'Руководство',
}

ACTIVE_ROLES = {ROLE_EMPLOYEE, ROLE_TECHNICIAN, ROLE_MANAGER, ROLE_OWNER}


def initialize_permissions_schema():
    """Создаёт журнал изменений ролей."""
    conn = sqlite3.connect(DB_PATH)
    try:
        with conn:
            conn.execute(
                '''CREATE TABLE IF NOT EXISTS role_audit (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       changed_at TEXT NOT NULL,
                       actor_chatid TEXT NOT NULL,
                       actor_login TEXT,
                       target_chatid TEXT,
                       target_login TEXT,
                       old_status INTEGER,
                       new_status INTEGER NOT NULL
                   )'''
            )
    finally:
        conn.close()


def _now():
    moscow = timezone(timedelta(hours=3))
    return datetime.now(moscow).strftime('%Y-%m-%d %H:%M:%S')


def actor_id(update):
    if isinstance(update, (int, str)) and str(update).lstrip('-').isdigit():
        return int(update)
    user = getattr(update, 'from_user', None)
    user_id = getattr(user, 'id', None)
    if user_id is not None and not getattr(user, 'is_bot', False):
        return user_id
    # Callback-обработчики старого кода часто передают дальше call.message.
    # В личном чате его chat.id совпадает с Telegram ID владельца диалога.
    chat = getattr(update, 'chat', None)
    chat_id = getattr(chat, 'id', None)
    if chat_id is not None and int(chat_id) > 0:
        return chat_id
    return user_id


def get_user(update=None, telegram_id=None):
    telegram_id = telegram_id if telegram_id is not None else actor_id(update)
    if telegram_id is None:
        return None
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            '''SELECT * FROM users
               WHERE CAST(chatid AS TEXT)=CAST(? AS TEXT)
               ORDER BY ID LIMIT 1''',
            (telegram_id,),
        ).fetchone()
    finally:
        conn.close()


def role_of(update=None, telegram_id=None):
    user = get_user(update, telegram_id)
    if not user or user['status'] not in ACTIVE_ROLES:
        return None
    return int(user['status'])


def require_role(update, bot, minimum=ROLE_EMPLOYEE, notify=True):
    """Проверяет активность и минимальную роль по неизменяемому Telegram ID."""
    user = get_user(update)
    status = user['status'] if user else None
    allowed = status in ACTIVE_ROLES and int(status) >= minimum
    if allowed:
        return user

    if notify:
        if not user or status is None:
            text = 'Сначала зарегистрируйтесь: отправьте боту /start в личных сообщениях.'
        elif status == ROLE_BLOCKED:
            text = 'Доступ запрещён. Учётная запись заблокирована.'
        else:
            text = f'Недостаточно прав. Требуется роль «{ROLE_NAMES[minimum]}» или выше.'
        _deny(update, bot, text)
    return None


def _deny(update, bot, text):
    if hasattr(update, 'data') and hasattr(update, 'message'):
        try:
            bot.answer_callback_query(update.id, text, show_alert=True)
            return
        except Exception:
            chat_id = getattr(getattr(update, 'message', None), 'chat', None)
            chat_id = getattr(chat_id, 'id', actor_id(update))
    else:
        chat = getattr(update, 'chat', None)
        chat_id = getattr(chat, 'id', actor_id(update))
        if chat_id is None:
            chat_id = actor_id(update)
    if chat_id is not None:
        bot.send_message(chat_id, text)


def change_role(actor, target_id, new_status):
    if new_status not in {ROLE_BLOCKED, *ACTIVE_ROLES}:
        raise ValueError('Неизвестная роль')

    actor_user = get_user(actor)
    if not actor_user or actor_user['status'] != ROLE_OWNER:
        raise PermissionError('Менять роли может только руководство')

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            target = conn.execute(
                'SELECT * FROM users WHERE ID=?',
                (target_id,),
            ).fetchone()
            if not target:
                raise ValueError('Пользователь не найден')

            old_status = target['status']
            if old_status == ROLE_OWNER and new_status != ROLE_OWNER:
                owners = conn.execute(
                    'SELECT COUNT(*) FROM users WHERE status=?',
                    (ROLE_OWNER,),
                ).fetchone()[0]
                if owners <= 1:
                    raise ValueError('Нельзя понизить последнего пользователя с ролью 3')

            if old_status != new_status:
                conn.execute(
                    'UPDATE users SET status=? WHERE ID=?',
                    (new_status, target_id),
                )
                conn.execute(
                    '''INSERT INTO role_audit
                       (changed_at, actor_chatid, actor_login, target_chatid,
                        target_login, old_status, new_status)
                       VALUES (?, ?, ?, ?, ?, ?, ?)''',
                    (
                        _now(), actor_user['chatid'], actor_user['login'],
                        target['chatid'], target['login'], old_status, new_status,
                    ),
                )
            return dict(target), old_status
    finally:
        conn.close()
