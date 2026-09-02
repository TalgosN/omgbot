import html
import io
import json
import os
import re
import sqlite3
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telebot import types

from club_config import get_clubs
from constants import CHATS, KPI_WEBAPP_URL
from permissions import ROLE_EMPLOYEE, ROLE_MANAGER, get_user, require_role


DB_PATH = 'db/omgbot.sql'
MOSCOW = ZoneInfo('Europe/Moscow')
MAX_ATTACHMENTS = 10

KIND_INFORMATION = 'information'
KIND_TASK = 'task'
CATEGORY_CLEANLINESS = 'cleanliness'
CATEGORY_DEEP_CLEANING = 'deep_cleaning'
CATEGORY_DEEP_CLEANING_SPECIAL = 'deep_cleaning_special'
CATEGORY_GENERAL = 'general'

_task_admin_drafts = {}
_task_app_completion_lock = threading.Lock()

DEFAULT_CLEANLINESS_POINTS = (
    'Лаунж — общий вид',
    'Ресепшен — рабочие поверхности и пол',
    'Санузел — общий вид',
    'Раковина и тумба',
    'Унитаз и пол вокруг',
    'Бэк — общий порядок',
    'Зеркало без разводов',
)

CLEANLINESS_POINTS_BY_CLUB = {
    'Марьино': (
        'Лаунж — общий вид',
        'Ресепшен — рабочие поверхности и пол',
        'Бэк — общий порядок',
    ),
    'Каширка': (
        'Малый зал',
        'Большой зал',
        'Ресепшен',
        'Бэк',
        'Туалет №1',
        'Туалет №2',
        'Раковина, тумба и зеркало',
    ),
}

LEGACY_TASKS = {
    7: ('Уход за VR-оборудованием', '23:00'),
    8: ('Пыль и полки', '20:00'),
    9: ('Лаунж и бэк', '20:00'),
    10: ('Глубокая уборка санузла', '20:00'),
    11: ('Глубокая уборка ресепшена', '20:00'),
    12: ('Вход и двери', '20:00'),
}

LEGACY_CLUB_MENTIONS = {
    '@omgvr_len': 'Ленинский',
    '@omgvr_mar': 'Марьино',
    '@omgvr_kash': 'Каширка',
    '@omgvr_prokshino': 'Прокшино',
    '@omgvr_dmi': 'Дмитровка',
}

LEGACY_MARYINO_SPECIALS = {
    10: (
        'Глубокая уборка бэка',
        'Проведите глубокую уборку бэка и приложите фото или видео результата.',
    ),
    12: (
        'Проверка чистоты клуба',
        'Проверьте, что задачи по уборке за неделю выполнены и клуб чистый. '
        'Приложите фото или видео результата.',
    ),
}


def _now(value=None):
    current = value or datetime.now(MOSCOW)
    if current.tzinfo is None:
        current = current.replace(tzinfo=MOSCOW)
    return current.astimezone(MOSCOW)


def _timestamp(value=None):
    return _now(value).strftime('%Y-%m-%d %H:%M:%S')


def _add_column(conn, table, definition):
    column = definition.split()[0]
    columns = {row[1] for row in conn.execute(f'PRAGMA table_info({table})')}
    if column not in columns:
        conn.execute(f'ALTER TABLE {table} ADD COLUMN {definition}')


def initialize_shift_tasks_schema(db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            conn.execute(
                '''CREATE TABLE IF NOT EXISTS broadcasts (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       text TEXT,
                       photo TEXT,
                       time TEXT,
                       freq_type TEXT,
                       freq_days TEXT,
                       status INTEGER DEFAULT 1
                   )'''
            )
            for definition in (
                "kind TEXT NOT NULL DEFAULT 'information'",
                'title TEXT',
                'clubs_json TEXT',
                'due_time TEXT',
                'announce_main INTEGER NOT NULL DEFAULT 0',
                'task_category TEXT',
                'created_by TEXT',
                'created_at TEXT',
                'updated_at TEXT',
            ):
                _add_column(conn, 'broadcasts', definition)

            conn.execute(
                '''CREATE TABLE IF NOT EXISTS shift_task_requirements (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       template_id INTEGER NOT NULL,
                       club TEXT NOT NULL,
                       position INTEGER NOT NULL,
                       label TEXT NOT NULL,
                       UNIQUE(template_id, club, position)
                   )'''
            )
            conn.execute(
                '''CREATE TABLE IF NOT EXISTS shift_task_instances (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       template_id INTEGER NOT NULL,
                       occurrence_date TEXT NOT NULL,
                       club TEXT NOT NULL,
                       title TEXT NOT NULL,
                       instructions TEXT,
                       requirements_json TEXT NOT NULL DEFAULT '[]',
                       available_at TEXT NOT NULL,
                       due_at TEXT NOT NULL,
                       status TEXT NOT NULL DEFAULT 'pending',
                       activated_at TEXT,
                       started_at TEXT,
                       started_by_login TEXT,
                       completed_at TEXT,
                       completed_by_login TEXT,
                       completed_by_name TEXT,
                       completed_by_chatid TEXT,
                       skipped_at TEXT,
                       skipped_by_login TEXT,
                       skipped_by_name TEXT,
                       skipped_by_chatid TEXT,
                       skip_reason TEXT,
                       overdue_notified_at TEXT,
                       report_chatid TEXT,
                       report_message_ids TEXT,
                       main_announced_at TEXT,
                       created_at TEXT NOT NULL,
                       UNIQUE(template_id, occurrence_date, club)
                   )'''
            )
            conn.execute(
                '''CREATE INDEX IF NOT EXISTS idx_shift_task_instances_date_status
                   ON shift_task_instances(occurrence_date, status, due_at)'''
            )
            conn.execute(
                '''CREATE TABLE IF NOT EXISTS shift_task_media (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       instance_id INTEGER NOT NULL,
                       telegram_file_id TEXT NOT NULL,
                       telegram_file_unique_id TEXT,
                       telegram_message_id INTEGER,
                       media_group_id TEXT,
                       media_type TEXT NOT NULL,
                       file_size INTEGER,
                       submitted_by_chatid TEXT NOT NULL,
                       submitted_by_login TEXT,
                       created_at TEXT NOT NULL,
                       state TEXT NOT NULL DEFAULT 'draft',
                       UNIQUE(instance_id, telegram_file_unique_id)
                   )'''
            )
            for definition in (
                'telegram_message_id INTEGER',
                'media_group_id TEXT',
            ):
                _add_column(conn, 'shift_task_media', definition)
            conn.execute(
                '''CREATE INDEX IF NOT EXISTS idx_shift_task_media_instance
                   ON shift_task_media(instance_id, state, id)'''
            )
            conn.execute(
                '''CREATE TABLE IF NOT EXISTS shift_task_drafts (
                       chatid TEXT PRIMARY KEY,
                       instance_id INTEGER NOT NULL,
                       state TEXT NOT NULL,
                       skip_reason TEXT,
                       updated_at TEXT NOT NULL
                   )'''
            )
            conn.execute(
                '''CREATE TABLE IF NOT EXISTS shift_task_notifications (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       instance_id INTEGER NOT NULL,
                       notification_type TEXT NOT NULL,
                       recipient_chatid TEXT NOT NULL,
                       telegram_message_id TEXT,
                       sent_at TEXT NOT NULL,
                       UNIQUE(instance_id, notification_type, recipient_chatid)
                   )'''
            )
            conn.execute(
                '''CREATE TABLE IF NOT EXISTS shift_task_close_warnings (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       run_id TEXT NOT NULL UNIQUE,
                       occurrence_date TEXT NOT NULL,
                       club TEXT NOT NULL,
                       actor_login TEXT,
                       actor_name TEXT,
                       actor_chatid TEXT,
                       task_ids_json TEXT NOT NULL,
                       warned_at TEXT NOT NULL,
                       report_sent_at TEXT
                   )'''
            )
            legacy_draft_instances = [
                row[0] for row in conn.execute(
                    'SELECT DISTINCT instance_id FROM shift_task_drafts'
                ).fetchall()
            ]
            if legacy_draft_instances:
                placeholders = ','.join('?' for _ in legacy_draft_instances)
                conn.execute(
                    f'''UPDATE shift_task_instances
                        SET status='pending', started_at=NULL,
                            started_by_login=NULL
                        WHERE id IN ({placeholders}) AND status='in_progress' ''',
                    legacy_draft_instances,
                )
            conn.execute("DELETE FROM shift_task_media WHERE state='draft'")
            conn.execute('DELETE FROM shift_task_drafts')
        _migrate_legacy_cleaning_broadcasts(conn)
        _ensure_cleanliness_template(conn)
    finally:
        conn.close()


def _legacy_task_instructions(text):
    lines = []
    for raw_line in str(text or '').splitlines():
        line = raw_line.strip()
        lowered = line.casefold()
        if not line:
            continue
        if any(mention in lowered for mention in LEGACY_CLUB_MENTIONS):
            continue
        if lowered.startswith(('понедельник -', 'вторник -', 'четверг -', 'пятница -', 'суббота /')):
            continue
        if 'срок выполнения задачи' in lowered:
            continue
        if 'отписаться в чат' in lowered or 'ждем фото' in lowered:
            continue
        if lowered.startswith('напоминалка:'):
            continue
        lines.append(line)
    return '\n'.join(lines).strip()


def _migrate_legacy_cleaning_broadcasts(conn):
    rows = conn.execute(
        '''SELECT id, text, kind, time, freq_type, freq_days, status
           FROM broadcasts
           WHERE id IN (7, 8, 9, 10, 11, 12)'''
    ).fetchall()
    for broadcast_id, text, kind, start_time, freq_type, freq_days, status in rows:
        if kind == KIND_TASK:
            continue
        title, due_time = LEGACY_TASKS[broadcast_id]
        lowered = str(text or '').casefold()
        clubs = [
            club for mention, club in LEGACY_CLUB_MENTIONS.items()
            if mention in lowered
        ]
        if not clubs:
            continue
        special = LEGACY_MARYINO_SPECIALS.get(broadcast_id)
        if special and 'Марьино' in clubs:
            clubs.remove('Марьино')
            marker = f'legacy:{broadcast_id}:maryino'
            exists = conn.execute(
                'SELECT 1 FROM broadcasts WHERE created_by=? LIMIT 1',
                (marker,),
            ).fetchone()
            if not exists:
                special_title, special_text = special
                conn.execute(
                    '''INSERT INTO broadcasts (
                           text, photo, time, freq_type, freq_days, status,
                           kind, title, clubs_json, due_time, announce_main,
                           task_category, created_by, created_at, updated_at
                       ) VALUES (?, 'None', ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)''',
                    (
                        special_text, start_time, freq_type, freq_days, status,
                        KIND_TASK, special_title,
                        json.dumps(['Марьино'], ensure_ascii=False), due_time,
                        CATEGORY_DEEP_CLEANING_SPECIAL, marker,
                        _timestamp(), _timestamp(),
                    ),
                )
        conn.execute(
            '''UPDATE broadcasts
               SET kind=?, title=?, text=?, clubs_json=?, due_time=?,
                   announce_main=0, task_category=?, updated_at=?
               WHERE id=?''',
            (
                KIND_TASK, title, _legacy_task_instructions(text),
                json.dumps(clubs, ensure_ascii=False), due_time,
                CATEGORY_DEEP_CLEANING, _timestamp(), broadcast_id,
            ),
        )
    conn.commit()


def _ensure_cleanliness_template(conn):
    row = conn.execute(
        '''SELECT id FROM broadcasts
           WHERE kind=? AND task_category=? ORDER BY id LIMIT 1''',
        (KIND_TASK, CATEGORY_CLEANLINESS),
    ).fetchone()
    physical_clubs = [
        name for name, settings in get_clubs().items()
        if settings.get('is_physical') and settings.get('require_geo')
    ]
    if row:
        template_id = int(row[0])
    else:
        cursor = conn.execute(
            '''INSERT INTO broadcasts (
                   text, photo, time, freq_type, freq_days, status, kind,
                   title, clubs_json, due_time, announce_main, task_category,
                   created_at, updated_at
               ) VALUES (?, 'None', '12:00', 'daily', '', 1, ?, ?, ?,
                         '20:00', 0, ?, ?, ?)''',
            (
                'Сделайте фотографии каждого пункта после уборки.',
                KIND_TASK,
                'Ежедневная чистота',
                json.dumps(physical_clubs, ensure_ascii=False),
                CATEGORY_CLEANLINESS,
                _timestamp(),
                _timestamp(),
            ),
        )
        template_id = cursor.lastrowid
    for club in physical_clubs:
        existing = conn.execute(
            '''SELECT COUNT(*) FROM shift_task_requirements
               WHERE template_id=? AND club=?''',
            (template_id, club),
        ).fetchone()[0]
        if existing:
            continue
        points = CLEANLINESS_POINTS_BY_CLUB.get(
            club, DEFAULT_CLEANLINESS_POINTS,
        )
        conn.executemany(
            '''INSERT INTO shift_task_requirements
               (template_id, club, position, label) VALUES (?, ?, ?, ?)''',
            [
                (template_id, club, index, label)
                for index, label in enumerate(points, 1)
            ],
        )
    conn.commit()


def _frequency_matches(template, day):
    frequency = str(template['freq_type'] or '')
    if frequency == 'daily':
        return True
    if frequency == 'custom':
        return str(day.weekday()) in str(template['freq_days'] or '')
    if frequency == 'once':
        return True
    return False


def _template_clubs(template):
    try:
        clubs = json.loads(template['clubs_json'] or '[]')
    except (TypeError, json.JSONDecodeError):
        return []
    available = get_clubs()
    return [str(club) for club in clubs if str(club) in available]


def _requirements_for(conn, template_id, club):
    return [
        row[0] for row in conn.execute(
            '''SELECT label FROM shift_task_requirements
               WHERE template_id=? AND club=? ORDER BY position, id''',
            (template_id, club),
        ).fetchall()
    ]


def ensure_task_instances(
    occurrence_date=None, db_path=DB_PATH, now=None, skip_expired=False,
):
    current = _now(now)
    day = occurrence_date or current.date()
    if isinstance(day, str):
        day = datetime.strptime(day, '%Y-%m-%d').date()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    created = []
    try:
        templates = conn.execute(
            '''SELECT * FROM broadcasts
               WHERE status=1 AND kind=? ORDER BY id''',
            (KIND_TASK,),
        ).fetchall()
        with conn:
            for template in templates:
                if not _frequency_matches(template, day):
                    continue
                start_time = str(template['time'] or '').strip()
                due_time = str(template['due_time'] or '').strip()
                try:
                    available_at = datetime.strptime(
                        f'{day.isoformat()} {start_time}', '%Y-%m-%d %H:%M',
                    ).replace(tzinfo=MOSCOW)
                    due_at = datetime.strptime(
                        f'{day.isoformat()} {due_time}', '%Y-%m-%d %H:%M',
                    ).replace(tzinfo=MOSCOW)
                except ValueError:
                    continue
                if due_at <= available_at:
                    continue
                if skip_expired and day == current.date() and current >= due_at:
                    continue
                for club in _template_clubs(template):
                    requirements = _requirements_for(conn, template['id'], club)
                    cursor = conn.execute(
                        '''INSERT OR IGNORE INTO shift_task_instances (
                               template_id, occurrence_date, club, title,
                               instructions, requirements_json, available_at,
                               due_at, created_at
                           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                        (
                            template['id'], day.isoformat(), club,
                            template['title'] or 'Задача смены',
                            template['text'] or '',
                            json.dumps(requirements, ensure_ascii=False),
                            _timestamp(available_at), _timestamp(due_at),
                            _timestamp(current),
                        ),
                    )
                    if cursor.rowcount:
                        created.append(cursor.lastrowid)
            for template in templates:
                if template['freq_type'] != 'once':
                    continue
                if any(
                    row[0] == template['id']
                    for row in conn.execute(
                        '''SELECT DISTINCT template_id FROM shift_task_instances
                           WHERE occurrence_date=?''',
                        (day.isoformat(),),
                    )
                ):
                    conn.execute(
                        'UPDATE broadcasts SET status=0, updated_at=? WHERE id=?',
                        (_timestamp(current), template['id']),
                    )
    finally:
        conn.close()
    return created


def _scheduled_recipients(conn, instance):
    rows = conn.execute(
        '''SELECT DISTINCT CAST(employee.chatid AS TEXT), employee.login,
                          COALESCE(NULLIF(employee.nick_name, ''),
                                   NULLIF(employee.first_name, ''),
                                   employee.login)
           FROM shifts shift_row
           JOIN users employee ON (
               shift_row.shift_login IS NOT NULL
               AND lower(shift_row.shift_login)=lower(employee.login)
           ) OR (
               shift_row.shift_login IS NULL
               AND shift_row.shift_second_name=employee.second_name
               AND shift_row.shift_first_name=employee.first_name
           )
           WHERE date(substr(shift_row.dt_shift, 1, 10))=date(?)
             AND lower(trim(shift_row.club))=lower(trim(?))
             AND employee.status>=? AND employee.chatid IS NOT NULL
             AND trim(CAST(employee.chatid AS TEXT))<>''
           ORDER BY employee.login''',
        (instance['occurrence_date'], instance['club'], ROLE_EMPLOYEE),
    ).fetchall()
    recipients = [tuple(row) for row in rows]
    if recipients:
        return recipients
    opener = conn.execute(
        '''SELECT CAST(employee.chatid AS TEXT), employee.login,
                  COALESCE(NULLIF(employee.nick_name, ''),
                           NULLIF(employee.first_name, ''), employee.login)
           FROM activity activity_row
           JOIN users employee ON lower(employee.login)=lower(activity_row.login)
           WHERE date(activity_row.dtrep)=date(?)
             AND lower(trim(activity_row.club))=lower(trim(?))
             AND activity_row.action LIKE '%Открыть%'
             AND employee.status>=? AND employee.chatid IS NOT NULL
           ORDER BY datetime(activity_row.dtrep) DESC LIMIT 1''',
        (instance['occurrence_date'], instance['club'], ROLE_EMPLOYEE),
    ).fetchone()
    return [tuple(opener)] if opener else []


def _instance_requirements(instance):
    try:
        values = json.loads(instance['requirements_json'] or '[]')
    except (TypeError, json.JSONDecodeError):
        return []
    return [str(value) for value in values if str(value).strip()]


def _task_card(instance, notification_type='activation'):
    labels = {
        'opening': 'Сегодня в клубе',
        'activation': 'Задача доступна',
        'reminder_3h': 'Напоминание',
        'due_1h': 'До срока остался час',
        'overdue': 'Срок задачи истёк',
    }
    heading = labels.get(notification_type, 'Задача смены')
    requirements = _instance_requirements(instance)
    lines = [
        f'📋 <b>{html.escape(heading)}</b>',
        '',
        f'<b>{html.escape(instance["title"])}</b>',
        f'📍 {html.escape(instance["club"])}',
        f'⏰ Выполнить до {html.escape(instance["due_at"][11:16])}',
    ]
    if notification_type == 'opening':
        lines.append(f'🔔 Задача появится в {html.escape(instance["available_at"][11:16])}')
    if instance['instructions']:
        lines.extend(['', html.escape(instance['instructions'][:1200])])
    if requirements:
        lines.extend(['', '<b>Что должно быть в отчёте:</b>'])
        lines.extend(
            f'{index}. {html.escape(label[:120])}'
            for index, label in enumerate(requirements, 1)
        )
        lines.append('Отправляйте вложения в этом же порядке.')
    minimum = len(requirements) if requirements else 1
    lines.extend(['', (
        f'Нужно вложений: {minimum}. Максимум — {MAX_ATTACHMENTS}. '
        'Можно отправлять фото и видео, видео — до 20 МБ.'
    )])
    return '\n'.join(lines)


def _task_webapp_url(instance_id=None):
    base_url = str(KPI_WEBAPP_URL or '').strip()
    runtime_path = os.path.join('data', 'kpi_webapp_url.txt')
    try:
        with open(runtime_path, encoding='utf-8') as runtime_file:
            runtime_url = runtime_file.read().strip()
    except OSError:
        runtime_url = ''
    if runtime_url:
        base_url = runtime_url
    if not base_url:
        return None
    url = f'{base_url.rstrip("/")}/shift/tasks'
    if instance_id is not None:
        url += f'?task={int(instance_id)}'
    return url


def _task_markup(instance_id, opening=False):
    markup = types.InlineKeyboardMarkup()
    webapp_url = _task_webapp_url(None if opening else instance_id)
    if webapp_url:
        markup.add(types.InlineKeyboardButton(
            '📋 Открыть в OMG Shift',
            web_app=types.WebAppInfo(webapp_url),
        ))
        return markup
    return None


def _notification_exists(conn, instance_id, kind, chatid):
    return conn.execute(
        '''SELECT 1 FROM shift_task_notifications
           WHERE instance_id=? AND notification_type=? AND recipient_chatid=?''',
        (instance_id, kind, str(chatid)),
    ).fetchone() is not None


def _send_direct_notification(bot, conn, instance, kind, recipient):
    chatid = str(recipient[0])
    if _notification_exists(conn, instance['id'], kind, chatid):
        return False
    message = bot.send_message(
        chatid,
        _task_card(instance, kind),
        parse_mode='HTML',
        reply_markup=_task_markup(instance['id'], opening=kind == 'opening'),
    )
    conn.execute(
        '''INSERT OR IGNORE INTO shift_task_notifications
           (instance_id, notification_type, recipient_chatid,
            telegram_message_id, sent_at)
           VALUES (?, ?, ?, ?, ?)''',
        (
            instance['id'], kind, chatid,
            str(getattr(message, 'message_id', '')), _timestamp(),
        ),
    )
    return True


def _opening_recipients(conn, instance):
    rows = conn.execute(
        '''SELECT DISTINCT CAST(employee.chatid AS TEXT), employee.login,
                          COALESCE(NULLIF(employee.nick_name, ''),
                                   NULLIF(employee.first_name, ''), employee.login)
           FROM activity activity_row
           JOIN users employee ON lower(employee.login)=lower(activity_row.login)
           WHERE date(activity_row.dtrep)=date(?)
             AND lower(trim(activity_row.club))=lower(trim(?))
             AND activity_row.action LIKE '%Открыть%'
             AND employee.status>=? AND employee.chatid IS NOT NULL''',
        (instance['occurrence_date'], instance['club'], ROLE_EMPLOYEE),
    ).fetchall()
    return [tuple(row) for row in rows]


def _announce_main(bot, conn, instance):
    template = conn.execute(
        'SELECT announce_main FROM broadcasts WHERE id=?',
        (instance['template_id'],),
    ).fetchone()
    if not template or not template[0] or instance['main_announced_at']:
        return
    bot.send_message(
        CHATS['main_group'],
        f'📋 <b>Задача на сегодня · {html.escape(instance["club"])}</b>\n\n'
        f'{html.escape(instance["title"])}\n'
        f'Выполнить до {html.escape(instance["due_at"][11:16])}.',
        parse_mode='HTML',
    )
    conn.execute(
        'UPDATE shift_task_instances SET main_announced_at=? WHERE id=?',
        (_timestamp(), instance['id']),
    )


def _notify_report_overdue(bot, conn, instance):
    if instance['overdue_notified_at']:
        return
    bot.send_message(
        CHATS['reports'],
        f'⚠️ <b>Задача не выполнена в срок</b>\n\n'
        f'📍 {html.escape(instance["club"])}\n'
        f'📋 {html.escape(instance["title"])}\n'
        f'⏰ Срок: {html.escape(instance["due_at"][11:16])}',
        parse_mode='HTML',
    )
    conn.execute(
        'UPDATE shift_task_instances SET overdue_notified_at=? WHERE id=?',
        (_timestamp(), instance['id']),
    )


def process_shift_tasks(bot, now=None, db_path=DB_PATH):
    current = _now(now)
    initialize_shift_tasks_schema(db_path)
    ensure_task_instances(
        current.date(), db_path=db_path, now=current, skip_expired=True,
    )
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    sent = 0
    try:
        instances = conn.execute(
            '''SELECT * FROM shift_task_instances
               WHERE occurrence_date=? AND status IN ('pending', 'in_progress')
               ORDER BY available_at, id''',
            (current.date().isoformat(),),
        ).fetchall()
        with conn:
            for instance in instances:
                available = datetime.strptime(
                    instance['available_at'], '%Y-%m-%d %H:%M:%S',
                ).replace(tzinfo=MOSCOW)
                due = datetime.strptime(
                    instance['due_at'], '%Y-%m-%d %H:%M:%S',
                ).replace(tzinfo=MOSCOW)
                if current < available:
                    for recipient in _opening_recipients(conn, instance):
                        try:
                            sent += int(_send_direct_notification(
                                bot, conn, instance, 'opening', recipient,
                            ))
                        except Exception as error:
                            print(f'Не отправлено утреннее уведомление задачи: {error}')
                    continue

                recipients = _scheduled_recipients(conn, instance)
                activated_now = False
                for recipient in recipients:
                    try:
                        delivered = _send_direct_notification(
                            bot, conn, instance, 'activation', recipient,
                        )
                        sent += int(delivered)
                        activated_now = activated_now or delivered
                    except Exception as error:
                        print(f'Не отправлена задача сотруднику: {error}')
                if activated_now and not instance['activated_at']:
                    activated_at = _timestamp(current)
                    conn.execute(
                        'UPDATE shift_task_instances SET activated_at=? WHERE id=?',
                        (activated_at, instance['id']),
                    )
                try:
                    _announce_main(bot, conn, instance)
                except Exception as error:
                    print(f'Не отправлено объявление задачи в MAIN GROUP: {error}')

                activation_base = instance['activated_at']
                if activation_base:
                    activated = datetime.strptime(
                        activation_base, '%Y-%m-%d %H:%M:%S',
                    ).replace(tzinfo=MOSCOW)
                    if current >= activated + timedelta(hours=3):
                        for recipient in recipients:
                            try:
                                sent += int(_send_direct_notification(
                                    bot, conn, instance, 'reminder_3h', recipient,
                                ))
                            except Exception as error:
                                print(f'Не отправлено напоминание задачи: {error}')
                if current >= due - timedelta(hours=1) and current < due:
                    for recipient in recipients:
                        try:
                            sent += int(_send_direct_notification(
                                bot, conn, instance, 'due_1h', recipient,
                            ))
                        except Exception as error:
                            print(f'Не отправлено напоминание о сроке: {error}')
                if current >= due:
                    for recipient in recipients:
                        try:
                            sent += int(_send_direct_notification(
                                bot, conn, instance, 'overdue', recipient,
                            ))
                        except Exception as error:
                            print(f'Не отправлено уведомление о просрочке: {error}')
                    try:
                        _notify_report_overdue(bot, conn, instance)
                    except Exception as error:
                        print(f'Не отправлена просрочка в REPORT: {error}')
    finally:
        conn.close()
    _retry_task_reports(bot, db_path=db_path)
    _retry_close_warning_notifications(bot, db_path=db_path)
    return sent


def _retry_task_reports(bot, db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        instances = conn.execute(
            '''SELECT * FROM shift_task_instances
               WHERE status IN ('completed', 'skipped')
                 AND report_chatid IS NULL
               ORDER BY COALESCE(completed_at, skipped_at), id LIMIT 20'''
        ).fetchall()
        for instance in instances:
            try:
                if instance['status'] == 'completed':
                    media_rows = conn.execute(
                        '''SELECT * FROM shift_task_media
                           WHERE instance_id=? AND state='submitted'
                           ORDER BY COALESCE(telegram_message_id, 0), id''',
                        (instance['id'],),
                    ).fetchall()
                    if not media_rows:
                        continue
                    actor = {
                        'name': instance['completed_by_name'] or 'Сотрудник',
                        'login': instance['completed_by_login'] or '',
                    }
                    message_ids = _send_task_report(
                        bot, instance, actor, media_rows,
                    )
                else:
                    message = bot.send_message(
                        CHATS['reports'],
                        f'⏭ <b>Задача пропущена</b>\n\n'
                        f'📍 {html.escape(instance["club"])}\n'
                        f'📋 {html.escape(instance["title"])}\n'
                        f'👤 {html.escape(instance["skipped_by_name"] or "Сотрудник")} · '
                        f'{html.escape(instance["skipped_by_login"] or "")}\n'
                        f'💬 <b>Причина:</b> {html.escape(instance["skip_reason"] or "Не указана")}',
                        parse_mode='HTML',
                    )
                    message_ids = [str(getattr(message, 'message_id', ''))]
            except Exception as error:
                print(f'Не отправлен отложенный отчёт задачи: {error}')
                continue
            with conn:
                conn.execute(
                    '''UPDATE shift_task_instances
                       SET report_chatid=?, report_message_ids=? WHERE id=?''',
                    (
                        str(CHATS['reports']), json.dumps(message_ids),
                        instance['id'],
                    ),
                )
    finally:
        conn.close()


def _retry_close_warning_notifications(bot, db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            '''SELECT * FROM shift_task_close_warnings
               WHERE report_sent_at IS NULL ORDER BY id LIMIT 20'''
        ).fetchall()
        for warning in rows:
            try:
                task_ids = json.loads(warning['task_ids_json'] or '[]')
            except (TypeError, json.JSONDecodeError):
                task_ids = []
            placeholders = ','.join('?' for _ in task_ids)
            titles = []
            if placeholders:
                titles = [
                    row[0] for row in conn.execute(
                        f'''SELECT title FROM shift_task_instances
                            WHERE id IN ({placeholders}) ORDER BY due_at, id''',
                        task_ids,
                    ).fetchall()
                ]
            task_lines = '\n'.join(
                f'• {html.escape(title)}' for title in titles
            ) or '• Невыполненные задачи смены'
            try:
                bot.send_message(
                    CHATS['reports'],
                    f'⚠️ <b>Закрытие с невыполненными задачами</b>\n\n'
                    f'📍 {html.escape(warning["club"])}\n'
                    f'👤 {html.escape(warning["actor_name"] or "Сотрудник")} · '
                    f'{html.escape(warning["actor_login"] or "")}\n\n'
                    f'{task_lines}\n\n'
                    'Закрытие не заблокировано, предупреждение записано.',
                    parse_mode='HTML',
                )
            except Exception as error:
                print(f'Не отправлено предупреждение закрытия в REPORT: {error}')
                continue
            with conn:
                conn.execute(
                    '''UPDATE shift_task_close_warnings
                       SET report_sent_at=? WHERE id=?''',
                    (_timestamp(), warning['id']),
                )
    finally:
        conn.close()


def pending_tasks_for_close(club, occurrence_date, db_path=DB_PATH):
    initialize_shift_tasks_schema(db_path)
    ensure_task_instances(occurrence_date, db_path=db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [
            dict(row) for row in conn.execute(
                '''SELECT * FROM shift_task_instances
                   WHERE occurrence_date=? AND lower(trim(club))=lower(trim(?))
                     AND status IN ('pending', 'in_progress')
                   ORDER BY due_at, id''',
                (str(occurrence_date), club),
            ).fetchall()
        ]
    finally:
        conn.close()


def record_close_task_warning(
    bot, run_id, club, occurrence_date, actor, db_path=DB_PATH,
):
    tasks = pending_tasks_for_close(club, occurrence_date, db_path=db_path)
    if not tasks:
        return None
    actor_login = str(actor.get('login') or '')
    actor_name = str(
        actor.get('nick_name') or actor.get('first_name') or actor_login
    )
    actor_chatid = str(actor.get('chatid') or '')
    task_ids = [task['id'] for task in tasks]
    conn = sqlite3.connect(db_path)
    try:
        existing = conn.execute(
            'SELECT report_sent_at FROM shift_task_close_warnings WHERE run_id=?',
            (run_id,),
        ).fetchone()
        with conn:
            conn.execute(
                '''INSERT OR IGNORE INTO shift_task_close_warnings (
                       run_id, occurrence_date, club, actor_login, actor_name,
                       actor_chatid, task_ids_json, warned_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                (
                    run_id, str(occurrence_date), club, actor_login, actor_name,
                    actor_chatid, json.dumps(task_ids), _timestamp(),
                ),
            )
        if not existing or not existing[0]:
            titles = '\n'.join(
                f'• {html.escape(task["title"])}'
                for task in tasks
            )
            try:
                bot.send_message(
                    CHATS['reports'],
                    f'⚠️ <b>Закрытие с невыполненными задачами</b>\n\n'
                    f'📍 {html.escape(club)}\n'
                    f'👤 {html.escape(actor_name)} · {html.escape(actor_login)}\n\n'
                    f'{titles}\n\n'
                    'Закрытие не заблокировано, предупреждение записано.',
                    parse_mode='HTML',
                )
            except Exception as error:
                print(f'Не отправлено предупреждение закрытия в REPORT: {error}')
            else:
                with conn:
                    conn.execute(
                        '''UPDATE shift_task_close_warnings
                           SET report_sent_at=? WHERE run_id=?''',
                        (_timestamp(), run_id),
                    )
    finally:
        conn.close()
    return {
        'count': len(tasks),
        'titles': [task['title'] for task in tasks],
        'message': (
            f'В клубе осталось невыполненных задач: {len(tasks)}. '
            'Закрытие не заблокировано, но предупреждение записано.'
        ),
    }


def _actor_snapshot(update):
    user = get_user(update)
    if not user:
        return None
    return {
        'chatid': str(user['chatid']),
        'login': str(user['login'] or ''),
        'name': str(user['nick_name'] or user['first_name'] or user['login']),
    }


def _app_actor(user):
    user = dict(user or {})
    login = str(user.get('login') or '')
    return {
        'chatid': str(user.get('chatid') or ''),
        'login': login,
        'name': str(
            user.get('nick_name') or user.get('first_name') or login
        ),
        'role': int(user.get('status') or 0),
    }


def _task_accessible_to_actor(conn, instance, actor):
    if not instance or not actor.get('chatid'):
        return False
    if actor['role'] >= ROLE_MANAGER:
        return True
    notified = conn.execute(
        '''SELECT 1 FROM shift_task_notifications
           WHERE instance_id=? AND recipient_chatid=? LIMIT 1''',
        (instance['id'], actor['chatid']),
    ).fetchone()
    if notified:
        return True
    recipients = _scheduled_recipients(conn, instance)
    return actor['chatid'] in {str(row[0]) for row in recipients}


def _app_task_payload(instance, current):
    row = dict(instance)
    status = row['status']
    due_at = datetime.strptime(
        row['due_at'], '%Y-%m-%d %H:%M:%S',
    ).replace(tzinfo=MOSCOW)
    overdue = status in ('pending', 'in_progress') and current >= due_at
    requirements = _instance_requirements(row)
    return {
        'id': row['id'],
        'template_id': row['template_id'],
        'date': row['occurrence_date'],
        'club': row['club'],
        'title': row['title'],
        'instructions': row['instructions'] or '',
        'requirements': requirements,
        'required_attachments': max(1, len(requirements)),
        'max_attachments': MAX_ATTACHMENTS,
        'available_at': row['available_at'],
        'due_at': row['due_at'],
        'status': status,
        'overdue': overdue,
        'started_at': row['started_at'],
        'started_by_login': row['started_by_login'],
        'completed_at': row['completed_at'],
        'completed_by_name': row['completed_by_name'],
        'completed_by_login': row['completed_by_login'],
        'skipped_at': row['skipped_at'],
        'skipped_by_name': row['skipped_by_name'],
        'skipped_by_login': row['skipped_by_login'],
        'skip_reason': row['skip_reason'],
        'can_execute': status in ('pending', 'in_progress'),
    }


def app_task_list(user, scope='active', db_path=DB_PATH, now=None):
    current = _now(now)
    initialize_shift_tasks_schema(db_path)
    ensure_task_instances(current.date(), db_path=db_path, now=current)
    actor = _app_actor(user)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if scope == 'history':
            first_day = (current.date() - timedelta(days=30)).isoformat()
            rows = conn.execute(
                '''SELECT * FROM shift_task_instances
                   WHERE occurrence_date BETWEEN ? AND ?
                   ORDER BY occurrence_date DESC, due_at DESC, id DESC''',
                (first_day, current.date().isoformat()),
            ).fetchall()
        else:
            rows = conn.execute(
                '''SELECT * FROM shift_task_instances
                   WHERE occurrence_date=?
                   ORDER BY due_at, id''',
                (current.date().isoformat(),),
            ).fetchall()
        return [
            _app_task_payload(row, current)
            for row in rows
            if _task_accessible_to_actor(conn, row, actor)
        ]
    finally:
        conn.close()


def start_app_task(user, instance_id, db_path=DB_PATH):
    actor = _app_actor(user)
    initialize_shift_tasks_schema(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        instance = conn.execute(
            'SELECT * FROM shift_task_instances WHERE id=?',
            (instance_id,),
        ).fetchone()
        if not _task_accessible_to_actor(conn, instance, actor):
            raise ValueError('Эта задача вам недоступна')
        if instance['status'] not in ('pending', 'in_progress'):
            raise ValueError('Задача уже закрыта')
        with conn:
            conn.execute(
                '''UPDATE shift_task_instances
                   SET status='in_progress', started_at=COALESCE(started_at, ?),
                       started_by_login=COALESCE(started_by_login, ?)
                   WHERE id=? AND status IN ('pending', 'in_progress')''',
                (_timestamp(), actor['login'], instance_id),
            )
        return _app_task_payload(
            conn.execute(
                'SELECT * FROM shift_task_instances WHERE id=?',
                (instance_id,),
            ).fetchone(),
            _now(),
        )
    finally:
        conn.close()


def _send_app_task_report(bot, instance, actor, uploads):
    requirements = _instance_requirements(instance)
    requirement_text = ''
    if requirements:
        requirement_text = '\n\n' + '\n'.join(
            f'{index}. {html.escape(label[:60])}'
            for index, label in enumerate(requirements, 1)
        )
    caption = (
        '✅ <b>Задача выполнена</b>\n\n'
        f'📍 {html.escape(str(instance["club"])[:60])}\n'
        f'📋 {html.escape(str(instance["title"])[:80])}\n'
        f'👤 {html.escape(str(actor["name"])[:80])} · '
        f'{html.escape(str(actor["login"])[:64])}\n'
        f'📎 Вложений: {len(uploads)}{requirement_text}'
    )
    files = []
    if len(uploads) == 1:
        upload = uploads[0]
        media_file = io.BytesIO(upload['content'])
        media_file.name = upload['filename']
        method = bot.send_video if upload['media_type'] == 'video' else bot.send_photo
        messages = [method(
            CHATS['reports'], media_file, caption=caption, parse_mode='HTML',
        )]
    else:
        media = []
        for index, upload in enumerate(uploads):
            media_file = io.BytesIO(upload['content'])
            media_file.name = upload['filename']
            files.append(media_file)
            media_class = (
                types.InputMediaVideo
                if upload['media_type'] == 'video'
                else types.InputMediaPhoto
            )
            media.append(media_class(
                media_file,
                caption=caption if index == 0 else None,
                parse_mode='HTML' if index == 0 else None,
            ))
        messages = list(bot.send_media_group(CHATS['reports'], media=media))
    stored = []
    for upload, message in zip(uploads, messages):
        if upload['media_type'] == 'video':
            telegram_media = message.video
        else:
            telegram_media = message.photo[-1]
        stored.append({
            **upload,
            'telegram_file_id': telegram_media.file_id,
            'telegram_file_unique_id': telegram_media.file_unique_id,
            'telegram_message_id': getattr(message, 'message_id', None),
            'media_group_id': getattr(message, 'media_group_id', None),
        })
    return stored, [
        str(getattr(message, 'message_id', '')) for message in messages
    ]


def complete_app_task(user, instance_id, uploads, bot, db_path=DB_PATH):
    actor = _app_actor(user)
    if not bot or not CHATS.get('reports'):
        raise RuntimeError('Чат отчётов временно недоступен')
    with _task_app_completion_lock:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            instance = conn.execute(
                'SELECT * FROM shift_task_instances WHERE id=?',
                (instance_id,),
            ).fetchone()
            if not _task_accessible_to_actor(conn, instance, actor):
                raise ValueError('Эта задача вам недоступна')
            if instance['status'] not in ('pending', 'in_progress'):
                raise ValueError('Задачу уже закрыл коллега')
            required = max(1, len(_instance_requirements(instance)))
            if not required <= len(uploads) <= MAX_ATTACHMENTS:
                raise ValueError(
                    f'Нужно добавить вложений: {required}. Сейчас: {len(uploads)}'
                )
            stored, message_ids = _send_app_task_report(
                bot, instance, actor, uploads,
            )
            completed_at = _timestamp()
            with conn:
                updated = conn.execute(
                    '''UPDATE shift_task_instances
                       SET status='completed', completed_at=?,
                           completed_by_login=?, completed_by_name=?,
                           completed_by_chatid=?, report_chatid=?,
                           report_message_ids=?
                       WHERE id=? AND status IN ('pending', 'in_progress')''',
                    (
                        completed_at, actor['login'], actor['name'],
                        actor['chatid'], str(CHATS['reports']),
                        json.dumps(message_ids), instance_id,
                    ),
                ).rowcount
                if not updated:
                    raise ValueError('Задачу уже закрыл коллега')
                conn.execute(
                    '''DELETE FROM shift_task_media
                       WHERE instance_id=? AND state='draft' ''',
                    (instance_id,),
                )
                for media in stored:
                    conn.execute(
                        '''INSERT OR IGNORE INTO shift_task_media (
                               instance_id, telegram_file_id,
                               telegram_file_unique_id, telegram_message_id,
                               media_group_id, media_type, file_size,
                               submitted_by_chatid, submitted_by_login,
                               created_at, state
                           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'submitted')''',
                        (
                            instance_id, media['telegram_file_id'],
                            media['telegram_file_unique_id'],
                            media['telegram_message_id'], media['media_group_id'],
                            media['media_type'], media['file_size'],
                            actor['chatid'], actor['login'], _timestamp(),
                        ),
                    )
                conn.execute(
                    'DELETE FROM shift_task_drafts WHERE instance_id=?',
                    (instance_id,),
                )
            _update_task_cards(bot, conn, instance_id, 'completed')
            return {'id': instance_id, 'status': 'completed'}
        finally:
            conn.close()


def skip_app_task(user, instance_id, reason, bot, db_path=DB_PATH):
    actor = _app_actor(user)
    reason = str(reason or '').strip()
    if not 3 <= len(reason) <= 500:
        raise ValueError('Укажите причину пропуска — от 3 до 500 символов')
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        instance = conn.execute(
            'SELECT * FROM shift_task_instances WHERE id=?',
            (instance_id,),
        ).fetchone()
        if not _task_accessible_to_actor(conn, instance, actor):
            raise ValueError('Эта задача вам недоступна')
        skipped_at = _timestamp()
        with conn:
            updated = conn.execute(
                '''UPDATE shift_task_instances
                   SET status='skipped', skipped_at=?, skipped_by_login=?,
                       skipped_by_name=?, skipped_by_chatid=?, skip_reason=?
                   WHERE id=? AND status IN ('pending', 'in_progress')''',
                (
                    skipped_at, actor['login'], actor['name'], actor['chatid'],
                    reason, instance_id,
                ),
            ).rowcount
            if not updated:
                raise ValueError('Задачу уже закрыл коллега')
            conn.execute(
                '''DELETE FROM shift_task_media
                   WHERE instance_id=? AND state='draft' ''',
                (instance_id,),
            )
            conn.execute(
                'DELETE FROM shift_task_drafts WHERE instance_id=?',
                (instance_id,),
            )
        if bot and CHATS.get('reports'):
            try:
                message = bot.send_message(
                    CHATS['reports'],
                    '⏭ <b>Задача пропущена</b>\n\n'
                    f'📍 {html.escape(instance["club"])}\n'
                    f'📋 {html.escape(instance["title"])}\n'
                    f'👤 {html.escape(actor["name"])} · '
                    f'{html.escape(actor["login"])}\n'
                    f'💬 <b>Причина:</b> {html.escape(reason)}',
                    parse_mode='HTML',
                )
            except Exception as error:
                print(f'Не отправлен пропуск задачи из приложения: {error}')
            else:
                with conn:
                    conn.execute(
                        '''UPDATE shift_task_instances
                           SET report_chatid=?, report_message_ids=? WHERE id=?''',
                        (
                            str(CHATS['reports']),
                            json.dumps([str(getattr(message, 'message_id', ''))]),
                            instance_id,
                        ),
                    )
            _update_task_cards(bot, conn, instance_id, 'skipped')
        return {'id': instance_id, 'status': 'skipped'}
    finally:
        conn.close()


def _send_task_report(bot, instance, actor, media_rows):
    requirements = _instance_requirements(instance)
    requirement_text = ''
    if requirements:
        requirement_text = '\n\n' + '\n'.join(
            f'{index}. {html.escape(label[:60])}'
            for index, label in enumerate(requirements, 1)
        )
    caption = (
        f'✅ <b>Задача выполнена</b>\n\n'
        f'📍 {html.escape(str(instance["club"])[:60])}\n'
        f'📋 {html.escape(str(instance["title"])[:80])}\n'
        f'👤 {html.escape(str(actor["name"])[:80])} · '
        f'{html.escape(str(actor["login"])[:64])}\n'
        f'📎 Вложений: {len(media_rows)}'
        f'{requirement_text}'
    )
    sent = []
    if len(media_rows) == 1:
        row = media_rows[0]
        method = bot.send_video if row['media_type'] == 'video' else bot.send_photo
        sent.append(method(
            CHATS['reports'], row['telegram_file_id'], caption=caption,
            parse_mode='HTML',
        ))
    else:
        media = []
        for index, row in enumerate(media_rows):
            cls = types.InputMediaVideo if row['media_type'] == 'video' else types.InputMediaPhoto
            media.append(cls(
                row['telegram_file_id'],
                caption=caption if index == 0 else None,
                parse_mode='HTML' if index == 0 else None,
            ))
        sent.extend(bot.send_media_group(CHATS['reports'], media=media))
    return [str(getattr(message, 'message_id', '')) for message in sent]


def _update_task_cards(bot, conn, instance_id, final_text):
    rows = conn.execute(
        '''SELECT recipient_chatid, telegram_message_id
           FROM shift_task_notifications
           WHERE instance_id=? AND telegram_message_id IS NOT NULL
             AND trim(telegram_message_id) <> '' ''',
        (instance_id,),
    ).fetchall()
    instance = conn.execute(
        'SELECT * FROM shift_task_instances WHERE id=?',
        (instance_id,),
    ).fetchone()
    icon = '✅' if final_text == 'completed' else '⏭'
    status = 'выполнена' if final_text == 'completed' else 'пропущена'
    for chatid, message_id in rows:
        try:
            bot.edit_message_text(
                f'{icon} <b>{html.escape(instance["title"])}</b>\n\n'
                f'📍 {html.escape(instance["club"])}\n'
                f'Задача {status}.',
                chat_id=chatid,
                message_id=int(message_id),
                parse_mode='HTML',
                reply_markup=None,
            )
        except Exception:
            try:
                bot.edit_message_reply_markup(
                    chat_id=chatid,
                    message_id=int(message_id),
                    reply_markup=None,
                )
            except Exception:
                pass
    return final_text


def _admin_task_days_keyboard(selected=''):
    markup = types.InlineKeyboardMarkup()
    names = {'0': 'Пн', '1': 'Вт', '2': 'Ср', '3': 'Чт', '4': 'Пт', '5': 'Сб', '6': 'Вс'}
    row = []
    for number, name in names.items():
        value = selected.replace(number, '') if number in selected else selected + number
        row.append(types.InlineKeyboardButton(
            f'✅ {name}' if number in selected else name,
            callback_data=f'stadmin_daytoggle_{value}',
        ))
        if len(row) == 4:
            markup.row(*row)
            row = []
    if row:
        markup.row(*row)
    markup.row(
        types.InlineKeyboardButton('Каждый день', callback_data='stadmin_daydaily'),
        types.InlineKeyboardButton('Однократно', callback_data='stadmin_dayonce'),
    )
    if selected:
        markup.add(types.InlineKeyboardButton(
            'Сохранить дни', callback_data=f'stadmin_daysave_{selected}',
        ))
    markup.add(types.InlineKeyboardButton('Отмена', callback_data='stadmin_abort'))
    return markup


def _admin_clubs_keyboard(selected=None):
    selected = set(selected or [])
    clubs = [
        name for name, settings in get_clubs().items()
        if settings.get('is_physical')
    ]
    markup = types.InlineKeyboardMarkup(row_width=2)
    for index, club in enumerate(clubs):
        markup.add(types.InlineKeyboardButton(
            f'✅ {club}' if club in selected else club,
            callback_data=f'stadmin_club_{index}',
        ))
    if selected:
        markup.add(types.InlineKeyboardButton(
            f'Продолжить · {len(selected)}', callback_data='stadmin_clubsave',
        ))
    markup.add(types.InlineKeyboardButton('Отмена', callback_data='stadmin_abort'))
    return markup


def start_task_creation(message, bot):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    _task_admin_drafts[message.chat.id] = {'clubs': []}
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add('Вернуться')
    sent = bot.send_message(
        message.chat.id,
        '<b>Новая задача смены</b>\n\nВведите короткое название — его увидят сотрудники в карточке.',
        parse_mode='HTML', reply_markup=markup,
    )
    bot.register_next_step_handler(sent, _admin_task_title, bot)


def start_task_conversion(message, bot, broadcast_id):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    initialize_shift_tasks_schema()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            '''SELECT * FROM broadcasts WHERE id=?
               AND COALESCE(kind, 'information')=?''',
            (broadcast_id, KIND_INFORMATION),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        bot.send_message(message.chat.id, 'Инфо-рассылка не найдена или уже преобразована.')
        return
    actor = _actor_snapshot(message)
    _task_admin_drafts[message.chat.id] = {
        'conversion_broadcast_id': int(broadcast_id),
        'clubs': [],
        'instructions': row['text'] or '',
        'time': row['time'],
        'freq_type': row['freq_type'],
        'freq_days': row['freq_days'] or '',
        'status': int(row['status'] or 0),
        'created_by': actor['login'] if actor else None,
    }
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add('Вернуться')
    sent = bot.send_message(
        message.chat.id,
        f'<b>Рассылка #{broadcast_id} → задача</b>\n\n'
        'Время, дни и текст сохранятся. Введите короткое название задачи:',
        parse_mode='HTML', reply_markup=markup,
    )
    bot.register_next_step_handler(sent, _admin_task_title, bot)


def _admin_abort(message, bot):
    _task_admin_drafts.pop(message.chat.id, None)
    from admin_panel import broadcast_menu
    broadcast_menu(message, bot)


def _admin_task_title(message, bot):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    if message.text == 'Вернуться':
        _admin_abort(message, bot)
        return
    title = str(message.text or '').strip()
    if not 3 <= len(title) <= 80:
        sent = bot.send_message(message.chat.id, 'Название должно содержать от 3 до 80 символов.')
        bot.register_next_step_handler(sent, _admin_task_title, bot)
        return
    draft = _task_admin_drafts[message.chat.id]
    draft['title'] = title
    if draft.get('conversion_broadcast_id'):
        bot.send_message(
            message.chat.id,
            'Выберите клубы, в которых появится задача:',
            reply_markup=_admin_clubs_keyboard(),
        )
        return
    sent = bot.send_message(
        message.chat.id,
        'Опишите, что нужно сделать. Упоминания клубов, срок и просьбу прислать отчёт писать не нужно — бот добавит это сам.',
    )
    bot.register_next_step_handler(sent, _admin_task_instructions, bot)


def _admin_task_instructions(message, bot):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    if message.text == 'Вернуться':
        _admin_abort(message, bot)
        return
    instructions = str(message.text or '').strip()
    if not 3 <= len(instructions) <= 1200:
        sent = bot.send_message(message.chat.id, 'Описание должно содержать от 3 до 1200 символов.')
        bot.register_next_step_handler(sent, _admin_task_instructions, bot)
        return
    _task_admin_drafts[message.chat.id]['instructions'] = instructions
    bot.send_message(
        message.chat.id,
        'Выберите клубы, в которых появится задача:',
        reply_markup=_admin_clubs_keyboard(),
    )


def _valid_time(value):
    try:
        datetime.strptime(str(value), '%H:%M')
        return True
    except ValueError:
        return False


def _admin_task_start_time(message, bot):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    if message.text == 'Вернуться':
        _admin_abort(message, bot)
        return
    value = str(message.text or '').strip()
    if not _valid_time(value):
        sent = bot.send_message(message.chat.id, 'Введите время в формате ЧЧ:ММ, например 12:00.')
        bot.register_next_step_handler(sent, _admin_task_start_time, bot)
        return
    _task_admin_drafts[message.chat.id]['time'] = value
    sent = bot.send_message(
        message.chat.id,
        'До какого времени задача должна быть выполнена? Например: 20:00',
    )
    bot.register_next_step_handler(sent, _admin_task_due_time, bot)


def _admin_task_due_time(message, bot):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    if message.text == 'Вернуться':
        _admin_abort(message, bot)
        return
    due = str(message.text or '').strip()
    draft = _task_admin_drafts.get(message.chat.id)
    if not _valid_time(due) or due <= draft['time']:
        sent = bot.send_message(
            message.chat.id,
            'Срок должен быть позже времени появления и иметь формат ЧЧ:ММ.',
        )
        bot.register_next_step_handler(sent, _admin_task_due_time, bot)
        return
    draft['due_time'] = due
    if draft.get('conversion_broadcast_id'):
        _prompt_task_announcement(message.chat.id, bot)
        return
    bot.send_message(
        message.chat.id,
        'Выберите дни выполнения:',
        reply_markup=_admin_task_days_keyboard(),
    )


def _prompt_task_announcement(chat_id, bot):
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton('Да', callback_data='stadmin_announce_yes'),
        types.InlineKeyboardButton('Нет', callback_data='stadmin_announce_no'),
    )
    bot.send_message(
        chat_id,
        'Дублировать короткое объявление о задаче в рабочую группу?',
        reply_markup=markup,
    )


def _save_task_template(chat_id, bot, announce_main):
    draft = _task_admin_drafts.pop(chat_id, None)
    if not draft:
        bot.send_message(chat_id, 'Черновик устарел. Начните создание заново.')
        return
    conn = sqlite3.connect(DB_PATH)
    try:
        with conn:
            conversion_id = draft.get('conversion_broadcast_id')
            if conversion_id:
                updated = conn.execute(
                    '''UPDATE broadcasts
                       SET kind=?, title=?, clubs_json=?, due_time=?,
                           announce_main=?, task_category=?, created_by=?,
                           updated_at=?
                       WHERE id=? AND COALESCE(kind, 'information')=?''',
                    (
                        KIND_TASK, draft['title'],
                        json.dumps(draft['clubs'], ensure_ascii=False),
                        draft['due_time'], int(announce_main), CATEGORY_GENERAL,
                        draft.get('created_by'), _timestamp(), conversion_id,
                        KIND_INFORMATION,
                    ),
                ).rowcount
                if not updated:
                    bot.send_message(chat_id, 'Рассылка уже изменена. Обновите список.')
                    return
                template_id = int(conversion_id)
            else:
                cursor = conn.execute(
                    '''INSERT INTO broadcasts (
                           text, photo, time, freq_type, freq_days, status, kind,
                           title, clubs_json, due_time, announce_main,
                           task_category, created_by, created_at, updated_at
                       ) VALUES (?, 'None', ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                    (
                        draft['instructions'], draft['time'], draft['freq_type'],
                        draft.get('freq_days', ''), KIND_TASK, draft['title'],
                        json.dumps(draft['clubs'], ensure_ascii=False),
                        draft['due_time'], int(announce_main),
                        CATEGORY_GENERAL, draft.get('created_by'),
                        _timestamp(), _timestamp(),
                    ),
                )
                template_id = cursor.lastrowid
    finally:
        conn.close()
    active_note = (
        'Сотрудники получат её по расписанию.'
        if not draft.get('conversion_broadcast_id') or draft.get('status', 1)
        else 'Рассылка была на паузе, поэтому задача тоже сохранена на паузе.'
    )
    bot.send_message(
        chat_id,
        f'✅ Задача #{template_id} '
        f'{"создана из рассылки" if draft.get("conversion_broadcast_id") else "создана"}. '
        f'{active_note}',
        reply_markup=types.ReplyKeyboardRemove(),
    )


def show_task_templates(message, bot):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    initialize_shift_tasks_schema()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            '''SELECT * FROM broadcasts WHERE kind=? ORDER BY id''',
            (KIND_TASK,),
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        bot.send_message(message.chat.id, 'Задач пока нет.')
        return
    lines = ['📋 <b>Задачи смены</b>', '']
    markup = types.InlineKeyboardMarkup()
    for row in rows:
        status = '🟢' if row['status'] else '⏸'
        lines.append(
            f'{status} <b>#{row["id"]} · {html.escape(row["title"] or "Без названия")}</b>\n'
            f'{row["time"]}–{row["due_time"]} · {html.escape(_frequency_label(row))}'
        )
        markup.add(types.InlineKeyboardButton(
            f'Настроить #{row["id"]}', callback_data=f'stadmin_manage_{row["id"]}',
        ))
    bot.send_message(
        message.chat.id, '\n\n'.join(lines), parse_mode='HTML', reply_markup=markup,
    )


def _frequency_label(row):
    if row['freq_type'] == 'daily':
        return 'ежедневно'
    if row['freq_type'] == 'once':
        return 'однократно'
    names = {'0': 'Пн', '1': 'Вт', '2': 'Ср', '3': 'Чт', '4': 'Пт', '5': 'Сб', '6': 'Вс'}
    return ', '.join(names[value] for value in str(row['freq_days'] or '') if value in names)


def _task_template_card(message, template_id, bot):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute('SELECT * FROM broadcasts WHERE id=? AND kind=?', (template_id, KIND_TASK)).fetchone()
    finally:
        conn.close()
    if not row:
        bot.send_message(message.chat.id, 'Задача не найдена.')
        return
    clubs = ', '.join(_template_clubs(row)) or 'не выбраны'
    status = 'Активна' if row['status'] else 'На паузе'
    text = (
        f'📋 <b>Задача #{row["id"]}</b>\n\n'
        f'<b>{html.escape(row["title"] or "Без названия")}</b>\n'
        f'📍 {html.escape(clubs)}\n'
        f'⏰ {row["time"]}–{row["due_time"]}\n'
        f'📅 {html.escape(_frequency_label(row))}\n'
        f'⚙️ {status}\n\n'
        f'{html.escape(row["text"] or "")}'
    )
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(
        '⏸ Пауза' if row['status'] else '▶️ Активировать',
        callback_data=f'stadmin_toggle_{template_id}',
    ))
    markup.row(
        types.InlineKeyboardButton('🕒 Время', callback_data=f'stadmin_time_{template_id}'),
        types.InlineKeyboardButton('⏰ Срок', callback_data=f'stadmin_due_{template_id}'),
    )
    markup.row(
        types.InlineKeyboardButton('✏️ Название', callback_data=f'stadmin_title_{template_id}'),
        types.InlineKeyboardButton('📝 Описание', callback_data=f'stadmin_text_{template_id}'),
    )
    markup.row(
        types.InlineKeyboardButton('📍 Клубы', callback_data=f'stadmin_editclubs_{template_id}'),
        types.InlineKeyboardButton('📊 Выполнение', callback_data=f'stadmin_history_{template_id}'),
    )
    markup.add(types.InlineKeyboardButton(
        '📅 Дни выполнения', callback_data=f'stadmin_frequency_{template_id}',
    ))
    if row['task_category'] == CATEGORY_CLEANLINESS:
        markup.add(types.InlineKeyboardButton(
            '🧹 Пункты чистоты', callback_data=f'stadmin_requirements_{template_id}',
        ))
    markup.add(types.InlineKeyboardButton('⬅️ К списку', callback_data='stadmin_list'))
    bot.send_message(message.chat.id, text, parse_mode='HTML', reply_markup=markup)


def _admin_edit_value_prompt(call, bot, field, prompt):
    bot.answer_callback_query(call.id)
    sent = bot.send_message(call.message.chat.id, prompt)
    bot.register_next_step_handler(sent, _admin_save_template_value, int(call.data.rsplit('_', 1)[1]), field, bot)


def _admin_save_template_value(message, template_id, field, bot):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    value = str(message.text or '').strip()
    if field in {'time', 'due_time'} and not _valid_time(value):
        sent = bot.send_message(message.chat.id, 'Введите время строго в формате ЧЧ:ММ.')
        bot.register_next_step_handler(sent, _admin_save_template_value, template_id, field, bot)
        return
    if field == 'text' and not 3 <= len(value) <= 1200:
        sent = bot.send_message(message.chat.id, 'Описание должно содержать от 3 до 1200 символов.')
        bot.register_next_step_handler(sent, _admin_save_template_value, template_id, field, bot)
        return
    if field == 'title' and not 3 <= len(value) <= 80:
        sent = bot.send_message(message.chat.id, 'Название должно содержать от 3 до 80 символов.')
        bot.register_next_step_handler(sent, _admin_save_template_value, template_id, field, bot)
        return
    conn = sqlite3.connect(DB_PATH)
    try:
        current = conn.execute('SELECT time, due_time FROM broadcasts WHERE id=?', (template_id,)).fetchone()
        start = value if field == 'time' else current[0]
        due = value if field == 'due_time' else current[1]
        if due <= start:
            sent = bot.send_message(message.chat.id, 'Срок должен быть позже времени появления.')
            bot.register_next_step_handler(sent, _admin_save_template_value, template_id, field, bot)
            return
        with conn:
            conn.execute(
                f'UPDATE broadcasts SET {field}=?, updated_at=? WHERE id=?',
                (value, _timestamp(), template_id),
            )
            if field == 'text':
                conn.execute(
                    '''UPDATE shift_task_instances SET instructions=?
                       WHERE template_id=?
                         AND status IN ('pending', 'in_progress')''',
                    (value, template_id),
                )
            elif field == 'title':
                conn.execute(
                    '''UPDATE shift_task_instances SET title=?
                       WHERE template_id=?
                         AND status IN ('pending', 'in_progress')''',
                    (value, template_id),
                )
            elif field in {'time', 'due_time'}:
                rows = conn.execute(
                    '''SELECT id, occurrence_date, activated_at
                       FROM shift_task_instances
                       WHERE template_id=?
                         AND status IN ('pending', 'in_progress')''',
                    (template_id,),
                ).fetchall()
                for instance_id, occurrence_date, activated_at in rows:
                    if field == 'time' and activated_at:
                        continue
                    column = 'available_at' if field == 'time' else 'due_at'
                    conn.execute(
                        f'UPDATE shift_task_instances SET {column}=? WHERE id=?',
                        (f'{occurrence_date} {value}:00', instance_id),
                    )
    finally:
        conn.close()
    bot.send_message(message.chat.id, '✅ Настройка сохранена.')
    _task_template_card(message, template_id, bot)


def _admin_requirement_clubs(call, bot, template_id):
    conn = sqlite3.connect(DB_PATH)
    try:
        clubs = [row[0] for row in conn.execute(
            '''SELECT DISTINCT club FROM shift_task_requirements
               WHERE template_id=? ORDER BY club''', (template_id,),
        )]
    finally:
        conn.close()
    markup = types.InlineKeyboardMarkup()
    for index, club in enumerate(clubs):
        markup.add(types.InlineKeyboardButton(
            club, callback_data=f'stadmin_reqclub_{template_id}_{index}',
        ))
    markup.add(types.InlineKeyboardButton('⬅️ Назад', callback_data=f'stadmin_manage_{template_id}'))
    _task_admin_drafts[call.message.chat.id] = {'requirement_clubs': clubs}
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, 'Выберите клуб для редактирования пунктов:', reply_markup=markup)


def _task_template_history(message, template_id, bot):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            '''SELECT * FROM shift_task_instances
               WHERE template_id=? ORDER BY occurrence_date DESC, id DESC''',
            (template_id,),
        ).fetchall()
    finally:
        conn.close()
    now_label = _timestamp()
    completed = sum(row['status'] == 'completed' for row in rows)
    late = sum(
        row['status'] == 'completed'
        and row['completed_at']
        and row['completed_at'] > row['due_at']
        for row in rows
    )
    skipped = sum(row['status'] == 'skipped' for row in rows)
    missed = sum(
        row['status'] in ('pending', 'in_progress')
        and row['due_at'] < now_label
        for row in rows
    )
    total_closed = completed + skipped + missed
    completion_rate = round(completed * 100 / total_closed) if total_closed else 0
    lines = [
        '📊 <b>Выполнение задачи</b>',
        '',
        f'✅ Выполнено: <b>{completed}</b>',
        f'🟡 Из них с опозданием: <b>{late}</b>',
        f'⏭ Пропущено: <b>{skipped}</b>',
        f'⚠️ Не выполнено в срок: <b>{missed}</b>',
        f'📈 Выполнение: <b>{completion_rate}%</b>',
    ]
    if rows:
        lines.extend(['', '<b>Последние запуски:</b>'])
    for row in rows[:12]:
        if row['status'] == 'completed':
            icon = '🟡' if row['completed_at'] > row['due_at'] else '✅'
            actor = row['completed_by_name'] or row['completed_by_login'] or 'Сотрудник'
        elif row['status'] == 'skipped':
            icon = '⏭'
            actor = row['skipped_by_name'] or row['skipped_by_login'] or 'Сотрудник'
        elif row['due_at'] < now_label:
            icon = '⚠️'
            actor = 'не выполнено'
        else:
            icon = '🕒'
            actor = 'ожидает выполнения'
        lines.append(
            f'{icon} {html.escape(row["occurrence_date"])} · '
            f'{html.escape(row["club"])} · {html.escape(actor)}'
        )
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(
        '⬅️ К задаче', callback_data=f'stadmin_manage_{template_id}',
    ))
    bot.send_message(
        message.chat.id, '\n'.join(lines), parse_mode='HTML', reply_markup=markup,
    )


def _admin_requirements_prompt(call, bot, template_id, club):
    conn = sqlite3.connect(DB_PATH)
    try:
        points = _requirements_for(conn, template_id, club)
    finally:
        conn.close()
    sent = bot.send_message(
        call.message.chat.id,
        f'<b>{html.escape(club)}</b>\n\nОтправьте новый список: один фотопункт на строку. Допустимо от 1 до 10 пунктов.\n\n'
        + '\n'.join(f'{index}. {html.escape(point)}' for index, point in enumerate(points, 1)),
        parse_mode='HTML',
    )
    bot.register_next_step_handler(sent, _admin_save_requirements, template_id, club, bot)


def _admin_save_requirements(message, template_id, club, bot):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    points = [
        re.sub(r'^\s*\d+[.)]\s*', '', line).strip()
        for line in str(message.text or '').splitlines()
        if line.strip()
    ]
    if not 1 <= len(points) <= MAX_ATTACHMENTS or any(len(point) > 120 for point in points):
        sent = bot.send_message(message.chat.id, 'Нужно от 1 до 10 строк, каждая не длиннее 120 символов.')
        bot.register_next_step_handler(sent, _admin_save_requirements, template_id, club, bot)
        return
    conn = sqlite3.connect(DB_PATH)
    try:
        with conn:
            conn.execute(
                'DELETE FROM shift_task_requirements WHERE template_id=? AND club=?',
                (template_id, club),
            )
            conn.executemany(
                '''INSERT INTO shift_task_requirements
                   (template_id, club, position, label) VALUES (?, ?, ?, ?)''',
                [
                    (template_id, club, index, point)
                    for index, point in enumerate(points, 1)
                ],
            )
            conn.execute(
                '''UPDATE shift_task_instances SET requirements_json=?
                   WHERE template_id=? AND club=?
                     AND status IN ('pending', 'in_progress')''',
                (json.dumps(points, ensure_ascii=False), template_id, club),
            )
    finally:
        conn.close()
    bot.send_message(message.chat.id, f'✅ Пункты для клуба «{club}» обновлены.')
    _task_template_card(message, template_id, bot)


def register_shift_task_admin_handlers(bot):
    @bot.callback_query_handler(func=lambda call: str(call.data or '').startswith('stadmin_'))
    def admin_task_callback(call):
        if not require_role(call, bot, ROLE_MANAGER):
            return
        data = str(call.data or '')
        chat_id = call.message.chat.id
        if data == 'stadmin_abort':
            bot.answer_callback_query(call.id)
            _admin_abort(call.message, bot)
            return
        if data.startswith('stadmin_club_'):
            draft = _task_admin_drafts.get(chat_id)
            if not draft:
                bot.answer_callback_query(call.id, 'Черновик устарел.', show_alert=True)
                return
            clubs = [name for name, settings in get_clubs().items() if settings.get('is_physical')]
            index = int(data.rsplit('_', 1)[1])
            club = clubs[index]
            selected = set(draft.get('clubs', []))
            selected.symmetric_difference_update({club})
            draft['clubs'] = sorted(selected, key=clubs.index)
            bot.answer_callback_query(call.id)
            bot.edit_message_reply_markup(
                chat_id, call.message.message_id,
                reply_markup=_admin_clubs_keyboard(draft['clubs']),
            )
            return
        if data == 'stadmin_clubsave':
            draft = _task_admin_drafts.get(chat_id)
            if not draft or not draft.get('clubs'):
                bot.answer_callback_query(call.id, 'Выберите хотя бы один клуб.', show_alert=True)
                return
            if draft.get('editing_template_id'):
                template_id = int(draft['editing_template_id'])
                conn = sqlite3.connect(DB_PATH)
                try:
                    with conn:
                        conn.execute(
                            '''UPDATE broadcasts SET clubs_json=?, updated_at=?
                               WHERE id=? AND kind=?''',
                            (
                                json.dumps(draft['clubs'], ensure_ascii=False),
                                _timestamp(), template_id, KIND_TASK,
                            ),
                        )
                finally:
                    conn.close()
                _task_admin_drafts.pop(chat_id, None)
                bot.answer_callback_query(call.id, 'Клубы обновлены.')
                _task_template_card(call.message, template_id, bot)
                return
            if draft.get('conversion_broadcast_id'):
                bot.answer_callback_query(call.id)
                sent = bot.send_message(
                    chat_id,
                    f'Рассылка появляется в {draft["time"]}. До какого времени задача должна быть выполнена?',
                )
                bot.register_next_step_handler(sent, _admin_task_due_time, bot)
                return
            bot.answer_callback_query(call.id)
            sent = bot.send_message(chat_id, 'Во сколько показать задачу сотрудникам? Например: 12:00')
            bot.register_next_step_handler(sent, _admin_task_start_time, bot)
            return
        if data.startswith('stadmin_daytoggle_'):
            selected = data.rsplit('_', 1)[1]
            bot.answer_callback_query(call.id)
            bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=_admin_task_days_keyboard(selected))
            return
        if data.startswith('stadmin_day'):
            draft = _task_admin_drafts.get(chat_id)
            if not draft:
                bot.answer_callback_query(call.id, 'Черновик устарел.', show_alert=True)
                return
            if data == 'stadmin_daydaily':
                draft.update(freq_type='daily', freq_days='')
            elif data == 'stadmin_dayonce':
                draft.update(freq_type='once', freq_days='')
            elif data.startswith('stadmin_daysave_'):
                draft.update(freq_type='custom', freq_days=data.rsplit('_', 1)[1])
            else:
                return
            if draft.get('editing_frequency_id'):
                template_id = int(draft['editing_frequency_id'])
                conn = sqlite3.connect(DB_PATH)
                try:
                    with conn:
                        conn.execute(
                            '''UPDATE broadcasts
                               SET freq_type=?, freq_days=?, updated_at=?
                               WHERE id=? AND kind=?''',
                            (
                                draft['freq_type'], draft.get('freq_days', ''),
                                _timestamp(), template_id, KIND_TASK,
                            ),
                        )
                finally:
                    conn.close()
                _task_admin_drafts.pop(chat_id, None)
                bot.answer_callback_query(call.id, 'Дни выполнения обновлены.')
                _task_template_card(call.message, template_id, bot)
                return
            actor = _actor_snapshot(call)
            draft['created_by'] = actor['login'] if actor else None
            bot.answer_callback_query(call.id)
            _prompt_task_announcement(chat_id, bot)
            return
        if data.startswith('stadmin_announce_'):
            bot.answer_callback_query(call.id)
            _save_task_template(chat_id, bot, data.endswith('_yes'))
            return
        if data == 'stadmin_list':
            bot.answer_callback_query(call.id)
            show_task_templates(call.message, bot)
            return
        match = re.fullmatch(r'stadmin_(manage|toggle|time|due|title|text|editclubs|frequency|requirements|history)_(\d+)', data)
        if match:
            action, raw_id = match.groups()
            template_id = int(raw_id)
            if action == 'manage':
                bot.answer_callback_query(call.id)
                _task_template_card(call.message, template_id, bot)
            elif action == 'toggle':
                conn = sqlite3.connect(DB_PATH)
                try:
                    with conn:
                        conn.execute(
                            '''UPDATE broadcasts SET status=CASE status WHEN 1 THEN 0 ELSE 1 END,
                               updated_at=? WHERE id=? AND kind=?''',
                            (_timestamp(), template_id, KIND_TASK),
                        )
                finally:
                    conn.close()
                bot.answer_callback_query(call.id, 'Статус изменён.')
                _task_template_card(call.message, template_id, bot)
            elif action == 'time':
                _admin_edit_value_prompt(call, bot, 'time', 'Введите новое время появления в формате ЧЧ:ММ.')
            elif action == 'due':
                _admin_edit_value_prompt(call, bot, 'due_time', 'Введите новый срок в формате ЧЧ:ММ.')
            elif action == 'text':
                _admin_edit_value_prompt(call, bot, 'text', 'Введите новое описание задачи.')
            elif action == 'title':
                _admin_edit_value_prompt(call, bot, 'title', 'Введите новое название задачи.')
            elif action == 'editclubs':
                conn = sqlite3.connect(DB_PATH)
                conn.row_factory = sqlite3.Row
                try:
                    row = conn.execute('SELECT clubs_json FROM broadcasts WHERE id=?', (template_id,)).fetchone()
                    selected = json.loads(row['clubs_json'] or '[]') if row else []
                finally:
                    conn.close()
                _task_admin_drafts[chat_id] = {'editing_template_id': template_id, 'clubs': selected}
                bot.answer_callback_query(call.id)
                bot.send_message(chat_id, 'Выберите новый список клубов:', reply_markup=_admin_clubs_keyboard(selected))
            elif action == 'frequency':
                _task_admin_drafts[chat_id] = {
                    'editing_frequency_id': template_id,
                }
                bot.answer_callback_query(call.id)
                bot.send_message(
                    chat_id,
                    'Выберите новые дни выполнения:',
                    reply_markup=_admin_task_days_keyboard(),
                )
            elif action == 'history':
                bot.answer_callback_query(call.id)
                _task_template_history(call.message, template_id, bot)
            else:
                _admin_requirement_clubs(call, bot, template_id)
            return
        req_match = re.fullmatch(r'stadmin_reqclub_(\d+)_(\d+)', data)
        if req_match:
            template_id, index = map(int, req_match.groups())
            clubs = _task_admin_drafts.get(chat_id, {}).get('requirement_clubs', [])
            if index >= len(clubs):
                bot.answer_callback_query(call.id, 'Список клубов устарел.', show_alert=True)
                return
            bot.answer_callback_query(call.id)
            _admin_requirements_prompt(call, bot, template_id, clubs[index])
