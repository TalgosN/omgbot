import calendar
import json
import math
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta

import sql_scripts


DB_PATH = 'db/omgbot.sql'
PENALTY_IMPACT = 0.10
STREAM_BONUS = 0.05

DEFAULT_METRIC_SETTINGS = {
    'Отзывы': 0.25,
    'Анкеты': 1.0,
    'Продления': 0.25,
    'Сертификаты': 125.0,
    'Абонементы': 250.0,
    'Инициативы': 0.10,
}

DEFAULT_CLUB_WEIGHTS = {
    'Дмитровка': (0.75, 2.0),
    'Ленинский': (0.9, 2.5),
    'Каширка': (0.75, 2.0),
    'Прокшино': (0.75, 2.0),
    'Марьино': (0.9, 2.2),
    'Коллцентр': (0.0, 0.0),
}


def initialize_shift_time_schema(db_path=DB_PATH, connection=None):
    conn = connection or sqlite3.connect(db_path)
    owns_connection = connection is None
    try:
        table_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='shifts'"
        ).fetchone()
        if not table_exists:
            return
        with conn:
            columns = {
                row[1] for row in conn.execute('PRAGMA table_info(shifts)')
            }
            for column in ('shift_start', 'shift_end'):
                if column in columns:
                    continue
                try:
                    conn.execute(
                        f'ALTER TABLE shifts ADD COLUMN {column} TEXT'
                    )
                except sqlite3.OperationalError as error:
                    if 'duplicate column name' not in str(error).lower():
                        raise
    finally:
        if owns_connection:
            conn.close()

FACT_FIELDS = {
    'Отзывы': 'reviews',
    'Анкеты': 'forms',
    'Продления': 'extensions',
    'Сертификаты': 'certificates',
    'Абонементы': 'subscriptions',
    'Инициативы': 'initiatives',
    'ДР': 'birthdays',
}


def _month_start(value):
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.replace(day=1)
    raw = str(value or '').strip()
    for date_format in ('%Y-%m-%d', '%d.%m.%Y', '%Y-%m'):
        try:
            return datetime.strptime(raw, date_format).date().replace(day=1)
        except ValueError:
            continue
    try:
        serial = float(raw)
    except ValueError as error:
        raise ValueError(f'Unsupported KPI month: {value}') from error
    return (date(1899, 12, 30) + timedelta(days=serial)).replace(day=1)


def _month_end(month):
    return month.replace(day=calendar.monthrange(month.year, month.month)[1])


def _day(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = str(value or '').strip()
    for date_format in ('%Y-%m-%d', '%d.%m.%Y'):
        try:
            return datetime.strptime(raw, date_format).date()
        except ValueError:
            continue
    raise ValueError(f'Unsupported KPI date: {value}')


def _normalize_login(value):
    login = str(value or '').strip()
    if login and not login.startswith('@'):
        login = f'@{login}'
    return login.lower()


def _sheet_round(value):
    if value >= 0:
        return math.floor(value + 0.5)
    return math.ceil(value - 0.5)


def initialize_kpi_calculation_schema(db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            conn.executescript(
                '''
                CREATE TABLE IF NOT EXISTS kpi_metric_settings (
                    metric TEXT NOT NULL,
                    effective_month DATE NOT NULL,
                    plan REAL NOT NULL,
                    updated_by TEXT,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (metric, effective_month)
                );

                CREATE TABLE IF NOT EXISTS kpi_club_weights (
                    club TEXT NOT NULL,
                    effective_month DATE NOT NULL,
                    weekday_weight REAL NOT NULL,
                    weekend_weight REAL NOT NULL,
                    updated_by TEXT,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (club, effective_month)
                );

                CREATE TABLE IF NOT EXISTS kpi_penalties (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    employee_login TEXT NOT NULL,
                    period_month DATE NOT NULL,
                    reason TEXT NOT NULL,
                    impact_pct REAL NOT NULL DEFAULT 0.10,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_by_login TEXT,
                    source TEXT NOT NULL DEFAULT 'app',
                    source_key TEXT UNIQUE,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    cancelled_by_login TEXT,
                    cancelled_at DATETIME,
                    cancel_reason TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_kpi_penalties_period
                    ON kpi_penalties(period_month, employee_login, status);

                CREATE TABLE IF NOT EXISTS kpi_monthly_streams (
                    employee_login TEXT NOT NULL,
                    period_month DATE NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 0,
                    bonus_pct REAL NOT NULL DEFAULT 0.05,
                    marked_by_login TEXT,
                    source TEXT NOT NULL DEFAULT 'app',
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (employee_login, period_month)
                );

                CREATE TABLE IF NOT EXISTS kpi_month_status (
                    period_month DATE PRIMARY KEY,
                    is_closed INTEGER NOT NULL DEFAULT 0,
                    updated_by_login TEXT,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    snapshot_json TEXT,
                    snapshot_created_at DATETIME
                );
                '''
            )
            month_status_columns = {
                row[1]
                for row in conn.execute(
                    'PRAGMA table_info(kpi_month_status)'
                )
            }
            if 'snapshot_json' not in month_status_columns:
                conn.execute(
                    'ALTER TABLE kpi_month_status ADD COLUMN snapshot_json TEXT'
                )
            if 'snapshot_created_at' not in month_status_columns:
                conn.execute(
                    'ALTER TABLE kpi_month_status '
                    'ADD COLUMN snapshot_created_at DATETIME'
                )
            metric_columns = {
                row[1]
                for row in conn.execute(
                    'PRAGMA table_info(kpi_metric_settings)'
                )
            }
            if 'price' in metric_columns:
                conn.execute(
                    'ALTER TABLE kpi_metric_settings DROP COLUMN price'
                )
            seed_month = '1970-01-01'
            conn.executemany(
                '''
                INSERT OR IGNORE INTO kpi_metric_settings (
                    metric, effective_month, plan, updated_by
                ) VALUES (?, ?, ?, 'system')
                ''',
                [
                    (metric, seed_month, plan)
                    for metric, plan in DEFAULT_METRIC_SETTINGS.items()
                ],
            )
            conn.execute(
                '''
                UPDATE kpi_metric_settings
                SET plan=0.10,
                    updated_by='system:initiative_bonus_migration',
                    updated_at=CURRENT_TIMESTAMP
                WHERE metric='Инициативы'
                  AND effective_month='1970-01-01'
                  AND plan=0.025
                '''
            )
            supported_metrics = tuple(DEFAULT_METRIC_SETTINGS)
            conn.execute(
                f'''
                DELETE FROM kpi_metric_settings
                WHERE metric NOT IN ({
                    ','.join('?' for _ in supported_metrics)
                })
                ''',
                supported_metrics,
            )
            conn.executemany(
                '''
                INSERT OR IGNORE INTO kpi_club_weights (
                    club, effective_month, weekday_weight, weekend_weight, updated_by
                ) VALUES (?, ?, ?, ?, 'system')
                ''',
                [
                    (club, seed_month, weekday, weekend)
                    for club, (weekday, weekend) in DEFAULT_CLUB_WEIGHTS.items()
                ],
            )
            _mirror_legacy_penalties(conn)
    finally:
        conn.close()


def _mirror_legacy_penalties(conn):
    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='penalty'"
    ).fetchone()
    if not table_exists:
        return
    conn.execute(
        '''
        INSERT OR IGNORE INTO kpi_penalties (
            employee_login, period_month, reason, impact_pct, status,
            created_by_login, source, source_key, created_at
        )
        SELECT lower(name), date(dt, 'start of month'), COALESCE(NULLIF(desc, ''), 'Штраф'),
               ?, 'active', 'legacy_import', 'legacy_db',
               'legacy-db:' || ID, COALESCE(dt, CURRENT_TIMESTAMP)
        FROM penalty
        WHERE name IS NOT NULL AND trim(name) <> '' AND dt IS NOT NULL
        ''',
        (PENALTY_IMPACT,),
    )


def mirror_legacy_penalty(
    conn,
    legacy_id,
    employee_login,
    event_date,
    reason,
    created_by_login,
):
    month = _month_start(event_date).isoformat()
    conn.execute(
        '''
        INSERT OR IGNORE INTO kpi_penalties (
            employee_login, period_month, reason, impact_pct, status,
            created_by_login, source, source_key, created_at
        ) VALUES (?, ?, ?, ?, 'active', ?, 'legacy_db', ?, ?)
        ''',
        (
            _normalize_login(employee_login),
            month,
            reason or 'Штраф',
            PENALTY_IMPACT,
            _normalize_login(created_by_login),
            f'legacy-db:{legacy_id}',
            str(event_date),
        ),
    )


def add_penalty(
    employee_login,
    period_month,
    reason,
    created_by_login,
    db_path=DB_PATH,
    source='app',
    source_key=None,
):
    reason = str(reason or '').strip()
    if not reason:
        raise ValueError('Penalty reason cannot be empty')
    initialize_kpi_calculation_schema(db_path)
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            cursor = conn.execute(
                '''
                INSERT INTO kpi_penalties (
                    employee_login, period_month, reason, impact_pct,
                    created_by_login, source, source_key
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    _normalize_login(employee_login),
                    _month_start(period_month).isoformat(),
                    reason,
                    PENALTY_IMPACT,
                    _normalize_login(created_by_login),
                    source,
                    source_key,
                ),
            )
            return cursor.lastrowid
    finally:
        conn.close()


def cancel_penalty(
    penalty_id,
    cancelled_by_login,
    cancel_reason,
    db_path=DB_PATH,
):
    cancel_reason = str(cancel_reason or '').strip()
    if not cancel_reason:
        raise ValueError('Penalty cancellation reason cannot be empty')
    initialize_kpi_calculation_schema(db_path)
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            cursor = conn.execute(
                '''
                UPDATE kpi_penalties
                SET status='cancelled',
                    cancelled_by_login=?,
                    cancelled_at=CURRENT_TIMESTAMP,
                    cancel_reason=?
                WHERE id=? AND status='active'
                ''',
                (
                    _normalize_login(cancelled_by_login),
                    cancel_reason,
                    penalty_id,
                ),
            )
            return cursor.rowcount == 1
    finally:
        conn.close()


def list_penalties(period_month, db_path=DB_PATH):
    initialize_kpi_calculation_schema(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            '''
            SELECT id, employee_login, period_month, reason, impact_pct,
                   status, created_by_login, source, created_at,
                   cancelled_by_login, cancelled_at, cancel_reason
            FROM kpi_penalties
            WHERE date(period_month)=date(?)
            ORDER BY created_at DESC, id DESC
            ''',
            (_month_start(period_month).isoformat(),),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_month_status(period_month, db_path=DB_PATH):
    initialize_kpi_calculation_schema(db_path)
    month = _month_start(period_month).isoformat()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            '''
            SELECT period_month, is_closed, updated_by_login, updated_at,
                   snapshot_json, snapshot_created_at
            FROM kpi_month_status
            WHERE date(period_month)=date(?)
            ''',
            (month,),
        ).fetchone()
        if not row:
            return {
                'period_month': month,
                'is_closed': False,
                'updated_by_login': None,
                'updated_at': None,
                'snapshot': None,
                'snapshot_created_at': None,
            }
        result = dict(row)
        result['is_closed'] = bool(result['is_closed'])
        raw_snapshot = result.pop('snapshot_json', None)
        result['snapshot'] = (
            json.loads(raw_snapshot)
            if raw_snapshot else None
        )
        return result
    finally:
        conn.close()


def set_month_status(
    period_month,
    is_closed,
    updated_by_login,
    db_path=DB_PATH,
    snapshot=None,
):
    initialize_kpi_calculation_schema(db_path)
    month = _month_start(period_month).isoformat()
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            conn.execute(
                '''
                INSERT INTO kpi_month_status (
                    period_month, is_closed, updated_by_login, updated_at,
                    snapshot_json, snapshot_created_at
                ) VALUES (?, ?, ?, CURRENT_TIMESTAMP, ?,
                          CASE WHEN ?=1 THEN CURRENT_TIMESTAMP ELSE NULL END)
                ON CONFLICT(period_month) DO UPDATE SET
                    is_closed=excluded.is_closed,
                    updated_by_login=excluded.updated_by_login,
                    updated_at=CURRENT_TIMESTAMP,
                    snapshot_json=CASE
                        WHEN excluded.is_closed=1 THEN excluded.snapshot_json
                        ELSE kpi_month_status.snapshot_json
                    END,
                    snapshot_created_at=CASE
                        WHEN excluded.is_closed=1 THEN CURRENT_TIMESTAMP
                        ELSE kpi_month_status.snapshot_created_at
                    END
                ''',
                (
                    month,
                    int(bool(is_closed)),
                    _normalize_login(updated_by_login),
                    (
                        json.dumps(
                            snapshot,
                            ensure_ascii=False,
                            separators=(',', ':'),
                        )
                        if is_closed and snapshot is not None else None
                    ),
                    int(bool(is_closed)),
                ),
            )
    finally:
        conn.close()
    return get_month_status(month, db_path)


def set_monthly_stream(
    employee_login,
    period_month,
    enabled,
    marked_by_login,
    db_path=DB_PATH,
    source='app',
    overwrite=True,
):
    initialize_kpi_calculation_schema(db_path)
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            if overwrite:
                conn.execute(
                    '''
                    INSERT INTO kpi_monthly_streams (
                        employee_login, period_month, enabled, bonus_pct,
                        marked_by_login, source, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(employee_login, period_month) DO UPDATE SET
                        enabled=excluded.enabled,
                        bonus_pct=excluded.bonus_pct,
                        marked_by_login=excluded.marked_by_login,
                        source=excluded.source,
                        updated_at=CURRENT_TIMESTAMP
                    ''',
                    (
                        _normalize_login(employee_login),
                        _month_start(period_month).isoformat(),
                        int(bool(enabled)),
                        STREAM_BONUS,
                        _normalize_login(marked_by_login),
                        source,
                    ),
                )
            else:
                conn.execute(
                    '''
                    INSERT OR IGNORE INTO kpi_monthly_streams (
                        employee_login, period_month, enabled, bonus_pct,
                        marked_by_login, source
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ''',
                    (
                        _normalize_login(employee_login),
                        _month_start(period_month).isoformat(),
                        int(bool(enabled)),
                        STREAM_BONUS,
                        _normalize_login(marked_by_login),
                        source,
                    ),
                )
    finally:
        conn.close()


def import_sheet_penalty(
    employee_login,
    period_month,
    reason,
    source_key,
    db_path=DB_PATH,
):
    initialize_kpi_calculation_schema(db_path)
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            cursor = conn.execute(
                '''
                INSERT OR IGNORE INTO kpi_penalties (
                    employee_login, period_month, reason, impact_pct, status,
                    created_by_login, source, source_key
                ) VALUES (?, ?, ?, ?, 'active', 'legacy_import',
                          'legacy_google_sheet', ?)
                ''',
                (
                    _normalize_login(employee_login),
                    _month_start(period_month).isoformat(),
                    reason,
                    PENALTY_IMPACT,
                    source_key,
                ),
            )
            return cursor.rowcount == 1
    finally:
        conn.close()


def _settings_for_month(conn, table, month, value_columns):
    columns = ', '.join(value_columns)
    key_column = 'metric' if table == 'kpi_metric_settings' else 'club'
    rows = conn.execute(
        f'''
        SELECT settings.{key_column}, {columns}
        FROM {table} settings
        JOIN (
            SELECT {key_column}, MAX(effective_month) AS effective_month
            FROM {table}
            WHERE date(effective_month) <= date(?)
            GROUP BY {key_column}
        ) latest
          ON latest.{key_column}=settings.{key_column}
         AND latest.effective_month=settings.effective_month
        ''',
        (month.isoformat(),),
    ).fetchall()
    return {row[0]: tuple(float(value) for value in row[1:]) for row in rows}


def get_kpi_settings(period_month, db_path=DB_PATH):
    month = _month_start(period_month)
    initialize_kpi_calculation_schema(db_path)
    conn = sqlite3.connect(db_path)
    try:
        metric_settings = conn.execute(
            '''
            SELECT settings.metric, settings.plan, settings.effective_month,
                   settings.updated_by, settings.updated_at
            FROM kpi_metric_settings settings
            JOIN (
                SELECT metric, MAX(effective_month) AS effective_month
                FROM kpi_metric_settings
                WHERE date(effective_month) <= date(?)
                GROUP BY metric
            ) latest
              ON latest.metric=settings.metric
             AND latest.effective_month=settings.effective_month
            ''',
            (month.isoformat(),),
        ).fetchall()
        club_weights = conn.execute(
            '''
            SELECT settings.club, settings.weekday_weight,
                   settings.weekend_weight, settings.effective_month,
                   settings.updated_by, settings.updated_at
            FROM kpi_club_weights settings
            JOIN (
                SELECT club, MAX(effective_month) AS effective_month
                FROM kpi_club_weights
                WHERE date(effective_month) <= date(?)
                GROUP BY club
            ) latest
              ON latest.club=settings.club
             AND latest.effective_month=settings.effective_month
            ''',
            (month.isoformat(),),
        ).fetchall()
    finally:
        conn.close()

    metrics_by_name = {row[0]: row for row in metric_settings}
    clubs_by_name = {row[0]: row for row in club_weights}
    return {
        'period_month': month.isoformat(),
        'metrics': [
            {
                'metric': metric,
                'value': float(metrics_by_name[metric][1]),
                'kind': 'initiative_bonus' if metric == 'Инициативы' else 'plan',
                'effective_month': metrics_by_name[metric][2],
                'updated_by': metrics_by_name[metric][3],
                'updated_at': metrics_by_name[metric][4],
            }
            for metric in DEFAULT_METRIC_SETTINGS
            if metric in metrics_by_name
        ],
        'clubs': [
            {
                'club': club,
                'weekday_weight': float(clubs_by_name[club][1]),
                'weekend_weight': float(clubs_by_name[club][2]),
                'effective_month': clubs_by_name[club][3],
                'updated_by': clubs_by_name[club][4],
                'updated_at': clubs_by_name[club][5],
            }
            for club in DEFAULT_CLUB_WEIGHTS
            if club in clubs_by_name
        ],
    }


def save_kpi_settings(
    period_month,
    metrics,
    clubs,
    updated_by,
    db_path=DB_PATH,
):
    month = _month_start(period_month)
    expected_metrics = set(DEFAULT_METRIC_SETTINGS)
    expected_clubs = set(DEFAULT_CLUB_WEIGHTS)
    if set(metrics) != expected_metrics:
        raise ValueError('Передан неполный или неизвестный набор показателей KPI')
    if set(clubs) != expected_clubs:
        raise ValueError('Передан неполный или неизвестный набор клубов KPI')

    try:
        metric_values = {
            metric: float(value)
            for metric, value in metrics.items()
        }
        club_values = {
            club: (float(values[0]), float(values[1]))
            for club, values in clubs.items()
        }
    except (TypeError, ValueError) as error:
        raise ValueError('Настройки KPI должны быть числами') from error
    if any(not math.isfinite(value) or value <= 0 for value in metric_values.values()):
        raise ValueError('Нормы и бонус инициативы должны быть больше нуля')
    if any(
        not math.isfinite(value) or value < 0
        for weights in club_values.values()
        for value in weights
    ):
        raise ValueError('Веса смен не могут быть отрицательными')

    initialize_kpi_calculation_schema(db_path)
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            conn.executemany(
                '''
                INSERT INTO kpi_metric_settings (
                    metric, effective_month, plan, updated_by, updated_at
                ) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(metric, effective_month) DO UPDATE SET
                    plan=excluded.plan,
                    updated_by=excluded.updated_by,
                    updated_at=CURRENT_TIMESTAMP
                ''',
                [
                    (metric, month.isoformat(), value, updated_by)
                    for metric, value in metric_values.items()
                ],
            )
            conn.executemany(
                '''
                INSERT INTO kpi_club_weights (
                    club, effective_month, weekday_weight,
                    weekend_weight, updated_by, updated_at
                ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(club, effective_month) DO UPDATE SET
                    weekday_weight=excluded.weekday_weight,
                    weekend_weight=excluded.weekend_weight,
                    updated_by=excluded.updated_by,
                    updated_at=CURRENT_TIMESTAMP
                ''',
                [
                    (
                        club,
                        month.isoformat(),
                        weekday_weight,
                        weekend_weight,
                        updated_by,
                    )
                    for club, (weekday_weight, weekend_weight)
                    in club_values.items()
                ],
            )
    finally:
        conn.close()
    return get_kpi_settings(month, db_path=db_path)


def _employees(conn, employee_logins):
    params = []
    where = "WHERE login IS NOT NULL AND trim(login) <> ''"
    if employee_logins is not None:
        normalized = sorted({_normalize_login(login) for login in employee_logins})
        if not normalized:
            return []
        placeholders = ','.join('?' for _ in normalized)
        where += f' AND lower(login) IN ({placeholders})'
        params.extend(normalized)
    rows = conn.execute(
        f'''
        SELECT lower(login), COALESCE(NULLIF(nick_name, ''), NULLIF(first_name, ''), login)
        FROM users
        {where}
        ORDER BY ID
        ''',
        params,
    ).fetchall()
    return [(row[0], row[1]) for row in rows]


def _metric_facts(conn, start, end):
    union_sql = sql_scripts.union.strip().rstrip(';')
    rows = conn.execute(
        f'''
        SELECT lower(s_name), kpi, SUM(fact)
        FROM ({union_sql}) source
        WHERE date(dt_rep) BETWEEN date(?) AND date(?)
          AND kpi <> 'Штрафы'
        GROUP BY lower(s_name), kpi
        ''',
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    facts = {}
    for login, metric, value in rows:
        facts.setdefault(login, {})[metric] = float(value or 0)
    return facts


def _birthday_facts(conn, start, end):
    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='birthday'"
    ).fetchone()
    if not table_exists:
        return {}
    rows = conn.execute(
        '''
        SELECT lower(who), COUNT(DISTINCT ID)
        FROM birthday
        WHERE date(dt_rep) BETWEEN date(?) AND date(?)
          AND COALESCE(status, '') <> 'Отклонено'
        GROUP BY lower(who)
        ''',
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    return {login: float(value or 0) for login, value in rows}


def _shift_totals(conn, start, end, weights):
    rows = conn.execute(
        '''
        SELECT lower(ns.login), sh.club, date(substr(sh.dt_shift, 1, 10)), sh.dur
        FROM shifts sh
        JOIN users ns ON (
            sh.shift_login IS NOT NULL
            AND lower(sh.shift_login)=lower(ns.login)
        ) OR (
            sh.shift_login IS NULL
            AND sh.shift_second_name=ns.second_name
            AND sh.shift_first_name=ns.first_name
        )
        WHERE date(substr(sh.dt_shift, 1, 10)) BETWEEN date(?) AND date(?)
        ''',
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    ordinary = {}
    grouped = {}
    for login, club, raw_date, duration in rows:
        duration = float(duration or 0)
        ordinary[login] = ordinary.get(login, 0.0) + round(duration / 6.0, 3)
        shift_date = datetime.strptime(raw_date, '%Y-%m-%d').date()
        weekend = shift_date.weekday() >= 5
        key = (login, club, weekend)
        grouped[key] = grouped.get(key, 0.0) + duration

    weighted = {}
    for (login, club, weekend), duration in grouped.items():
        weekday_weight, weekend_weight = weights.get(club, (0.0, 0.0))
        weight = weekend_weight if weekend else weekday_weight
        rounded_shifts = _sheet_round(duration / 6.0)
        weighted[login] = weighted.get(login, 0.0) + rounded_shifts * weight
    return ordinary, weighted


def _penalties(conn, month):
    rows = conn.execute(
        '''
        SELECT lower(employee_login), COUNT(*), COALESCE(SUM(impact_pct), 0)
        FROM kpi_penalties
        WHERE date(period_month)=date(?) AND status='active'
        GROUP BY lower(employee_login)
        ''',
        (month.isoformat(),),
    ).fetchall()
    return {
        login: {'count': int(count), 'impact': float(impact or 0)}
        for login, count, impact in rows
    }


def _streams(conn, month):
    rows = conn.execute(
        '''
        SELECT lower(employee_login), enabled, bonus_pct
        FROM kpi_monthly_streams
        WHERE date(period_month)=date(?)
        ''',
        (month.isoformat(),),
    ).fetchall()
    return {
        login: float(bonus or 0) if enabled else 0.0
        for login, enabled, bonus in rows
    }


def _build_kpi_rows(
    month,
    employees,
    metric_settings,
    facts,
    birthdays,
    ordinary_shifts,
    weighted_shifts,
    penalties,
    streams,
):
    result = []
    for login, nickname in employees:
        employee_facts = facts.get(login, {})
        shifts = ordinary_shifts.get(login, 0.0)
        weighted = weighted_shifts.get(login, 0.0)
        row = {
            'login': login,
            'nickname': nickname,
            'period_month': month.isoformat(),
            'shifts': shifts,
            'weighted_shifts': weighted,
        }
        for metric, field in FACT_FIELDS.items():
            row[field] = employee_facts.get(metric, 0.0)
        row['birthdays'] = birthdays.get(login, row['birthdays'])

        for metric, field in (
            ('Отзывы', 'reviews'),
            ('Анкеты', 'forms'),
            ('Продления', 'extensions'),
            ('Сертификаты', 'certificates'),
            ('Абонементы', 'subscriptions'),
        ):
            plan = metric_settings[metric][0]
            denominator = shifts * plan
            row[f'{field}_plan_per_shift'] = plan
            row[f'{field}_target'] = denominator
            row[f'{field}_pct'] = row[field] / denominator if denominator else 0.0
        row['initiative_bonus_per_item'] = metric_settings['Инициативы'][0]
        row['initiatives_pct'] = (
            row['initiatives'] * row['initiative_bonus_per_item']
        )

        primary = (
            row['reviews_pct'] + row['forms_pct'] + row['extensions_pct']
        ) / 3.0
        secondary = 0.10 * (
            row['certificates_pct']
            + row['subscriptions_pct']
        )
        penalty = penalties.get(login, {'count': 0, 'impact': 0.0})
        stream_bonus = streams.get(login, 0.0)
        row['penalties'] = penalty['count']
        row['penalty_impact'] = penalty['impact']
        row['stream'] = bool(stream_bonus)
        row['stream_bonus'] = stream_bonus
        row['total_pct'] = (
            primary
            + secondary
            + row['initiatives_pct']
            - penalty['impact']
            + stream_bonus
        )
        row['weighted_pct'] = (
            row['total_pct'] * shifts / weighted if weighted else 0.0
        )
        result.append(row)

    participants = [row for row in result if row['shifts'] > 0]
    for row in result:
        row['rank'] = None

    ranked = sorted(
        participants,
        key=lambda item: item['total_pct'],
        reverse=True,
    )
    previous_value = None
    previous_rank = 0
    for index, row in enumerate(ranked, start=1):
        if previous_value is None or row['total_pct'] != previous_value:
            previous_rank = index
            previous_value = row['total_pct']
        row['rank'] = previous_rank

    average = (
        sum(row['total_pct'] for row in participants) / len(participants)
        if participants else 0.0
    )
    for row in result:
        row['average_pct'] = average
        if row['shifts'] <= 0 or average <= 0:
            row['zone'] = '⚪'
        elif row['total_pct'] >= average:
            row['zone'] = '🟢'
        elif row['total_pct'] >= average * 0.8:
            row['zone'] = '🟡'
        else:
            row['zone'] = '🔴'
    return result


def calculate_monthly_kpi(
    period_month,
    db_path=DB_PATH,
    employee_logins=None,
    ensure_schema=True,
    period_end=None,
):
    month = _month_start(period_month)
    end = _day(period_end) if period_end is not None else _month_end(month)
    if end.replace(day=1) != month:
        raise ValueError('KPI period end must belong to the selected month')
    if ensure_schema:
        initialize_kpi_calculation_schema(db_path)
    conn = sqlite3.connect(db_path)
    try:
        employees = _employees(conn, employee_logins)
        metric_settings = _settings_for_month(
            conn,
            'kpi_metric_settings',
            month,
            ('plan',),
        )
        club_weights = _settings_for_month(
            conn,
            'kpi_club_weights',
            month,
            ('weekday_weight', 'weekend_weight'),
        )
        facts = _metric_facts(conn, month, end)
        birthdays = _birthday_facts(conn, month, end)
        ordinary_shifts, weighted_shifts = _shift_totals(
            conn,
            month,
            end,
            club_weights,
        )
        penalties = _penalties(conn, month)
        streams = _streams(conn, month)
    finally:
        conn.close()

    return _build_kpi_rows(
        month,
        employees,
        metric_settings,
        facts,
        birthdays,
        ordinary_shifts,
        weighted_shifts,
        penalties,
        streams,
    )


def calculate_daily_kpi_series(
    period_month,
    period_end,
    db_path=DB_PATH,
    employee_logins=None,
    ensure_schema=True,
):
    month = _month_start(period_month)
    end = _day(period_end)
    if end.replace(day=1) != month:
        raise ValueError('KPI period end must belong to the selected month')
    if ensure_schema:
        initialize_kpi_calculation_schema(db_path)

    union_sql = sql_scripts.union.strip().rstrip(';')
    conn = sqlite3.connect(db_path)
    try:
        employees = _employees(conn, employee_logins)
        metric_settings = _settings_for_month(
            conn,
            'kpi_metric_settings',
            month,
            ('plan',),
        )
        club_weights = _settings_for_month(
            conn,
            'kpi_club_weights',
            month,
            ('weekday_weight', 'weekend_weight'),
        )
        metric_rows = conn.execute(
            f'''
            SELECT date(dt_rep), lower(s_name), kpi, SUM(fact)
            FROM ({union_sql}) source
            WHERE date(dt_rep) BETWEEN date(?) AND date(?)
              AND kpi <> 'Штрафы'
            GROUP BY date(dt_rep), lower(s_name), kpi
            ORDER BY date(dt_rep)
            ''',
            (month.isoformat(), end.isoformat()),
        ).fetchall()
        birthday_rows = []
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='birthday'"
        ).fetchone():
            birthday_rows = conn.execute(
                '''
                SELECT date(dt_rep), lower(who), COUNT(DISTINCT ID)
                FROM birthday
                WHERE date(dt_rep) BETWEEN date(?) AND date(?)
                  AND COALESCE(status, '') <> 'Отклонено'
                GROUP BY date(dt_rep), lower(who)
                ORDER BY date(dt_rep)
                ''',
                (month.isoformat(), end.isoformat()),
            ).fetchall()
        shift_rows = conn.execute(
            '''
            SELECT date(substr(sh.dt_shift, 1, 10)), lower(ns.login),
                   sh.club, sh.dur
            FROM shifts sh
            JOIN users ns ON (
                sh.shift_login IS NOT NULL
                AND lower(sh.shift_login)=lower(ns.login)
            ) OR (
                sh.shift_login IS NULL
                AND sh.shift_second_name=ns.second_name
                AND sh.shift_first_name=ns.first_name
            )
            WHERE date(substr(sh.dt_shift, 1, 10))
                  BETWEEN date(?) AND date(?)
            ORDER BY date(substr(sh.dt_shift, 1, 10))
            ''',
            (month.isoformat(), end.isoformat()),
        ).fetchall()
        penalties = _penalties(conn, month)
        streams = _streams(conn, month)
    finally:
        conn.close()

    metrics_by_day = defaultdict(list)
    for raw_day, login, metric, value in metric_rows:
        metrics_by_day[raw_day].append((login, metric, float(value or 0)))
    birthdays_by_day = defaultdict(list)
    for raw_day, login, value in birthday_rows:
        birthdays_by_day[raw_day].append((login, float(value or 0)))
    shifts_by_day = defaultdict(list)
    for raw_day, login, club, duration in shift_rows:
        shifts_by_day[raw_day].append((login, club, float(duration or 0)))

    facts = {}
    birthdays = {}
    ordinary_shifts = {}
    grouped_shift_duration = {}
    series = []
    current = month
    while current <= end:
        raw_day = current.isoformat()
        for login, metric, value in metrics_by_day[raw_day]:
            employee_facts = facts.setdefault(login, {})
            employee_facts[metric] = employee_facts.get(metric, 0.0) + value
        for login, value in birthdays_by_day[raw_day]:
            birthdays[login] = birthdays.get(login, 0.0) + value
        for login, club, duration in shifts_by_day[raw_day]:
            ordinary_shifts[login] = (
                ordinary_shifts.get(login, 0.0) + round(duration / 6.0, 3)
            )
            key = (login, club, current.weekday() >= 5)
            grouped_shift_duration[key] = (
                grouped_shift_duration.get(key, 0.0) + duration
            )

        weighted_shifts = {}
        for (login, club, weekend), duration in grouped_shift_duration.items():
            weekday_weight, weekend_weight = club_weights.get(
                club,
                (0.0, 0.0),
            )
            weight = weekend_weight if weekend else weekday_weight
            weighted_shifts[login] = weighted_shifts.get(login, 0.0) + (
                _sheet_round(duration / 6.0) * weight
            )

        series.append({
            'date': raw_day,
            'employees': _build_kpi_rows(
                month,
                employees,
                metric_settings,
                facts,
                birthdays,
                ordinary_shifts,
                weighted_shifts,
                penalties,
                streams,
            ),
        })
        current += timedelta(days=1)
    return series


def compare_with_sheet(server_rows, sheet_rows):
    numeric_fields = (
        'shifts',
        'weighted_shifts',
        'reviews',
        'reviews_pct',
        'forms',
        'forms_pct',
        'extensions',
        'extensions_pct',
        'certificates',
        'certificates_pct',
        'subscriptions',
        'subscriptions_pct',
        'initiatives',
        'initiatives_pct',
        'penalties',
        'total_pct',
        'weighted_pct',
        'rank',
    )
    server_by_login = {
        _normalize_login(row['login']): row
        for row in server_rows
    }
    differences = []
    for sheet_row in sheet_rows:
        login = _normalize_login(sheet_row.get('login'))
        server_row = server_by_login.get(login)
        if not server_row:
            differences.append({
                'login': login,
                'field': 'employee',
                'server': None,
                'sheet': sheet_row.get('nickname'),
            })
            continue
        for field in numeric_fields:
            if field == 'rank' and float(server_row.get('shifts', 0) or 0) <= 0:
                continue
            server_value = float(server_row.get(field, 0) or 0)
            sheet_value = float(sheet_row.get(field, 0) or 0)
            tolerance = 0.0001 if field not in ('rank', 'penalties') else 0
            if abs(server_value - sheet_value) > tolerance:
                differences.append({
                    'login': login,
                    'field': field,
                    'server': server_value,
                    'sheet': sheet_value,
                })
    return differences


def get_metric_entries(
    employee_login,
    period_month,
    metric,
    period_end=None,
    db_path=DB_PATH,
):
    month = _month_start(period_month)
    end = _day(period_end) if period_end is not None else _month_end(month)
    if end.replace(day=1) != month:
        raise ValueError('KPI period end must belong to the selected month')
    login = _normalize_login(employee_login)
    start = month.isoformat()
    finish = end.isoformat()

    direct_queries = {
        'reviews': '''
            SELECT ID, date(d_rep), amount, desc, NULL, NULL, 'Локальная база KPI'
            FROM reviews
            WHERE lower(who)=? AND date(d_rep) BETWEEN date(?) AND date(?)
        ''',
        'extensions': '''
            SELECT ID, date(dt_rep), 1, desc, club, status, 'Локальная база KPI'
            FROM afterparty
            WHERE lower(who)=? AND date(dt_rep) BETWEEN date(?) AND date(?)
              AND COALESCE(status, '') <> 'Отклонено'
        ''',
        'certificates': '''
            SELECT ID, date(d_rep), bonus, '№ ' || COALESCE(num, '—'),
                   NULL, NULL, 'Локальная база KPI'
            FROM sert
            WHERE lower(who)=? AND date(d_rep) BETWEEN date(?) AND date(?)
        ''',
        'subscriptions': '''
            SELECT ID, date(d_rep), bonus, '№ ' || COALESCE(num, '—'),
                   NULL, NULL, 'Локальная база KPI'
            FROM abik
            WHERE lower(who)=? AND date(d_rep) BETWEEN date(?) AND date(?)
        ''',
        'initiatives': '''
            SELECT ID, date(dt_rep), 1, desc, club, status, 'Локальная база KPI'
            FROM initiative
            WHERE lower(who)=? AND date(dt_rep) BETWEEN date(?) AND date(?)
              AND COALESCE(status, '') <> 'Отклонено'
        ''',
    }

    conn = sqlite3.connect(db_path)
    try:
        if metric == 'forms':
            union_sql = sql_scripts.union.strip().rstrip(';')
            rows = conn.execute(
                f'''
                SELECT NULL, date(dt_rep), fact,
                       'Распределено по сменам', NULL, NULL,
                       'Распределение анкет по сменам'
                FROM ({union_sql}) source
                WHERE lower(s_name)=? AND kpi='Анкеты'
                  AND date(dt_rep) BETWEEN date(?) AND date(?)
                ORDER BY date(dt_rep) DESC
                ''',
                (login, start, finish),
            ).fetchall()
        elif metric == 'shifts':
            rows = conn.execute(
                '''
                SELECT sh.rowid, date(substr(sh.dt_shift, 1, 10)),
                       sh.dur / 6.0,
                       printf('%g ч · %s', sh.dur, COALESCE(sh.source, 'смена')),
                       sh.club, NULL, COALESCE(sh.source, 'История смен')
                FROM shifts sh
                JOIN users ns ON (
                    sh.shift_login IS NOT NULL
                    AND lower(sh.shift_login)=lower(ns.login)
                ) OR (
                    sh.shift_login IS NULL
                    AND sh.shift_second_name=ns.second_name
                    AND sh.shift_first_name=ns.first_name
                )
                WHERE lower(ns.login)=?
                  AND date(substr(sh.dt_shift, 1, 10))
                      BETWEEN date(?) AND date(?)
                ORDER BY date(substr(sh.dt_shift, 1, 10)) DESC, sh.rowid DESC
                ''',
                (login, start, finish),
            ).fetchall()
        elif metric in direct_queries:
            rows = conn.execute(
                direct_queries[metric] + ' ORDER BY 2 DESC, 1 DESC',
                (login, start, finish),
            ).fetchall()
        else:
            raise ValueError('Unsupported KPI metric')
    finally:
        conn.close()

    return [
        {
            'id': row[0],
            'date': row[1],
            'value': float(row[2] or 0),
            'description': row[3],
            'club': row[4],
            'status': row[5],
            'source': row[6],
        }
        for row in rows
    ]


def get_hashtag_summaries(
    employee_logins,
    period_month,
    period_end=None,
    db_path=DB_PATH,
):
    month = _month_start(period_month)
    end = _day(period_end) if period_end is not None else _month_end(month)
    if end.replace(day=1) != month:
        raise ValueError('KPI period end must belong to the selected month')
    logins = sorted({_normalize_login(login) for login in employee_logins})
    result = {login: [] for login in logins}
    if not logins:
        return result

    conn = sqlite3.connect(db_path)
    try:
        table_exists = conn.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='hashtag_events'"
        ).fetchone()
        if not table_exists:
            return result
        placeholders = ','.join('?' for _ in logins)
        rows = conn.execute(
            f'''
            SELECT lower(telegram), lower(hashtag), value, lower(value_unit)
            FROM hashtag_events
            WHERE lower(telegram) IN ({placeholders})
              AND status='applied'
              AND date(event_date) BETWEEN date(?) AND date(?)
            ORDER BY lower(hashtag), event_date, id
            ''',
            (*logins, month.isoformat(), end.isoformat()),
        ).fetchall()
    finally:
        conn.close()

    grouped = defaultdict(lambda: {
        'count': 0,
        'total_value': 0.0,
        'units': set(),
    })
    for login, hashtag, value, value_unit in rows:
        item = grouped[(login, hashtag)]
        item['count'] += 1
        if value is not None:
            item['total_value'] += float(value)
        if value_unit:
            item['units'].add(value_unit)

    money_units = {'rubles', 'ruble', 'rub', '₽'}
    for (login, hashtag), values in grouped.items():
        units = values.pop('units')
        value_unit = next(iter(units)) if len(units) == 1 else None
        total_value = values['total_value']
        if value_unit is None or value_unit in money_units:
            total_value = None
        result.setdefault(login, []).append({
            'hashtag': hashtag,
            'count': values['count'],
            'total_value': total_value,
            'value_unit': value_unit if total_value is not None else None,
        })
    return result


def get_hashtag_entries(
    employee_login,
    period_month,
    hashtag,
    period_end=None,
    db_path=DB_PATH,
):
    month = _month_start(period_month)
    end = _day(period_end) if period_end is not None else _month_end(month)
    if end.replace(day=1) != month:
        raise ValueError('KPI period end must belong to the selected month')
    normalized_hashtag = str(hashtag or '').strip().lower()
    if (
        not normalized_hashtag.startswith('#')
        or len(normalized_hashtag) > 64
    ):
        raise ValueError('Unsupported hashtag')

    conn = sqlite3.connect(db_path)
    try:
        table_exists = conn.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='hashtag_events'"
        ).fetchone()
        if not table_exists:
            return []
        rows = conn.execute(
            '''
            SELECT id, date(event_date), value, lower(value_unit), comment,
                   club, source
            FROM hashtag_events
            WHERE lower(telegram)=?
              AND lower(hashtag)=?
              AND status='applied'
              AND date(event_date) BETWEEN date(?) AND date(?)
            ORDER BY date(event_date) DESC, id DESC
            ''',
            (
                _normalize_login(employee_login),
                normalized_hashtag,
                month.isoformat(),
                end.isoformat(),
            ),
        ).fetchall()
    finally:
        conn.close()

    money_units = {'rubles', 'ruble', 'rub', '₽'}
    return [
        {
            'id': row[0],
            'date': row[1],
            'value': (
                None
                if row[3] in money_units or row[2] is None
                else float(row[2])
            ),
            'value_unit': (
                None if row[3] in money_units else row[3]
            ),
            'description': row[4],
            'club': row[5],
            'status': None,
            'source': row[6],
        }
        for row in rows
    ]


def get_kpi_freshness(period_month, period_end=None, db_path=DB_PATH):
    month = _month_start(period_month)
    end = _day(period_end) if period_end is not None else _month_end(month)
    if end.replace(day=1) != month:
        raise ValueError('KPI period end must belong to the selected month')

    union_sql = sql_scripts.union.strip().rstrip(';')
    conn = sqlite3.connect(db_path)
    try:
        latest_metric_date = conn.execute(
            f'''
            SELECT MAX(date(dt_rep))
            FROM ({union_sql}) source
            WHERE date(dt_rep) BETWEEN date(?) AND date(?)
              AND kpi <> 'Штрафы'
            ''',
            (month.isoformat(), end.isoformat()),
        ).fetchone()[0]
        latest_shift_date = conn.execute(
            '''
            SELECT MAX(date(substr(dt_shift, 1, 10)))
            FROM shifts
            WHERE date(substr(dt_shift, 1, 10)) BETWEEN date(?) AND date(?)
            ''',
            (month.isoformat(), end.isoformat()),
        ).fetchone()[0]
    finally:
        conn.close()
    return {
        'latest_metric_date': latest_metric_date,
        'latest_shift_date': latest_shift_date,
    }


def get_kpi_control_status(period_month, db_path=DB_PATH):
    month = _month_start(period_month)
    conn = sqlite3.connect(db_path)
    try:
        penalty_sources = {
            source: int(count)
            for source, count in conn.execute(
                '''
                SELECT source, COUNT(*)
                FROM kpi_penalties
                GROUP BY source
                '''
            ).fetchall()
        }
        current_penalties = conn.execute(
            '''
            SELECT COUNT(*)
            FROM kpi_penalties
            WHERE date(period_month)=date(?) AND status='active'
            ''',
            (month.isoformat(),),
        ).fetchone()[0]
        current_streams = conn.execute(
            '''
            SELECT COUNT(*)
            FROM kpi_monthly_streams
            WHERE date(period_month)=date(?) AND enabled=1
            ''',
            (month.isoformat(),),
        ).fetchone()[0]
    finally:
        conn.close()
    return {
        'period_month': month.isoformat(),
        'penalty_sources': penalty_sources,
        'current_penalties': int(current_penalties),
        'current_streams': int(current_streams),
    }
