import json
import os
import re
import sqlite3
import threading
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import requests

from kpi_calculator import PHYSICAL_KPI_CLUBS, calculate_monthly_kpi
from permissions import (
    ACTIVE_ROLES,
    ROLE_EMPLOYEE,
    ROLE_MANAGER,
    ROLE_OWNER,
    ROLE_TECHNICIAN,
)
from task_notifications import REPAIR_TASK_TYPE


DB_PATH = 'db/omgbot.sql'
MOSCOW = ZoneInfo('Europe/Moscow')
OPENROUTER_ENDPOINT = 'https://openrouter.ai/api/v1/chat/completions'
PROMPT_VERSION = 'birthday-v1'
SEND_AFTER = time(10, 15)
_check_lock = threading.Lock()

COMMON_PROMPT = '''Ты — Виарыч, внутренний Telegram-бот команды OMG VR.

Напиши личное поздравление сотруднику с днём рождения от первого лица — именно от Виарыча.

Стиль:
- тёплый, живой и слегка шутливый;
- без корпоративных штампов и чрезмерного пафоса;
- как от хорошо знакомого бота, который давно находится рядом с командой;
- 2 коротких абзаца;
- 450–700 символов;
- можно использовать не более двух уместных эмодзи.

Правила:
- обращайся к человеку по указанному имени или нику;
- не упоминай возраст и год рождения;
- не угадывай пол человека и избегай формулировок вроде «рад/рада»;
- не упоминай нейросеть, генерацию текста или промпт;
- не используй Markdown, списки, заголовки и подпись;
- не придумывай события, качества, результаты и числовые показатели;
- используй только переданные факты;
- не повторяй все показатели подряд: выбери один или два как основу поздравления;
- не сравнивай человека с коллегами, если это прямо не указано в фактах;
- не упоминай штрафы, неудачи, низкие показатели или отсутствие результатов;
- верни только готовый текст поздравления.'''

ROLE_PROMPTS = {
    ROLE_EMPLOYEE: '''Это сотрудник клуба.

Сделай акцент на том, что человек является важной частью повседневной жизни OMG VR: встречает гостей, проводит смены, создаёт атмосферу и помогает клубам работать.

Если среди фактов есть сильный KPI, отзывы, продления, сертификаты, абонементы, мероприятия или замеченные ремонтные проблемы — естественно упомяни один или два наиболее заметных результата.

Не превращай поздравление в отчёт и не перечисляй цифры подряд.''',
    ROLE_TECHNICIAN: '''Это сотрудник с ролью ремонтника.

Сделай акцент на надёжности, внимательности к проблемам и способности доводить сложные задачи до решения.

В первую очередь используй факты о решённых ремонтах, заявках, заменах оборудования и работе с разными клубами. Если таких фактов мало, можно использовать смены, KPI и показатели работы с гостями.

Не называй человека «мастером на все руки» и не утверждай, что он что-либо починил, если этого нет в переданных фактах.''',
    ROLE_MANAGER: '''Это менеджер OMG VR.

Сделай поздравление немного более собранным, но всё ещё тёплым и неофициальным. Можно отметить надёжность, способность поддерживать процессы и помогать команде двигаться вперёд.

Приоритетные факты: стабильность KPI, работа с разными клубами, решение заявок, отчёты смен, инициативы и сильные показатели работы с гостями.

Не приписывай человеку результаты всей команды. Не утверждай, что благодаря менеджеру выросли показатели клуба, если такого факта нет.''',
    ROLE_OWNER: '''Это один из руководителей OMG VR.

Напиши личное, тёплое и уважительное поздравление без статистики, рабочих результатов и блока с итогами года.

Можно поблагодарить за идеи, направление движения и развитие OMG VR, но без конкретных выдуманных событий и достижений.

Сохрани лёгкий характер Виарыча: без официоза, лести и чрезмерного пафоса.''',
}


def normalize_login(value):
    login = str(value or '').strip()
    if not login:
        return ''
    return login if login.startswith('@') else f'@{login}'


def initialize_birthday_schema(db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            conn.executescript(
                '''
                CREATE TABLE IF NOT EXISTS birthday_greeting_preferences (
                    employee_id INTEGER PRIMARY KEY,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS birthday_greeting_deliveries (
                    employee_id INTEGER NOT NULL,
                    birthday_year INTEGER NOT NULL,
                    birthday_date TEXT NOT NULL,
                    message_text TEXT NOT NULL,
                    facts_json TEXT NOT NULL,
                    generation_source TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    sent_at TEXT,
                    target_chat_id TEXT,
                    PRIMARY KEY(employee_id, birthday_year)
                );
                CREATE INDEX IF NOT EXISTS idx_birthday_greeting_pending
                    ON birthday_greeting_deliveries(sent_at, birthday_date);
                '''
            )
    finally:
        conn.close()


def birthday_public_enabled(employee_id, db_path=DB_PATH):
    initialize_birthday_schema(db_path)
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            '''SELECT enabled FROM birthday_greeting_preferences
               WHERE employee_id=?''',
            (employee_id,),
        ).fetchone()
        return not row or bool(row[0])
    finally:
        conn.close()


def toggle_birthday_public(employee_id, db_path=DB_PATH):
    enabled = not birthday_public_enabled(employee_id, db_path)
    now = datetime.now(MOSCOW).isoformat(timespec='seconds')
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            conn.execute(
                '''INSERT INTO birthday_greeting_preferences(
                       employee_id, enabled, updated_at
                   ) VALUES (?, ?, ?)
                   ON CONFLICT(employee_id) DO UPDATE SET
                       enabled=excluded.enabled,
                       updated_at=excluded.updated_at''',
                (employee_id, int(enabled), now),
            )
    finally:
        conn.close()
    return enabled


def _table_exists(conn, table):
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone())


def _birthday_in_year(birthday, year):
    try:
        return birthday.replace(year=year)
    except ValueError:
        return date(year, 2, 28)


def _personal_year(birthday, today):
    try:
        start = today.replace(year=today.year - 1)
    except ValueError:
        start = date(today.year - 1, 2, 28)
    return start, today


def _is_birthday_today(birthday, today):
    if birthday.month == today.month and birthday.day == today.day:
        return True
    return (
        birthday.month == 2
        and birthday.day == 29
        and today.month == 2
        and today.day == 28
        and not _is_leap_year(today.year)
    )


def _is_leap_year(year):
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def _active_user_by_login(login, db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            f'''SELECT * FROM users
                WHERE lower(login)=lower(?)
                  AND status IN ({','.join('?' for _ in ACTIVE_ROLES)})
                ORDER BY ID LIMIT 1''',
            (normalize_login(login), *sorted(ACTIVE_ROLES)),
        ).fetchone()
    finally:
        conn.close()


def _active_birthday_users(today, db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            f'''SELECT users.*,
                       COALESCE(preferences.enabled, 1) birthday_enabled
                FROM users
                LEFT JOIN birthday_greeting_preferences preferences
                  ON preferences.employee_id=users.ID
                WHERE users.status IN ({','.join('?' for _ in ACTIVE_ROLES)})
                  AND users.bday IS NOT NULL AND trim(users.bday)<>''
                ORDER BY users.ID''',
            tuple(sorted(ACTIVE_ROLES)),
        ).fetchall()
    finally:
        conn.close()
    result = []
    for user in rows:
        try:
            birthday = date.fromisoformat(str(user['bday']).split()[0])
        except (TypeError, ValueError):
            continue
        if user['birthday_enabled'] and _is_birthday_today(birthday, today):
            result.append(user)
    return result


def _number(conn, sql, params):
    row = conn.execute(sql, params).fetchone()
    return float(row[0] or 0) if row else 0.0


def _direct_metric(conn, table, date_column, expression, login, start, end, where=''):
    if not _table_exists(conn, table):
        return 0.0
    return _number(
        conn,
        f'''SELECT {expression} FROM {table}
            WHERE lower(who)=lower(?)
              AND date(substr({date_column}, 1, 10))>=date(?)
              AND date(substr({date_column}, 1, 10))<date(?)
              {where}''',
        (login, start.isoformat(), end.isoformat()),
    )


def _best_completed_month_kpi(login, start, end, db_path=DB_PATH):
    month = start.replace(day=1)
    if start.day > 1:
        month = _next_month(month)
    best = 0.0
    while _next_month(month) <= end:
        rows = calculate_monthly_kpi(
            month,
            db_path=db_path,
            employee_logins=[login],
        )
        row = rows[0] if rows else None
        if row and float(row.get('shifts') or 0) >= 5:
            best = max(best, float(row.get('total_pct') or 0))
        month = _next_month(month)
    return best


def _next_month(value):
    if value.month == 12:
        return date(value.year + 1, 1, 1)
    return date(value.year, value.month + 1, 1)


def collect_personal_year_stats(user, today=None, db_path=DB_PATH):
    today = today or datetime.now(MOSCOW).date()
    role = int(user['status'])
    if role == ROLE_OWNER:
        return {'period_start': None, 'period_end': today.isoformat()}
    birthday = date.fromisoformat(str(user['bday']).split()[0])
    start, end = _personal_year(birthday, today)
    login = normalize_login(user['login'])
    conn = sqlite3.connect(db_path)
    try:
        shift_params = (
            login,
            str(user['second_name'] or ''),
            str(user['first_name'] or ''),
            start.isoformat(),
            end.isoformat(),
        )
        shift_where = '''(
                (shift_login IS NOT NULL AND lower(shift_login)=lower(?))
                OR
                (shift_login IS NULL AND shift_second_name=? AND shift_first_name=?)
            )
            AND date(substr(dt_shift, 1, 10))>=date(?)
            AND date(substr(dt_shift, 1, 10))<date(?)'''
        hours = _number(
            conn,
            f'SELECT SUM(COALESCE(dur, 0)) FROM shifts WHERE {shift_where}',
            shift_params,
        ) if _table_exists(conn, 'shifts') else 0.0
        clubs = _number(
            conn,
            f'''SELECT COUNT(DISTINCT club) FROM shifts
                WHERE {shift_where}
                  AND club IN ({','.join('?' for _ in PHYSICAL_KPI_CLUBS)})''',
            (*shift_params, *PHYSICAL_KPI_CLUBS),
        ) if _table_exists(conn, 'shifts') else 0.0
        stats = {
            'period_start': start.isoformat(),
            'period_end': end.isoformat(),
            'shifts': hours / 6.0,
            'hours': hours,
            'clubs': clubs,
            'reviews': _direct_metric(
                conn, 'reviews', 'd_rep', 'SUM(COALESCE(amount, 0))',
                login, start, end,
            ),
            'extensions': _direct_metric(
                conn, 'afterparty', 'dt_rep', 'COUNT(DISTINCT ID)',
                login, start, end,
                "AND COALESCE(status, '')<>'Отклонено'",
            ),
            'certificates': _direct_metric(
                conn, 'sert', 'd_rep', 'COUNT(*)', login, start, end,
            ),
            'subscriptions': _direct_metric(
                conn, 'abik', 'd_rep', 'COUNT(*)', login, start, end,
            ),
            'initiatives': _direct_metric(
                conn, 'initiative', 'dt_rep', 'COUNT(DISTINCT ID)',
                login, start, end,
                "AND COALESCE(status, '')<>'Отклонено'",
            ),
            'birthdays': _direct_metric(
                conn, 'birthday', 'dt_rep', 'COUNT(DISTINCT ID)',
                login, start, end,
                "AND COALESCE(status, '')<>'Отклонено'",
            ),
            'created_repairs': 0.0,
            'solved_repairs': 0.0,
            'replacements': 0.0,
            'shift_reports': 0.0,
        }
        if _table_exists(conn, 'tasks') and _table_exists(conn, 'task_events'):
            event_params = (REPAIR_TASK_TYPE, login, start.isoformat(), end.isoformat())
            stats['created_repairs'] = _number(
                conn,
                '''SELECT COUNT(DISTINCT tasks.ID)
                   FROM tasks JOIN task_events ON task_events.task_id=tasks.ID
                   WHERE tasks.type=? AND task_events.event_type='created'
                     AND lower(task_events.actor_login)=lower(?)
                     AND date(substr(task_events.event_at, 1, 10))>=date(?)
                     AND date(substr(task_events.event_at, 1, 10))<date(?)''',
                event_params,
            )
            stats['solved_repairs'] = _number(
                conn,
                '''SELECT COUNT(DISTINCT tasks.ID)
                   FROM tasks JOIN task_events ON task_events.task_id=tasks.ID
                   WHERE tasks.type=? AND tasks.status='Выполнено'
                     AND task_events.event_type='solution'
                     AND lower(task_events.actor_login)=lower(?)
                     AND date(substr(task_events.event_at, 1, 10))>=date(?)
                     AND date(substr(task_events.event_at, 1, 10))<date(?)''',
                event_params,
            )
        if _table_exists(conn, 'equipment_events'):
            stats['replacements'] = _number(
                conn,
                '''SELECT COUNT(*) FROM equipment_events
                   WHERE event_type='replaced'
                     AND lower(actor_login)=lower(?)
                     AND date(substr(event_at, 1, 10))>=date(?)
                     AND date(substr(event_at, 1, 10))<date(?)''',
                (login, start.isoformat(), end.isoformat()),
            )
        if _table_exists(conn, 'shift_webapp_runs'):
            stats['shift_reports'] = _number(
                conn,
                '''SELECT COUNT(*) FROM shift_webapp_runs
                   WHERE lower(login)=lower(?) AND completed_at IS NOT NULL
                     AND date(substr(completed_at, 1, 10))>=date(?)
                     AND date(substr(completed_at, 1, 10))<date(?)''',
                (login, start.isoformat(), end.isoformat()),
            )
    finally:
        conn.close()
    try:
        stats['kpi_peak'] = _best_completed_month_kpi(
            login, start, end, db_path,
        )
    except Exception as error:
        print(f'Не удалось рассчитать KPI для поздравления {login}: {error}')
        stats['kpi_peak'] = 0.0
    return stats


def _display_number(value):
    value = float(value or 0)
    return str(int(round(value))) if abs(value - round(value)) < 0.05 else f'{value:.1f}'


def _count_phrase(value, one, few, many):
    number = int(round(float(value or 0)))
    last_two = number % 100
    if 11 <= last_two <= 14:
        form = many
    elif number % 10 == 1:
        form = one
    elif 2 <= number % 10 <= 4:
        form = few
    else:
        form = many
    return f'{number} {form}'


def select_positive_facts(stats, role):
    if role == ROLE_OWNER:
        return []
    facts = {
        'shifts': (
            f'{_count_phrase(stats.get("shifts"), "смена", "смены", "смен")} · '
            f'{_count_phrase(stats.get("hours"), "час", "часа", "часов")}'
            if stats.get('shifts') else None
        ),
        'clubs': (
            f'Работа в {_display_number(stats.get("clubs"))} клубах'
            if stats.get('clubs', 0) >= 2 else None
        ),
        'kpi_peak': (
            f'Лучший KPI — {round(float(stats.get("kpi_peak") or 0) * 100)}%'
            if stats.get('kpi_peak', 0) >= 1 else None
        ),
        'reviews': (
            _count_phrase(stats.get('reviews'), 'отзыв', 'отзыва', 'отзывов')
            if stats.get('reviews') else None
        ),
        'extensions': (
            _count_phrase(
                stats.get('extensions'),
                'продление игры', 'продления игр', 'продлений игр',
            )
            if stats.get('extensions') else None
        ),
        'certificates': (
            _count_phrase(
                stats.get('certificates'),
                'сертификат', 'сертификата', 'сертификатов',
            )
            if stats.get('certificates') else None
        ),
        'subscriptions': (
            _count_phrase(
                stats.get('subscriptions'),
                'абонемент', 'абонемента', 'абонементов',
            )
            if stats.get('subscriptions') else None
        ),
        'initiatives': (
            _count_phrase(
                stats.get('initiatives'),
                'принятая инициатива', 'принятые инициативы',
                'принятых инициатив',
            )
            if stats.get('initiatives') else None
        ),
        'birthdays': (
            _count_phrase(
                stats.get('birthdays'),
                'проведённый день рождения', 'проведённых дня рождения',
                'проведённых дней рождения',
            )
            if stats.get('birthdays') else None
        ),
        'created_repairs': (
            _count_phrase(
                stats.get('created_repairs'),
                'замеченный ремонт', 'замеченных ремонта',
                'замеченных ремонтов',
            )
            if stats.get('created_repairs') else None
        ),
        'solved_repairs': (
            _count_phrase(
                stats.get('solved_repairs'),
                'решённый ремонт', 'решённых ремонта', 'решённых ремонтов',
            )
            if stats.get('solved_repairs') else None
        ),
        'replacements': (
            _count_phrase(
                stats.get('replacements'),
                'замена оборудования', 'замены оборудования',
                'замен оборудования',
            )
            if stats.get('replacements') else None
        ),
        'shift_reports': (
            _count_phrase(
                stats.get('shift_reports'),
                'отчёт OMG Shift', 'отчёта OMG Shift', 'отчётов OMG Shift',
            )
            if stats.get('shift_reports') else None
        ),
    }
    priorities = {
        ROLE_EMPLOYEE: (
            'shifts', 'kpi_peak', 'reviews', 'extensions', 'birthdays',
            'certificates', 'subscriptions', 'clubs', 'created_repairs',
        ),
        ROLE_TECHNICIAN: (
            'solved_repairs', 'replacements', 'created_repairs', 'clubs',
            'shifts', 'kpi_peak', 'reviews',
        ),
        ROLE_MANAGER: (
            'initiatives', 'solved_repairs', 'shift_reports', 'clubs',
            'shifts', 'kpi_peak', 'reviews', 'extensions',
        ),
    }
    return [
        facts[key] for key in priorities.get(role, priorities[ROLE_EMPLOYEE])
        if facts.get(key)
    ][:5]


def _fallback_greeting(name, role):
    variants = {
        ROLE_EMPLOYEE: (
            f'{name}, с днём рождения! Желаю, чтобы смены приносили больше '
            'классных историй, гости радовали, а времени на себя всегда '
            'оставалось с запасом. Спасибо, что каждый день делаешь OMG VR '
            'живым и настоящим 💜'
        ),
        ROLE_TECHNICIAN: (
            f'{name}, с днём рождения! Пусть сложные задачи решаются спокойно, '
            'техника ведёт себя прилично, а вне работы остаётся побольше времени '
            'на всё, что действительно радует. Очень ценю твою надёжность! 🛠'
        ),
        ROLE_MANAGER: (
            f'{name}, с днём рождения! Желаю сил, спокойствия и побольше дней, '
            'когда всё складывается именно так, как задумано. Спасибо за '
            'надёжность, внимание к команде и движение OMG VR вперёд 💜'
        ),
        ROLE_OWNER: (
            f'{name}, с днём рождения! Желаю энергии для новых идей, уверенности '
            'в каждом следующем шаге и побольше времени на жизнь за пределами '
            'рабочих чатов. Спасибо за направление и характер OMG VR 🚀'
        ),
    }
    return variants.get(role, variants[ROLE_EMPLOYEE])


def _clean_generated_text(value):
    text = str(value or '').strip().strip('"')
    text = re.sub(r'\*\*|__|```', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    if not text or len(text) > 1400:
        raise ValueError('OpenRouter вернул поздравление недопустимой длины')
    return text


def generate_openrouter_greeting(name, role, facts, session=requests):
    api_key = str(os.getenv('OPENROUTER_API_KEY') or '').strip()
    if not api_key:
        raise ValueError('OPENROUTER_API_KEY не задан')
    role_prompt = ROLE_PROMPTS.get(role, ROLE_PROMPTS[ROLE_EMPLOYEE])
    if role == ROLE_OWNER:
        user_prompt = (
            f'Имя для обращения: {name}\n\n'
            'Напиши поздравление с днём рождения от Виарыча. '
            'Не используй статистику и не упоминай рабочие показатели.'
        )
    else:
        user_prompt = (
            f'Имя для обращения: {name}\n\n'
            'Подтверждённые положительные факты за личный год:\n'
            + ('\n'.join(f'- {fact}' for fact in facts) or 'Фактов для упоминания нет.')
            + '\n\nНапиши поздравление от Виарыча.'
        )
    body = {
        'messages': [
            {'role': 'system', 'content': f'{COMMON_PROMPT}\n\n{role_prompt}'},
            {'role': 'user', 'content': user_prompt},
        ],
        'temperature': 0.75,
        'max_tokens': 450,
    }
    model = str(os.getenv('OPENROUTER_MODEL') or '').strip()
    if model:
        body['model'] = model
    response = session.post(
        OPENROUTER_ENDPOINT,
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
            'HTTP-Referer': 'https://omg-vr.ru',
            'X-Title': 'Виарыч · поздравления',
        },
        json=body,
        timeout=45,
    )
    response.raise_for_status()
    payload = response.json()
    try:
        return _clean_generated_text(payload['choices'][0]['message']['content'])
    except (KeyError, IndexError, TypeError) as error:
        raise ValueError('OpenRouter вернул некорректный ответ') from error


def build_birthday_message(user, today=None, db_path=DB_PATH, generator=None):
    today = today or datetime.now(MOSCOW).date()
    role = int(user['status'])
    name = str(user['nick_name'] or user['first_name'] or user['login']).strip()
    mention = normalize_login(user['login']) or name
    stats = collect_personal_year_stats(user, today, db_path)
    facts = select_positive_facts(stats, role)
    source = 'openrouter'
    try:
        greeting = (generator or generate_openrouter_greeting)(name, role, facts)
    except Exception as error:
        print(f'OpenRouter не сгенерировал поздравление для {mention}: {error}')
        greeting = _fallback_greeting(name, role)
        source = 'fallback'
    parts = [
        f'🎂 Сегодня день рождения у {mention} — {name}!',
        greeting,
    ]
    if role != ROLE_OWNER and facts:
        parts.append('✨ За этот год:\n' + '\n'.join(f'• {fact}' for fact in facts))
    parts.append('— Виарыч 🤖💜')
    return {
        'text': '\n\n'.join(parts),
        'facts': facts,
        'stats': stats,
        'source': source,
        'prompt_version': PROMPT_VERSION,
    }


def build_birthday_preview(login, today=None, db_path=DB_PATH, generator=None):
    user = _active_user_by_login(login, db_path)
    if not user:
        raise ValueError('Активный сотрудник с таким Telegram-тегом не найден')
    return user, build_birthday_message(
        user,
        today=today,
        db_path=db_path,
        generator=generator,
    )


def _delivery(user, year, db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            '''SELECT * FROM birthday_greeting_deliveries
               WHERE employee_id=? AND birthday_year=?''',
            (user['ID'], year),
        ).fetchone()
    finally:
        conn.close()


def _save_delivery(user, today, payload, db_path=DB_PATH):
    now = datetime.now(MOSCOW).isoformat(timespec='seconds')
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            conn.execute(
                '''INSERT OR IGNORE INTO birthday_greeting_deliveries(
                       employee_id, birthday_year, birthday_date, message_text,
                       facts_json, generation_source, prompt_version, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                (
                    user['ID'], today.year, today.isoformat(), payload['text'],
                    json.dumps(payload['facts'], ensure_ascii=False),
                    payload['source'], payload['prompt_version'], now,
                ),
            )
    finally:
        conn.close()
    return _delivery(user, today.year, db_path)


def _mark_sent(user, today, target_chat_id, db_path=DB_PATH):
    now = datetime.now(MOSCOW).isoformat(timespec='seconds')
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            conn.execute(
                '''UPDATE birthday_greeting_deliveries
                   SET sent_at=?, target_chat_id=?
                   WHERE employee_id=? AND birthday_year=?''',
                (now, str(target_chat_id), user['ID'], today.year),
            )
    finally:
        conn.close()


def send_today_birthday_greetings(
    bot,
    target_chat_id,
    now=None,
    db_path=DB_PATH,
    ignore_time=False,
):
    now = now or datetime.now(MOSCOW)
    if now.tzinfo is None:
        now = now.replace(tzinfo=MOSCOW)
    if not ignore_time and now.timetz().replace(tzinfo=None) < SEND_AFTER:
        return 0
    initialize_birthday_schema(db_path)
    sent = 0
    for user in _active_birthday_users(now.date(), db_path):
        delivery = _delivery(user, now.year, db_path)
        if delivery and delivery['sent_at']:
            continue
        if not delivery:
            payload = build_birthday_message(user, now.date(), db_path)
            delivery = _save_delivery(user, now.date(), payload, db_path)
        try:
            bot.send_message(target_chat_id, delivery['message_text'])
        except Exception as error:
            print(f'Не удалось отправить поздравление {user["login"]}: {error}')
            continue
        _mark_sent(user, now.date(), target_chat_id, db_path)
        sent += 1
    return sent


def start_birthday_greetings_check(bot, target_chat_id, db_path=DB_PATH):
    if not target_chat_id or not _check_lock.acquire(blocking=False):
        return False

    def worker():
        try:
            send_today_birthday_greetings(bot, target_chat_id, db_path=db_path)
        except Exception as error:
            print(f'Ошибка проверки дней рождения: {error}')
        finally:
            _check_lock.release()

    threading.Thread(
        target=worker,
        name='birthday-greetings-check',
        daemon=True,
    ).start()
    return True
