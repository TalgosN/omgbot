"""Одноразовый импорт почасовых ставок из старого Shifton в локальную БД."""

import os
import sqlite3
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv


PROJECT_DIR = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_DIR / 'db' / 'omgbot.sql'
COMPANY_ID = 16303
DEFAULT_SCHEDULE_ID = 27521
CALLCENTER_SCHEDULE_ID = 27341

NAME_ALIASES = {
    'Вязгина Даша': 'Вязгина Дарья',
    'Додов Ануш': 'Додов Анушервон',
    'Лукьяненко Катерина': 'Лукьяненко Екатерина',
    'Песков Максим': 'Песков Максон',
}

CREATE_RATES_TABLE = '''
    CREATE TABLE IF NOT EXISTS payroll_rates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        login TEXT NOT NULL,
        club TEXT NOT NULL DEFAULT '*',
        hourly_rate REAL NOT NULL CHECK (hourly_rate >= 0),
        valid_from DATE NOT NULL,
        valid_to DATE,
        source TEXT NOT NULL DEFAULT 'manual',
        UNIQUE(login, club, valid_from)
    )
'''


def get_required_env(name):
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f'Не задана переменная окружения {name}')
    return value


def get_access_headers():
    response = requests.post(
        'https://api2.shifton.com/oauth/token',
        json={
            'username': get_required_env('SHIFTON_USER'),
            'password': get_required_env('SHIFTON_PASS'),
            'client_id': get_required_env('SHIFTON_CLIENT_ID'),
            'client_secret': get_required_env('SHIFTON_CLIENT_SECRET'),
            'grant_type': 'password',
            'scope': '',
        },
        headers={'Accept': 'application/json', 'Content-Type': 'application/json'},
        timeout=20,
    )
    response.raise_for_status()
    token = response.json()
    if not token.get('access_token'):
        raise RuntimeError(f"Shifton не выдал access_token: {token.get('error', 'неизвестная ошибка')}")
    return {
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'Authorization': f"Bearer {token['access_token']}",
        'refresh_token': token.get('refresh_token', ''),
    }


def get_json(url, headers):
    response = requests.get(url, headers=headers, timeout=20)
    response.raise_for_status()
    return response.json()


def resolve_login(conn, old_name):
    current_name = NAME_ALIASES.get(old_name, old_name)
    candidates = set()
    user_columns = {row[1] for row in conn.execute('PRAGMA table_info(users)')}

    for name in dict.fromkeys((current_name, old_name)):
        if {'second_name', 'first_name'} <= user_columns:
            row = conn.execute(
                """SELECT login FROM users
                   WHERE trim(second_name || ' ' || first_name) = ?
                     AND login IS NOT NULL AND trim(login) <> ''""",
                (name,),
            ).fetchall()
            candidates.update(item[0] for item in row)

        row = conn.execute(
            """SELECT DISTINCT shift_login FROM shifts
               WHERE trim(shift_second_name || ' ' || shift_first_name) = ?
                 AND shift_login IS NOT NULL AND trim(shift_login) <> ''""",
            (name,),
        ).fetchall()
        candidates.update(item[0] for item in row)

    by_canonical_login = {str(login).strip().lower(): login for login in candidates}
    if len(by_canonical_login) != 1:
        found = ', '.join(sorted(by_canonical_login.values())) or 'ничего'
        raise RuntimeError(f'{old_name}: найдено {found}')
    return next(iter(by_canonical_login.values()))


def main():
    load_dotenv(PROJECT_DIR / '.env')
    headers = get_access_headers()
    employees = get_json(
        f'https://api2.shifton.com/work/1.0.0/companies/{COMPANY_ID}/employees',
        headers,
    )
    employee_names = {employee['id']: employee['full_name'] for employee in employees}
    schedules = (
        ('*', DEFAULT_SCHEDULE_ID),
        ('Коллцентр', CALLCENTER_SCHEDULE_ID),
    )

    source_rates = []
    for club, schedule_id in schedules:
        schedule = get_json(
            f'https://api.shifton.com/work/1.0.0/schedules/{schedule_id}',
            headers,
        )
        for rate in schedule.get('users', []):
            employee_id = rate['employee_id']
            if employee_id not in employee_names:
                raise RuntimeError(f'В расписании {schedule_id} неизвестный employee_id={employee_id}')
            source_rates.append((employee_names[employee_id], club, float(rate['rate'])))

    conn = sqlite3.connect(DB_PATH)
    try:
        valid_from = conn.execute(
            "SELECT COALESCE(MIN(date(dt_shift)), '1970-01-01') FROM shifts"
        ).fetchone()[0]
        resolved_rates = {}
        errors = []
        for old_name, club, hourly_rate in source_rates:
            try:
                login = resolve_login(conn, old_name)
                key = (login, club)
                if key in resolved_rates and resolved_rates[key] != hourly_rate:
                    raise RuntimeError(f'несколько разных ставок для {login}, {club}')
                resolved_rates[key] = hourly_rate
            except RuntimeError as error:
                errors.append(str(error))

        if errors:
            raise RuntimeError('Импорт остановлен:\n- ' + '\n- '.join(errors))

        with conn:
            conn.execute(CREATE_RATES_TABLE)
            conn.execute(
                'CREATE INDEX IF NOT EXISTS idx_payroll_rates_login_dates '
                'ON payroll_rates(login, valid_from, valid_to)'
            )
            conn.executemany(
                """INSERT INTO payroll_rates
                   (login, club, hourly_rate, valid_from, valid_to, source)
                   VALUES (?, ?, ?, ?, NULL, 'legacy_shifton')
                   ON CONFLICT(login, club, valid_from) DO UPDATE SET
                       hourly_rate = excluded.hourly_rate,
                       source = excluded.source""",
                [
                    (login, club, hourly_rate, valid_from)
                    for (login, club), hourly_rate in resolved_rates.items()
                ],
            )
    finally:
        conn.close()

    employees_count = len({login.lower() for login, _club in resolved_rates})
    print(f'Готово: импортировано {len(resolved_rates)} ставок для {employees_count} сотрудников.')
    print(f'Ставки действуют для истории начиная с {valid_from}.')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(f'Ошибка импорта ставок: {error}', file=sys.stderr)
        raise SystemExit(1)
