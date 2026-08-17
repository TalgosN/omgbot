import re
import sqlite3
from datetime import datetime

from task_analytics import record_task_event


DEFAULT_ITEMS = (
    ('VR-шлем', ('левая линза', 'правая линза', 'маска')),
    ('VR-контроллер', ('левый', 'правый', 'оба')),
    ('Питание и зарядка VR', ('пауэрбанк', 'док-станция', 'зарядка', 'блок питания', 'кабель')),
    ('Компьютер / SteamVR', ('системный блок', 'SteamVR / программа')),
    ('Крепление VR', ()),
    ('Телевизор', ('экран', 'подсветка', 'пульт')),
    ('PlayStation', ()),
    ('Контроллер PlayStation', ('левый стик', 'правый стик', 'целиком')),
    ('Автосимулятор', ('руль', 'педали', 'сцепление', 'кокпит', 'питание')),
    ('Терминал', ()),
    ('Планшет', ()),
    ('Рабочий телефон', ()),
    ('Сеть / программное обеспечение', ()),
    ('Кондиционер', ()),
    ('Освещение / неон', ('лампа', 'неон', 'подсветка')),
    ('Вывеска', ()),
    ('Электрика', ()),
    ('Сантехника / отопление', ()),
    ('Пожарная / охранная система', ()),
    ('Мебель / элементы помещения', ()),
    ('Другое', ()),
)

ZONE_COUNTS = {
    'Ленинский': 8,
    'Марьино': 8,
    'Дмитровка': 8,
    'Прокшино': 8,
    'Каширка': 10,
}


def initialize_repair_schema(db_path='db/omgbot.sql'):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            conn.executescript(
                '''
                CREATE TABLE IF NOT EXISTS repair_item_types (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    active INTEGER NOT NULL DEFAULT 1,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS repair_item_details (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_type_id INTEGER NOT NULL REFERENCES repair_item_types(id),
                    name TEXT NOT NULL COLLATE NOCASE,
                    active INTEGER NOT NULL DEFAULT 1,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(item_type_id, name)
                );
                CREATE TABLE IF NOT EXISTS repair_locations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    club TEXT NOT NULL,
                    name TEXT NOT NULL COLLATE NOCASE,
                    kind TEXT NOT NULL DEFAULT 'other',
                    zone_number INTEGER,
                    active INTEGER NOT NULL DEFAULT 1,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(club, name)
                );
                CREATE TABLE IF NOT EXISTS repair_cases (
                    task_id INTEGER PRIMARY KEY REFERENCES tasks(ID),
                    item_type_id INTEGER NOT NULL REFERENCES repair_item_types(id),
                    detail_id INTEGER REFERENCES repair_item_details(id),
                    mapping_source TEXT NOT NULL DEFAULT 'app',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS repair_case_locations (
                    task_id INTEGER NOT NULL REFERENCES repair_cases(task_id),
                    location_id INTEGER NOT NULL REFERENCES repair_locations(id),
                    PRIMARY KEY(task_id, location_id)
                );
                CREATE TABLE IF NOT EXISTS repair_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL REFERENCES tasks(ID),
                    event_type TEXT NOT NULL,
                    event_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    message TEXT
                );
                CREATE TABLE IF NOT EXISTS repair_migration_exclusions (
                    task_id INTEGER PRIMARY KEY REFERENCES tasks(ID),
                    canonical_task_id INTEGER REFERENCES tasks(ID),
                    reason TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS equipment_units (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    club TEXT NOT NULL,
                    location_id INTEGER NOT NULL REFERENCES repair_locations(id),
                    item_type_id INTEGER NOT NULL REFERENCES repair_item_types(id),
                    generation INTEGER NOT NULL DEFAULT 1,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    retired_at TEXT,
                    replaced_by_id INTEGER REFERENCES equipment_units(id),
                    UNIQUE(club, location_id, item_type_id, generation)
                );
                CREATE TABLE IF NOT EXISTS equipment_unit_tasks (
                    unit_id INTEGER NOT NULL REFERENCES equipment_units(id),
                    task_id INTEGER NOT NULL REFERENCES repair_cases(task_id),
                    PRIMARY KEY(unit_id, task_id)
                );
                CREATE TABLE IF NOT EXISTS equipment_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    unit_id INTEGER NOT NULL REFERENCES equipment_units(id),
                    event_type TEXT NOT NULL,
                    event_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    actor_login TEXT,
                    actor_name TEXT,
                    message TEXT,
                    related_unit_id INTEGER REFERENCES equipment_units(id)
                );
                CREATE INDEX IF NOT EXISTS idx_repair_cases_item
                    ON repair_cases(item_type_id, detail_id);
                CREATE INDEX IF NOT EXISTS idx_repair_locations_club
                    ON repair_locations(club, active, sort_order);
                CREATE INDEX IF NOT EXISTS idx_repair_case_locations_location
                    ON repair_case_locations(location_id, task_id);
                CREATE INDEX IF NOT EXISTS idx_repair_events_task
                    ON repair_events(task_id, event_at, id);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_equipment_active_identity
                    ON equipment_units(club, location_id, item_type_id)
                    WHERE active=1;
                CREATE INDEX IF NOT EXISTS idx_equipment_unit_tasks_task
                    ON equipment_unit_tasks(task_id, unit_id);
                CREATE INDEX IF NOT EXISTS idx_equipment_events_unit
                    ON equipment_events(unit_id, event_at, id);
                '''
            )
            _seed_catalog(conn)
            _migrate_legacy_repairs(conn)
            _sync_equipment_units(conn)
    finally:
        conn.close()


def _seed_catalog(conn):
    for item_order, (item_name, details) in enumerate(DEFAULT_ITEMS, 1):
        conn.execute(
            '''INSERT INTO repair_item_types(name, sort_order)
               VALUES (?, ?) ON CONFLICT(name) DO NOTHING''',
            (item_name, item_order),
        )
        item_id = conn.execute(
            'SELECT id FROM repair_item_types WHERE name=?', (item_name,)
        ).fetchone()[0]
        for detail_order, detail_name in enumerate(details, 1):
            conn.execute(
                '''INSERT INTO repair_item_details(item_type_id, name, sort_order)
                   VALUES (?, ?, ?) ON CONFLICT(item_type_id, name) DO NOTHING''',
                (item_id, detail_name, detail_order),
            )

    for club, zone_count in ZONE_COUNTS.items():
        locations = [
            (f'{number} зона', 'zone', number, number)
            for number in range(1, zone_count + 1)
        ]
        locations.extend((
            ('Админское место', 'admin', None, 100),
            ('Общий клуб', 'general', None, 140),
        ))
        if club == 'Каширка':
            locations.extend((
                ('Большой лаунж', 'lounge', None, 110),
                ('Малый лаунж', 'lounge', None, 111),
            ))
        else:
            locations.append(('Лаунж', 'lounge', None, 110))
        if club in {'Ленинский', 'Марьино', 'Дмитровка'}:
            locations.append(('Автосим', 'autosim', None, 120))
        for name, kind, zone_number, sort_order in locations:
            conn.execute(
                '''INSERT INTO repair_locations(
                       club, name, kind, zone_number, sort_order
                   ) VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(club, name) DO NOTHING''',
                (club, name, kind, zone_number, sort_order),
            )


def _normalized(value):
    return re.sub(r'\s+', ' ', str(value or '').lower().replace('ё', 'е')).strip()


def _item_match(title, text):
    rules = (
        ('Контроллер PlayStation', r'джо?й?ст|геймпад|контроллер.{0,12}(?:ps|пс)|(?:ps|пс).{0,12}контроллер'),
        ('PlayStation', r'playstation|\bps\s*[345]?\b|плейстейш|приставк'),
        ('VR-контроллер', r'контроллер|контр|окулус|джо?й?стик|джойст|дрифт|стик|микрик|курок|колпачок|залипает [ab]'),
        ('VR-шлем', r'шлем|линз|маск'),
        ('Питание и зарядка VR', r'пауэрбанк|powerbank|повер|док.?станц|заряд|блок питан|провод|кабел|\bпб\b|\bбп\b'),
        ('Компьютер / SteamVR', r'steamvr|стим|компьютер|\bкомп\b|системн|\bпк\b|вылетает'),
        ('Крепление VR', r'креплен|кронштейн|трос'),
        ('Телевизор', r'телевиз|телик|телек|\bтв\b|пульт'),
        ('Автосимулятор', r'автосим|симулятор|руль|педал|сцеплен'),
        ('Терминал', r'терминал|касс'),
        ('Планшет', r'планшет|тачскрин|сенсорн.{0,8}экран'),
        ('Рабочий телефон', r'телефон'),
        ('Сеть / программное обеспечение', r'роутер|интернет|wi.?fi|вай.?фай|сеть|программ|vpn|телеграм'),
        ('Кондиционер', r'кондиционер|кондей'),
        ('Освещение / неон', r'подсвет|освещ|ламп|неон|свет'),
        ('Вывеска', r'вывеск'),
        ('Электрика', r'розет|электр|автомат|щиток'),
        ('Сантехника / отопление', r'батаре|радиатор|течет|сантех|кран|унитаз|раковин|труб'),
        ('Пожарная / охранная система', r'сигнализац|пожарн|охранн'),
        ('Мебель / элементы помещения', r'диван|стол|стул|кресл|двер|стен|пол|потол|мебел|ручк|вешал|шкаф|ключ|перил'),
    )
    for source in (title, text):
        for name, pattern in rules:
            if re.search(pattern, source):
                return name
    return None


def _detail_match(item_name, text):
    rules = {
        'VR-шлем': (('левая линза', r'лев.{0,8}линз'), ('правая линза', r'прав.{0,8}линз'), ('маска', r'маск')),
        'VR-контроллер': (('левый', r'лев.{0,12}(?:контрол|джойст)'), ('правый', r'прав.{0,12}(?:контрол|джойст)'), ('оба', r'оба контрол')),
        'Питание и зарядка VR': (('пауэрбанк', r'пауэрбанк|powerbank'), ('док-станция', r'док.?станц'), ('зарядка', r'заряд'), ('блок питания', r'блок питан'), ('кабель', r'кабел|провод')),
        'Телевизор': (('пульт', r'пульт'), ('подсветка', r'подсвет'), ('экран', r'экран|матриц')),
        'Планшет': (),
        'Контроллер PlayStation': (('левый стик', r'лев.{0,8}стик'), ('правый стик', r'прав.{0,8}стик')),
        'Автосимулятор': (('руль', r'руль'), ('педали', r'педал'), ('сцепление', r'сцеплен'), ('кокпит', r'кокпит'), ('питание', r'питан')),
        'Освещение / неон': (('неон', r'неон'), ('лампа', r'ламп'), ('подсветка', r'подсвет')),
    }
    matches = [name for name, pattern in rules.get(item_name, ()) if re.search(pattern, text)]
    return matches[0] if len(matches) == 1 else None


def _location_names(club, text, item_name):
    zone_text = re.sub(r'\bq\d+\b', '', text)
    if club == 'Каширка' and re.search(r'мал(?:ый|ом) (?:зал|лаунж)', text):
        return ['Малый лаунж']
    if club == 'Каширка' and re.search(r'больш(?:ой|ом) (?:зал|лаунж)', text):
        return ['Большой лаунж']
    if re.search(r'админ|ресеп|рецеп', text):
        return ['Админское место']
    if re.search(r'лаунж', text) or item_name in {'PlayStation', 'Контроллер PlayStation'}:
        return ['Лаунж'] if club != 'Каширка' else []
    if re.search(r'автосим|симулятор', text):
        return ['Автосим']
    zone_count = ZONE_COUNTS.get(club, 0)
    zones = {
        int(number)
        for number in re.findall(r'(?<!\d)(\d{1,2})(?=\s*(?:,|и|зон|$))', zone_text)
        if 1 <= int(number) <= zone_count
    }
    zones.update(
        int(number)
        for number in re.findall(r'(?<!\d)(\d{1,2})\s*(?:\(|шлем|комп)', zone_text)
        if 1 <= int(number) <= zone_count
    )
    for first, last in re.findall(r'(?<!\d)(\d{1,2})\s*по\s*(\d{1,2})\s*зон', zone_text):
        start, end = int(first), int(last)
        zones.update(range(max(1, start), min(zone_count, end) + 1))
    for first, last in re.findall(r'(?<!\d)(\d{1,2})\s*[-–]\s*(\d{1,2})', zone_text):
        start, end = int(first), int(last)
        zones.update(range(max(1, start), min(zone_count, end) + 1))
    word_zones = {'первая': 1, 'первый': 1, 'вторая': 2, 'второй': 2}
    zones.update(number for word, number in word_zones.items() if re.search(rf'\b{word}\b', text))
    if zones:
        return [f'{number} зона' for number in sorted(zones)]
    if item_name in {'Терминал', 'Планшет', 'Рабочий телефон', 'Сеть / программное обеспечение'}:
        return ['Админское место']
    if item_name == 'Автосимулятор':
        return ['Автосим']
    if item_name in {
        'Питание и зарядка VR', 'Кондиционер', 'Освещение / неон',
        'Вывеска', 'Электрика', 'Сантехника / отопление',
        'Пожарная / охранная система', 'Мебель / элементы помещения',
    }:
        return ['Общий клуб']
    return []


def _migrate_legacy_repairs(conn):
    task_table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tasks'"
    ).fetchone()
    if not task_table:
        return
    rows = conn.execute(
        '''SELECT ID, club, title, desc, feedback FROM tasks
           WHERE lower(type)=lower('Ремонт') ORDER BY ID'''
    ).fetchall()
    seen = {}
    overrides = {
        20: ('Компьютер / SteamVR', None, ('4 зона',)),
        48: ('Сеть / программное обеспечение', None, ('7 зона', '8 зона')),
        101: ('Освещение / неон', 'подсветка', ('Админское место',)),
        104: ('Освещение / неон', 'подсветка', ('Малый лаунж',)),
        107: ('Телевизор', 'подсветка', ('5 зона', '8 зона')),
        132: ('Сантехника / отопление', None, ('Общий клуб',)),
        153: ('Телевизор', 'пульт', ('Лаунж',)),
        184: ('Пожарная / охранная система', None, ('Общий клуб',)),
        196: ('Телевизор', None, ('3 зона',)),
        248: ('Мебель / элементы помещения', None, ('Общий клуб',)),
        251: ('Планшет', None, ('Админское место',)),
        331: ('Кондиционер', None, ('Лаунж', 'Автосим')),
    }
    for row in rows:
        text = _normalized(' '.join(str(row[key] or '') for key in ('title', 'desc', 'feedback')))
        primary_text = _normalized(' '.join(str(row[key] or '') for key in ('title', 'desc')))
        if text in {'test test тест', 'тест тест тест'} or _normalized(row['title']) in {'test', 'тест'}:
            conn.execute(
                '''INSERT INTO repair_migration_exclusions(task_id, reason)
                   VALUES (?, 'test') ON CONFLICT(task_id) DO NOTHING''',
                (row['ID'],),
            )
            continue
        duplicate_key = (row['club'], _normalized(row['title']), _normalized(row['desc']))
        if duplicate_key in seen:
            conn.execute(
                '''INSERT INTO repair_migration_exclusions(
                       task_id, canonical_task_id, reason
                   ) VALUES (?, ?, 'duplicate')
                   ON CONFLICT(task_id) DO NOTHING''',
                (row['ID'], seen[duplicate_key]),
            )
            continue
        seen[duplicate_key] = row['ID']
        if conn.execute('SELECT 1 FROM repair_cases WHERE task_id=?', (row['ID'],)).fetchone():
            continue

        override = overrides.get(row['ID'])
        if override:
            item_name, detail_name, location_names = override
        else:
            item_name = _item_match(_normalized(row['title']), primary_text)
            detail_name = _detail_match(item_name, primary_text) if item_name else None
            location_names = _location_names(row['club'], primary_text, item_name)
        if not item_name or not location_names:
            continue
        item = conn.execute(
            'SELECT id FROM repair_item_types WHERE name=?', (item_name,)
        ).fetchone()
        location_rows = conn.execute(
            f'''SELECT id FROM repair_locations WHERE club=? AND name IN (
                    {','.join('?' for _ in location_names)}
                )''',
            (row['club'], *location_names),
        ).fetchall()
        if not item or len(location_rows) != len(location_names):
            continue
        detail = conn.execute(
            '''SELECT id FROM repair_item_details
               WHERE item_type_id=? AND name=?''',
            (item['id'], detail_name),
        ).fetchone() if detail_name else None
        conn.execute(
            '''INSERT INTO repair_cases(
                   task_id, item_type_id, detail_id, mapping_source
               ) VALUES (?, ?, ?, 'legacy-auto')''',
            (row['ID'], item['id'], detail['id'] if detail else None),
        )
        conn.executemany(
            '''INSERT INTO repair_case_locations(task_id, location_id)
               VALUES (?, ?)''',
            ((row['ID'], location['id']) for location in location_rows),
        )


def repair_title(item_name, detail_name, location_names):
    thing = item_name + (f' ({detail_name})' if detail_name else '')
    return f"{thing} — {', '.join(location_names)}"


def catalog_payload(db_path, club=None, include_inactive=False):
    initialize_repair_schema(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        active_sql = '' if include_inactive else 'WHERE items.active=1'
        items = conn.execute(
            f'''SELECT items.id, items.name, items.active,
                       details.id detail_id, details.name detail_name,
                       details.active detail_active
                FROM repair_item_types items
                LEFT JOIN repair_item_details details
                  ON details.item_type_id=items.id
                 {' ' if include_inactive else 'AND details.active=1'}
                {active_sql}
                ORDER BY items.sort_order, items.name,
                         details.sort_order, details.name'''
        ).fetchall()
        result = []
        by_id = {}
        for row in items:
            item = by_id.get(row['id'])
            if not item:
                item = {'id': row['id'], 'name': row['name'], 'active': bool(row['active']), 'details': []}
                by_id[row['id']] = item
                result.append(item)
            if row['detail_id'] is not None:
                item['details'].append({'id': row['detail_id'], 'name': row['detail_name'], 'active': bool(row['detail_active'])})
        locations = []
        if club:
            sql = '''SELECT id, club, name, kind, zone_number, active
                     FROM repair_locations WHERE club=?'''
            params = [club]
            if not include_inactive:
                sql += ' AND active=1'
            sql += ' ORDER BY sort_order, name'
            locations = [dict(row) for row in conn.execute(sql, params)]
            for location in locations:
                location['active'] = bool(location['active'])
        return {'items': result, 'locations': locations}
    finally:
        conn.close()


def _ensure_equipment_unit(conn, club, item_id, location_id, created_at=None):
    unit = conn.execute(
        '''SELECT id FROM equipment_units
           WHERE club=? AND item_type_id=? AND location_id=? AND active=1''',
        (club, item_id, location_id),
    ).fetchone()
    if unit:
        return unit['id'] if isinstance(unit, sqlite3.Row) else unit[0]
    generation = conn.execute(
        '''SELECT COALESCE(MAX(generation), 0) + 1
           FROM equipment_units
           WHERE club=? AND item_type_id=? AND location_id=?''',
        (club, item_id, location_id),
    ).fetchone()[0]
    event_at = created_at or datetime.now().isoformat(timespec='seconds')
    cursor = conn.execute(
        '''INSERT INTO equipment_units(
               club, location_id, item_type_id, generation, created_at
           ) VALUES (?, ?, ?, ?, ?)''',
        (club, location_id, item_id, generation, event_at),
    )
    unit_id = cursor.lastrowid
    conn.execute(
        '''INSERT INTO equipment_events(unit_id, event_type, event_at)
           VALUES (?, 'discovered', ?)''',
        (unit_id, event_at),
    )
    return unit_id


def _link_equipment_unit(conn, task_id, club, item_id, location_id, created_at=None):
    unit_id = _ensure_equipment_unit(
        conn, club, item_id, location_id, created_at=created_at,
    )
    conn.execute(
        '''INSERT OR IGNORE INTO equipment_unit_tasks(unit_id, task_id)
           VALUES (?, ?)''',
        (unit_id, task_id),
    )
    return unit_id


def _sync_equipment_units(conn):
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tasks'"
    ).fetchone():
        return
    repairs = conn.execute(
        '''SELECT tasks.ID task_id, tasks.club, tasks.dtrep,
                  cases.item_type_id, locations.location_id
           FROM repair_cases cases
           JOIN tasks ON tasks.ID=cases.task_id
           JOIN repair_case_locations locations ON locations.task_id=cases.task_id
           LEFT JOIN equipment_unit_tasks equipment
             ON equipment.task_id=tasks.ID
            AND equipment.unit_id IN (
                SELECT id FROM equipment_units
                WHERE location_id=locations.location_id
                  AND item_type_id=cases.item_type_id
            )
           WHERE equipment.task_id IS NULL
           ORDER BY date(tasks.dtrep), tasks.ID'''
    ).fetchall()
    for repair in repairs:
        _link_equipment_unit(
            conn,
            repair['task_id'],
            repair['club'],
            repair['item_type_id'],
            repair['location_id'],
            created_at=repair['dtrep'],
        )


def create_repair_case(conn, task_id, club, item_id, detail_id, location_ids):
    item = conn.execute(
        'SELECT id, name FROM repair_item_types WHERE id=? AND active=1',
        (item_id,),
    ).fetchone()
    if not item:
        raise ValueError('Выберите оборудование из списка')
    detail = None
    if detail_id:
        detail = conn.execute(
            '''SELECT id, name FROM repair_item_details
               WHERE id=? AND item_type_id=? AND active=1''',
            (detail_id, item_id),
        ).fetchone()
        if not detail:
            raise ValueError('Выберите корректное уточнение')
    location_ids = list(dict.fromkeys(int(value) for value in location_ids))
    if not location_ids:
        raise ValueError('Выберите хотя бы одно место')
    placeholders = ','.join('?' for _ in location_ids)
    locations = conn.execute(
        f'''SELECT id, name FROM repair_locations
            WHERE club=? AND active=1 AND id IN ({placeholders})
            ORDER BY sort_order, name''',
        (club, *location_ids),
    ).fetchall()
    if len(locations) != len(location_ids):
        raise ValueError('Выберите места, относящиеся к выбранному клубу')
    conn.execute(
        '''INSERT INTO repair_cases(task_id, item_type_id, detail_id)
           VALUES (?, ?, ?)''',
        (task_id, item_id, detail['id'] if detail else None),
    )
    conn.executemany(
        'INSERT INTO repair_case_locations(task_id, location_id) VALUES (?, ?)',
        ((task_id, location['id']) for location in locations),
    )
    for location in locations:
        _link_equipment_unit(conn, task_id, club, item_id, location['id'])
    conn.execute(
        '''INSERT INTO repair_events(task_id, event_type, event_at)
           VALUES (?, 'created', ?)''',
        (task_id, datetime.now().isoformat(timespec='seconds')),
    )
    return repair_title(
        item['name'], detail['name'] if detail else None,
        [location['name'] for location in locations],
    )


def repair_payload(conn, task_id):
    case = conn.execute(
        '''SELECT cases.task_id, cases.mapping_source,
                  items.id item_id, items.name item_name,
                  details.id detail_id, details.name detail_name
           FROM repair_cases cases
           JOIN repair_item_types items ON items.id=cases.item_type_id
           LEFT JOIN repair_item_details details ON details.id=cases.detail_id
           WHERE cases.task_id=?''',
        (task_id,),
    ).fetchone()
    if not case:
        return None
    locations = conn.execute(
        '''SELECT locations.id, locations.name
           FROM repair_case_locations links
           JOIN repair_locations locations ON locations.id=links.location_id
           WHERE links.task_id=? ORDER BY locations.sort_order, locations.name''',
        (task_id,),
    ).fetchall()
    units = conn.execute(
        '''SELECT units.id, units.generation, units.active,
                  locations.id location_id, locations.name location_name
           FROM equipment_unit_tasks links
           JOIN equipment_units units ON units.id=links.unit_id
           JOIN repair_locations locations ON locations.id=units.location_id
           WHERE links.task_id=?
           ORDER BY locations.sort_order, locations.name''',
        (task_id,),
    ).fetchall()
    history = conn.execute(
        '''SELECT DISTINCT tasks.ID, tasks.dtrep, tasks.title, tasks.status,
                          tasks.dtfb, tasks.feedback
           FROM equipment_unit_tasks current_unit
           JOIN equipment_unit_tasks history_unit
             ON history_unit.unit_id=current_unit.unit_id
           JOIN tasks ON tasks.ID=history_unit.task_id
           LEFT JOIN repair_migration_exclusions excluded
             ON excluded.task_id=tasks.ID
           WHERE current_unit.task_id=? AND excluded.task_id IS NULL
           ORDER BY date(tasks.dtrep) DESC, tasks.ID DESC''',
        (task_id,),
    ).fetchall()
    return {
        'item': {'id': case['item_id'], 'name': case['item_name']},
        'detail': ({'id': case['detail_id'], 'name': case['detail_name']} if case['detail_id'] else None),
        'locations': [dict(row) for row in locations],
        'units': [
            {
                'id': row['id'], 'generation': row['generation'],
                'active': bool(row['active']),
                'location_id': row['location_id'],
                'location_name': row['location_name'],
            }
            for row in units
        ],
        'mapping_source': case['mapping_source'],
        'history': [
            {
                'task_id': row['ID'], 'date': row['dtrep'],
                'title': row['title'], 'status': row['status'],
                'closed_at': row['dtfb'], 'feedback': row['feedback'],
                'events': [dict(event) for event in conn.execute(
                    '''SELECT event_type, event_at, message
                       FROM repair_events WHERE task_id=?
                       ORDER BY event_at, id''',
                    (row['ID'],),
                )],
            }
            for row in history
        ],
    }


def add_repair_event(conn, task_id, event_type, message=None):
    if not conn.execute('SELECT 1 FROM repair_cases WHERE task_id=?', (task_id,)).fetchone():
        return
    conn.execute(
        '''INSERT INTO repair_events(task_id, event_type, event_at, message)
           VALUES (?, ?, ?, ?)''',
        (task_id, event_type, datetime.now().isoformat(timespec='seconds'), message),
    )


def equipment_list_payload(db_path):
    initialize_repair_schema(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            '''SELECT units.id, units.club, units.generation,
                      items.id item_id, items.name item_name,
                      locations.id location_id, locations.name location_name,
                      locations.sort_order location_order,
                      COUNT(DISTINCT links.task_id) repair_count,
                      SUM(CASE WHEN tasks.status IN ('В работе', 'На проверке')
                               THEN 1 ELSE 0 END) open_count,
                      MAX(tasks.dtrep) last_repair
               FROM equipment_units units
               JOIN repair_item_types items ON items.id=units.item_type_id
               JOIN repair_locations locations ON locations.id=units.location_id
               LEFT JOIN equipment_unit_tasks links ON links.unit_id=units.id
               LEFT JOIN tasks ON tasks.ID=links.task_id
               WHERE units.active=1
               GROUP BY units.id
               ORDER BY units.club, locations.sort_order, locations.name,
                        items.sort_order, items.name'''
        ).fetchall()
        return {
            'units': [
                {
                    'id': row['id'], 'club': row['club'],
                    'generation': row['generation'],
                    'item': {'id': row['item_id'], 'name': row['item_name']},
                    'location': {
                        'id': row['location_id'], 'name': row['location_name'],
                    },
                    'repair_count': row['repair_count'],
                    'open_count': row['open_count'],
                    'last_repair': row['last_repair'],
                }
                for row in rows
            ],
        }
    finally:
        conn.close()


def equipment_payload(conn, unit_id):
    unit = conn.execute(
        '''SELECT units.id, units.club, units.generation, units.active,
                  units.created_at, units.retired_at, units.replaced_by_id,
                  units.location_id, locations.name location_name,
                  units.item_type_id, items.name item_name
           FROM equipment_units units
           JOIN repair_locations locations ON locations.id=units.location_id
           JOIN repair_item_types items ON items.id=units.item_type_id
           WHERE units.id=?''',
        (unit_id,),
    ).fetchone()
    if not unit:
        return None
    generations = conn.execute(
        '''SELECT id, generation, active, created_at, retired_at, replaced_by_id
           FROM equipment_units
           WHERE club=? AND location_id=? AND item_type_id=?
           ORDER BY generation DESC''',
        (unit['club'], unit['location_id'], unit['item_type_id']),
    ).fetchall()
    history = conn.execute(
        '''SELECT tasks.ID task_id, tasks.dtrep, tasks.dtfb, tasks.status,
                  tasks.title, tasks.desc description, tasks.feedback,
                  details.name detail_name, identity.id unit_id,
                  identity.generation,
                  (SELECT COUNT(*) FROM repair_case_locations places
                   WHERE places.task_id=tasks.ID) location_count
           FROM equipment_units identity
           JOIN equipment_unit_tasks links ON links.unit_id=identity.id
           JOIN tasks ON tasks.ID=links.task_id
           JOIN repair_cases cases ON cases.task_id=tasks.ID
           LEFT JOIN repair_item_details details ON details.id=cases.detail_id
           WHERE identity.club=? AND identity.location_id=?
             AND identity.item_type_id=?
           ORDER BY date(tasks.dtrep) DESC, tasks.ID DESC''',
        (unit['club'], unit['location_id'], unit['item_type_id']),
    ).fetchall()
    replacements = conn.execute(
        '''SELECT events.event_at, events.actor_login, events.actor_name,
                  events.message, units.generation,
                  replacement.generation replacement_generation
           FROM equipment_events events
           JOIN equipment_units units ON units.id=events.unit_id
           LEFT JOIN equipment_units replacement
             ON replacement.id=events.related_unit_id
           WHERE units.club=? AND units.location_id=?
             AND units.item_type_id=? AND events.event_type='replaced'
           ORDER BY events.event_at DESC, events.id DESC''',
        (unit['club'], unit['location_id'], unit['item_type_id']),
    ).fetchall()
    return {
        'id': unit['id'], 'club': unit['club'],
        'generation': unit['generation'], 'active': bool(unit['active']),
        'created_at': unit['created_at'], 'retired_at': unit['retired_at'],
        'replaced_by_id': unit['replaced_by_id'],
        'item': {'id': unit['item_type_id'], 'name': unit['item_name']},
        'location': {'id': unit['location_id'], 'name': unit['location_name']},
        'generations': [
            {
                'id': row['id'], 'generation': row['generation'],
                'active': bool(row['active']), 'created_at': row['created_at'],
                'retired_at': row['retired_at'],
                'replaced_by_id': row['replaced_by_id'],
            }
            for row in generations
        ],
        'history': [dict(row) for row in history],
        'replacements': [dict(row) for row in replacements],
        'open_tasks': [
            dict(row) for row in history
            if row['unit_id'] == unit['id']
            and row['status'] in {'В работе', 'На проверке'}
        ],
    }


def replace_equipment_unit(
    conn, unit_id, actor, message, close_multi_location_tasks,
    event_at, feedback_entry,
):
    unit = conn.execute(
        '''SELECT * FROM equipment_units WHERE id=? AND active=1''',
        (unit_id,),
    ).fetchone()
    if not unit:
        return None
    open_tasks = conn.execute(
        '''SELECT tasks.*,
                  (SELECT COUNT(*) FROM repair_case_locations locations
                   WHERE locations.task_id=tasks.ID) location_count
           FROM equipment_unit_tasks links
           JOIN tasks ON tasks.ID=links.task_id
           WHERE links.unit_id=?
             AND tasks.status IN ('В работе', 'На проверке')
           ORDER BY tasks.ID''',
        (unit_id,),
    ).fetchall()
    multi_location_tasks = [
        {'id': row['ID'], 'title': row['title'], 'location_count': row['location_count']}
        for row in open_tasks if row['location_count'] > 1
    ]
    if multi_location_tasks and not close_multi_location_tasks:
        return {
            'requires_confirmation': True,
            'multi_location_tasks': multi_location_tasks,
            'open_task_count': len(open_tasks),
        }

    conn.execute(
        '''UPDATE equipment_units SET active=0, retired_at=? WHERE id=?''',
        (event_at, unit_id),
    )
    cursor = conn.execute(
        '''INSERT INTO equipment_units(
               club, location_id, item_type_id, generation, created_at
           ) VALUES (?, ?, ?, ?, ?)''',
        (
            unit['club'], unit['location_id'], unit['item_type_id'],
            unit['generation'] + 1, event_at,
        ),
    )
    replacement_id = cursor.lastrowid
    conn.execute(
        'UPDATE equipment_units SET replaced_by_id=? WHERE id=?',
        (replacement_id, unit_id),
    )
    actor = actor or {}
    conn.execute(
        '''INSERT INTO equipment_events(
               unit_id, event_type, event_at, actor_login, actor_name,
               message, related_unit_id
           ) VALUES (?, 'replaced', ?, ?, ?, ?, ?)''',
        (
            unit_id, event_at, actor.get('login'), actor.get('name'),
            message or None, replacement_id,
        ),
    )
    conn.execute(
        '''INSERT INTO equipment_events(
               unit_id, event_type, event_at, actor_login, actor_name,
               message, related_unit_id
           ) VALUES (?, 'installed', ?, ?, ?, ?, ?)''',
        (
            replacement_id, event_at, actor.get('login'), actor.get('name'),
            message or None, unit_id,
        ),
    )

    closed_tasks = []
    for task in open_tasks:
        feedback = str(task['feedback'] or '').strip()
        feedback = f'{feedback}\n\n{feedback_entry}'.strip()
        conn.execute(
            '''UPDATE tasks SET status='Выполнено', feedback=?, dtfb=?
               WHERE ID=? AND status IN ('В работе', 'На проверке')''',
            (feedback, event_at[:10], task['ID']),
        )
        add_repair_event(
            conn, task['ID'], 'confirmed', message or 'Оборудование заменено на новое',
        )
        record_task_event(
            conn, task['ID'], 'confirmed', event_at=datetime.fromisoformat(event_at),
            actor=actor,
        )
        closed_tasks.append(dict(task))
    return {
        'requires_confirmation': False,
        'replacement_id': replacement_id,
        'closed_tasks': closed_tasks,
    }


def migration_review_payload(db_path):
    initialize_repair_schema(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            '''SELECT tasks.ID, tasks.dtrep, tasks.club, tasks.title, tasks.desc
               FROM tasks
               LEFT JOIN repair_cases cases ON cases.task_id=tasks.ID
               LEFT JOIN repair_migration_exclusions excluded ON excluded.task_id=tasks.ID
               WHERE lower(tasks.type)=lower('Ремонт')
                 AND cases.task_id IS NULL AND excluded.task_id IS NULL
               ORDER BY date(tasks.dtrep) DESC, tasks.ID DESC'''
        ).fetchall()
        counts = conn.execute(
            '''SELECT
                 (SELECT COUNT(*) FROM tasks WHERE lower(type)=lower('Ремонт')) total,
                 (SELECT COUNT(*) FROM repair_cases) mapped,
                 (SELECT COUNT(*) FROM repair_migration_exclusions WHERE reason='test') tests,
                 (SELECT COUNT(*) FROM repair_migration_exclusions WHERE reason='duplicate') duplicates'''
        ).fetchone()
        return {'summary': dict(counts), 'unmapped': [dict(row) for row in rows]}
    finally:
        conn.close()
