import os
import re
import sqlite3
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

from club_config import get_clubs


CATEGORY_SEEDS = (
    ('energy', '⚡', 'Энергетики'),
    ('soda', '🥤', 'Газировка'),
    ('juice', '🧃', 'Соки'),
    ('water', '💧', 'Вода и стаканы'),
    ('cleaning', '🧼', 'Уборка и гигиена'),
    ('certificates', '🎁', 'Сертификаты и полиграфия'),
    ('household', '🧰', 'Хозяйственные'),
)
PRODUCT_ALIAS_GROUPS = (
    ('Adrenaline Energy Power синий', (
        'Adrenaline Energy Power синий', 'Adrenaline Синий',
    )),
    ('Adrenaline Zero Sugar', (
        'Adrenaline Zero Sugar', 'Adrenaline Белый',
    )),
    ('Adrenaline Ягодный', (
        'Adrenaline Ягодный', 'Adrenaline Ягоды',
    )),
    ('Сок Любимый: Вишня-Черешня', (
        'Сок вишня-черешня',
        'Сок любимый: Вишня-Черешня',
    )),
    ('Сок Любимый: Земляничный', (
        'Сок земляничный',
        'Сок любимый: Земляничный',
    )),
    ('Сок Любимый: Яблочный', (
        'Сок яблочный',
        'Сок любимый: Яблочный',
    )),
    ('Салфетки влажные (в пачках)', (
        'Салфетки влажные(в пачках)',
        'Салфетки(влажные в пачках)',
        'Салфетки(влажные)',
    )),
    ('Салфетки сухие (в пачках)', (
        'Салфетки сухие(в пачках)',
        'Салфетки(сухие в пачках)',
        'Салфетки(сухие)',
    )),
    ('Средство для стекол', (
        'Жидкость для стекол',
        'Средство для стекла',
        'Средство для стекол',
    )),
    ('Сертификаты ДР', (
        'Сертификат ДР', 'Сертификаты ДР',
    )),
    ('Скотч малярный', (
        'Малярная лента(скотч)', 'Скотч малярный',
    )),
    ('Средство для унитаза', (
        'Доместос', 'Средство для унитаза',
    )),
    ('Жидкое мыло', (
        'Крем-мыло', 'Средство для рук', 'Жидкое мыло',
    )),
)
_initialized_paths = set()
_schema_lock = threading.Lock()


def normalize_product_name(value):
    normalized = str(value or '').strip().lower().replace('ё', 'е')
    normalized = re.sub(r'[^a-zа-я0-9]+', ' ', normalized)
    return ' '.join(normalized.split())


_PRODUCT_ALIAS_LOOKUP = {
    normalize_product_name(alias): canonical
    for canonical, aliases in PRODUCT_ALIAS_GROUPS
    for alias in aliases
}


def canonical_product_identity(value):
    raw_name = str(value or '').strip()
    normalized = normalize_product_name(raw_name)
    canonical = _PRODUCT_ALIAS_LOOKUP.get(normalized, raw_name)
    return canonical, normalize_product_name(canonical)


def _category_slug(name):
    value = normalize_product_name(name)
    if any(word in value for word in ('adrenaline', 'burn', 'монстр', 'monster')):
        return 'energy'
    if value.startswith('сок ') or 'сок любимый' in value:
        return 'juice'
    if any(word in value for word in (
        'evervess', 'frustile', 'добрый',
    )):
        return 'soda'
    if 'палпи' in value:
        return 'juice'
    if any(word in value for word in ('вода', 'стаканчик')):
        return 'water'
    if any(word in value for word in (
        'сертификат', 'конверт', 'коробк', 'флаер',
        'чековая лента',
    )):
        return 'certificates'
    if any(word in value for word in (
        'салфет', 'мусорн', 'мыл', 'доместос', 'бумаг',
        'жидкость', 'средство',
    )):
        return 'cleaning'
    return 'household'


def _now():
    return datetime.now(ZoneInfo('Europe/Moscow')).strftime('%Y-%m-%d %H:%M:%S')


def allowed_consumable_clubs():
    return [
        name for name, config in get_clubs().items()
        if config.get('require_geo') is True
    ]


def _columns(conn, table):
    return {row[1] for row in conn.execute(f'PRAGMA table_info({table})')}


def _merge_catalog_aliases(conn):
    for canonical_name, aliases in PRODUCT_ALIAS_GROUPS:
        canonical_normalized = normalize_product_name(canonical_name)
        alias_names = {
            normalize_product_name(alias)
            for alias in aliases
        }
        placeholders = ','.join('?' for _ in alias_names)
        products = conn.execute(
            f'''SELECT * FROM consumable_products
                WHERE normalized_name IN ({placeholders})
                ORDER BY CASE WHEN normalized_name=? THEN 0 ELSE 1 END, id''',
            (*alias_names, canonical_normalized),
        ).fetchall()
        if not products:
            continue

        target = products[0]
        for source in products[1:]:
            overlapping_clubs = conn.execute(
                '''SELECT source.club
                   FROM consumables source
                   JOIN consumables target ON target.club=source.club
                   WHERE source.product_id=? AND target.product_id=?''',
                (source['id'], target['id']),
            ).fetchall()
            if overlapping_clubs:
                clubs = ', '.join(row['club'] for row in overlapping_clubs)
                raise RuntimeError(
                    f'Cannot merge duplicate consumables in the same club: '
                    f'{canonical_name} ({clubs})'
                )
            if target['photo'] is None and source['photo'] is not None:
                conn.execute(
                    '''UPDATE consumable_products
                       SET photo=?, photo_mime=?, photo_updated_at=?
                       WHERE id=?''',
                    (
                        source['photo'], source['photo_mime'],
                        source['photo_updated_at'], target['id'],
                    ),
                )
                target = conn.execute(
                    'SELECT * FROM consumable_products WHERE id=?',
                    (target['id'],),
                ).fetchone()
            conn.execute(
                '''UPDATE consumables SET product_id=?, name=?
                   WHERE product_id=?''',
                (target['id'], canonical_name, source['id']),
            )
            conn.execute(
                'UPDATE consumable_events SET product_id=? WHERE product_id=?',
                (target['id'], source['id']),
            )
            conn.execute(
                'DELETE FROM consumable_products WHERE id=?',
                (source['id'],),
            )

        conn.execute(
            '''UPDATE consumable_products
               SET name=?, normalized_name=? WHERE id=?''',
            (canonical_name, canonical_normalized, target['id']),
        )
        conn.execute(
            'UPDATE consumables SET name=? WHERE product_id=?',
            (canonical_name, target['id']),
        )


def initialize_consumables_schema(db_path):
    schema_key = os.path.abspath(str(db_path))
    with _schema_lock:
        if schema_key in _initialized_paths:
            return
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            conn.execute(
                '''CREATE TABLE IF NOT EXISTS consumables (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       club TEXT,
                       name TEXT NOT NULL,
                       quantity INTEGER DEFAULT 0,
                       min_limit INTEGER DEFAULT 5
                   )'''
            )
            conn.execute(
                '''CREATE TABLE IF NOT EXISTS consumables_history (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       item_id INTEGER,
                       club TEXT,
                       name TEXT,
                       user_name TEXT,
                       old_qty INTEGER,
                       new_qty INTEGER,
                       updated_at TIMESTAMP
                   )'''
            )
            conn.execute(
                '''CREATE TABLE IF NOT EXISTS consumable_categories (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       slug TEXT NOT NULL UNIQUE,
                       emoji TEXT NOT NULL,
                       name TEXT NOT NULL,
                       sort_order INTEGER NOT NULL DEFAULT 0,
                       is_active INTEGER NOT NULL DEFAULT 1
                   )'''
            )
            for order, (slug, emoji, name) in enumerate(CATEGORY_SEEDS, 1):
                conn.execute(
                    '''INSERT INTO consumable_categories
                           (slug, emoji, name, sort_order, is_active)
                       VALUES (?, ?, ?, ?, 1)
                       ON CONFLICT(slug) DO UPDATE SET
                           emoji=excluded.emoji,
                           name=excluded.name,
                           sort_order=excluded.sort_order''',
                    (slug, emoji, name, order),
                )
            conn.execute(
                '''CREATE TABLE IF NOT EXISTS consumable_products (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       name TEXT NOT NULL,
                       normalized_name TEXT NOT NULL UNIQUE,
                       category_id INTEGER NOT NULL,
                       photo BLOB,
                       photo_mime TEXT,
                       photo_updated_at TEXT,
                       category_source TEXT NOT NULL DEFAULT 'auto',
                       created_at TEXT NOT NULL,
                       created_by TEXT
                   )'''
            )
            product_columns = _columns(conn, 'consumable_products')
            if 'category_source' not in product_columns:
                conn.execute(
                    '''ALTER TABLE consumable_products ADD COLUMN
                       category_source TEXT NOT NULL DEFAULT 'auto' '''
                )
            conn.execute(
                '''CREATE TABLE IF NOT EXISTS consumable_events (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       item_id INTEGER NOT NULL,
                       product_id INTEGER,
                       club TEXT NOT NULL,
                       event_type TEXT NOT NULL,
                       actor TEXT,
                       details TEXT,
                       created_at TEXT NOT NULL
                   )'''
            )
            inventory_columns = _columns(conn, 'consumables')
            additions = {
                'product_id': 'INTEGER',
                'is_active': 'INTEGER NOT NULL DEFAULT 1',
                'archived_at': 'TEXT',
                'archived_by': 'TEXT',
                'archive_reason': 'TEXT',
            }
            for column, definition in additions.items():
                if column not in inventory_columns:
                    conn.execute(
                        f'ALTER TABLE consumables ADD COLUMN {column} {definition}'
                    )

            category_ids = {
                row['slug']: row['id']
                for row in conn.execute(
                    'SELECT id, slug FROM consumable_categories'
                )
            }
            rows = conn.execute(
                '''SELECT id, name, product_id FROM consumables
                   ORDER BY id'''
            ).fetchall()
            for row in rows:
                if row['product_id']:
                    continue
                product_name, normalized = canonical_product_identity(row['name'])
                if not normalized:
                    normalized = f'item-{row["id"]}'
                product = conn.execute(
                    '''SELECT id FROM consumable_products
                       WHERE normalized_name=?''',
                    (normalized,),
                ).fetchone()
                if product:
                    product_id = product['id']
                else:
                    cursor = conn.execute(
                        '''INSERT INTO consumable_products
                               (name, normalized_name, category_id,
                                category_source, created_at, created_by)
                           VALUES (?, ?, ?, 'auto', ?, 'migration')''',
                        (
                            product_name, normalized,
                            category_ids[_category_slug(product_name)], _now(),
                        ),
                    )
                    product_id = cursor.lastrowid
                conn.execute(
                    'UPDATE consumables SET product_id=?, name=? WHERE id=?',
                    (product_id, product_name, row['id']),
                )
            _merge_catalog_aliases(conn)
            for product in conn.execute(
                '''SELECT id, name FROM consumable_products
                   WHERE category_source='auto' '''
            ).fetchall():
                conn.execute(
                    '''UPDATE consumable_products SET category_id=?
                       WHERE id=?''',
                    (category_ids[_category_slug(product['name'])], product['id']),
                )
            conn.execute(
                '''CREATE UNIQUE INDEX IF NOT EXISTS
                       idx_consumables_club_product
                   ON consumables (club, product_id)'''
            )
            conn.execute(
                '''CREATE INDEX IF NOT EXISTS idx_consumables_active_club
                   ON consumables (club, is_active)'''
            )
            conn.execute(
                '''CREATE INDEX IF NOT EXISTS idx_consumable_events_item
                   ON consumable_events (item_id, created_at DESC)'''
            )
    finally:
        conn.close()
    with _schema_lock:
        _initialized_paths.add(schema_key)


def _category_payload(conn):
    return [dict(row) for row in conn.execute(
        '''SELECT id, slug, emoji, name
           FROM consumable_categories
           WHERE is_active=1 ORDER BY sort_order, id'''
    )]


def add_category(db_path, name, emoji, _actor):
    name = str(name or '').strip()
    emoji = str(emoji or '').strip()[:8] or '📦'
    normalized = normalize_product_name(name)
    if len(name) < 2 or not normalized:
        raise ValueError('Укажите название категории')
    initialize_consumables_schema(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            duplicate = conn.execute(
                '''SELECT id FROM consumable_categories
                   WHERE lower(name)=lower(?)''',
                (name,),
            ).fetchone()
            if duplicate:
                raise ValueError('Такая категория уже есть')
            maximum = conn.execute(
                'SELECT COALESCE(MAX(sort_order), 0) FROM consumable_categories'
            ).fetchone()[0]
            slug = f'custom:{normalized}'
            cursor = conn.execute(
                '''INSERT INTO consumable_categories
                       (slug, emoji, name, sort_order, is_active)
                   VALUES (?, ?, ?, ?, 1)''',
                (slug, emoji, name, int(maximum) + 1),
            )
        return {
            'id': cursor.lastrowid,
            'slug': slug,
            'emoji': emoji,
            'name': name,
        }
    except sqlite3.IntegrityError as error:
        raise ValueError('Такая категория уже есть') from error
    finally:
        conn.close()


def inventory_payload(db_path, club=None, include_archived=False):
    initialize_consumables_schema(db_path)
    clubs = allowed_consumable_clubs()
    selected = str(club or '').strip()
    if selected not in clubs:
        selected = clubs[0] if clubs else ''
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        active_filter = '' if include_archived else 'AND c.is_active=1'
        rows = conn.execute(
            f'''SELECT c.id, c.club, c.quantity, c.min_limit, c.is_active,
                       c.archived_at, c.archive_reason, p.id AS product_id,
                       p.name, p.photo IS NOT NULL AS has_photo,
                       p.photo_updated_at, cat.id AS category_id,
                       cat.slug AS category_slug, cat.emoji AS category_emoji,
                       cat.name AS category_name,
                       (SELECT MAX(h.updated_at) FROM consumables_history h
                        WHERE h.item_id=c.id) AS last_updated_at
                FROM consumables c
                JOIN consumable_products p ON p.id=c.product_id
                JOIN consumable_categories cat ON cat.id=p.category_id
                WHERE c.club=? {active_filter}
                ORDER BY (c.quantity <= c.min_limit) DESC,
                         cat.sort_order, lower(p.name)''',
            (selected,),
        ).fetchall() if selected else []
        items = []
        for row in rows:
            item = dict(row)
            item['is_active'] = bool(item['is_active'])
            item['has_photo'] = bool(item['has_photo'])
            item['is_low'] = (
                item['is_active']
                and int(item['quantity'] or 0) <= int(item['min_limit'] or 0)
            )
            items.append(item)
        active_items = [item for item in items if item['is_active']]
        return {
            'clubs': clubs,
            'selected_club': selected,
            'categories': _category_payload(conn),
            'items': items,
            'summary': {
                'active': len(active_items),
                'low': sum(item['is_low'] for item in active_items),
                'archived': sum(not item['is_active'] for item in items),
            },
        }
    finally:
        conn.close()


def update_quantity(db_path, item_id, quantity, actor):
    quantity = int(quantity)
    if quantity < 0:
        raise ValueError('Остаток не может быть отрицательным')
    initialize_consumables_schema(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            item = conn.execute(
                '''SELECT c.*, p.name AS product_name
                   FROM consumables c
                   JOIN consumable_products p ON p.id=c.product_id
                   WHERE c.id=?''',
                (item_id,),
            ).fetchone()
            if not item:
                raise ValueError('Расходник не найден')
            if not item['is_active']:
                raise ValueError('Сначала восстановите расходник из архива')
            old_quantity = int(item['quantity'] or 0)
            conn.execute(
                'UPDATE consumables SET quantity=? WHERE id=?',
                (quantity, item_id),
            )
            conn.execute(
                '''INSERT INTO consumables_history
                       (item_id, club, name, user_name, old_qty, new_qty,
                        updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)''',
                (
                    item_id, item['club'], item['product_name'], actor,
                    old_quantity, quantity, _now(),
                ),
            )
        return {
            'id': item_id,
            'club': item['club'],
            'name': item['product_name'],
            'old_quantity': old_quantity,
            'quantity': quantity,
            'min_limit': int(item['min_limit'] or 0),
            'became_low': (
                old_quantity > int(item['min_limit'] or 0)
                and quantity <= int(item['min_limit'] or 0)
            ),
        }
    finally:
        conn.close()


def _record_event(conn, item, event_type, actor, details=None):
    conn.execute(
        '''INSERT INTO consumable_events
               (item_id, product_id, club, event_type, actor, details,
                created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)''',
        (
            item['id'], item['product_id'], item['club'], event_type,
            actor, details, _now(),
        ),
    )


def add_inventory_item(
    db_path, club, name, category_id, quantity, min_limit, actor,
    photo=None, photo_mime=None,
):
    clubs = allowed_consumable_clubs()
    if club not in clubs:
        raise ValueError('Выберите клуб из списка')
    raw_name = str(name or '').strip()
    if len(raw_name) < 2 or not normalize_product_name(raw_name):
        raise ValueError('Укажите название товара')
    name, normalized = canonical_product_identity(raw_name)
    quantity = int(quantity)
    min_limit = int(min_limit)
    if quantity < 0 or min_limit < 0:
        raise ValueError('Остаток и минимум не могут быть отрицательными')
    initialize_consumables_schema(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            category = conn.execute(
                '''SELECT id FROM consumable_categories
                   WHERE id=? AND is_active=1''',
                (int(category_id),),
            ).fetchone()
            if not category:
                raise ValueError('Категория не найдена')
            product = conn.execute(
                '''SELECT * FROM consumable_products
                   WHERE normalized_name=?''',
                (normalized,),
            ).fetchone()
            if product:
                product_id = product['id']
                inventory_name = product['name']
                existing = conn.execute(
                    '''SELECT * FROM consumables
                       WHERE club=? AND product_id=?''',
                    (club, product_id),
                ).fetchone()
                if existing:
                    return {
                        'conflict': 'archived' if not existing['is_active'] else 'active',
                        'item_id': existing['id'],
                        'name': product['name'],
                    }
                if photo:
                    conn.execute(
                        '''UPDATE consumable_products
                           SET photo=?, photo_mime=?, photo_updated_at=?
                           WHERE id=?''',
                        (photo, photo_mime, _now(), product_id),
                    )
            else:
                cursor = conn.execute(
                    '''INSERT INTO consumable_products
                           (name, normalized_name, category_id, category_source, photo,
                            photo_mime, photo_updated_at, created_at, created_by)
                       VALUES (?, ?, ?, 'manual', ?, ?, ?, ?, ?)''',
                    (
                        name, normalized, int(category_id), photo, photo_mime,
                        _now() if photo else None, _now(), actor,
                    ),
                )
                product_id = cursor.lastrowid
                inventory_name = name
            cursor = conn.execute(
                '''INSERT INTO consumables
                       (club, name, quantity, min_limit, product_id, is_active)
                   VALUES (?, ?, ?, ?, ?, 1)''',
                (club, inventory_name, quantity, min_limit, product_id),
            )
            item = {
                'id': cursor.lastrowid,
                'product_id': product_id,
                'club': club,
            }
            _record_event(conn, item, 'created', actor)
            if quantity:
                conn.execute(
                    '''INSERT INTO consumables_history
                           (item_id, club, name, user_name, old_qty, new_qty,
                            updated_at)
                       VALUES (?, ?, ?, ?, 0, ?, ?)''',
                    (item['id'], club, inventory_name, actor, quantity, _now()),
                )
        return {'created': True, 'item_id': item['id']}
    finally:
        conn.close()


def update_item_settings(db_path, item_id, min_limit, category_id, actor):
    min_limit = int(min_limit)
    if min_limit < 0:
        raise ValueError('Минимум не может быть отрицательным')
    initialize_consumables_schema(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            item = conn.execute(
                'SELECT * FROM consumables WHERE id=?', (item_id,),
            ).fetchone()
            if not item:
                raise ValueError('Расходник не найден')
            category = conn.execute(
                '''SELECT id FROM consumable_categories
                   WHERE id=? AND is_active=1''',
                (int(category_id),),
            ).fetchone()
            if not category:
                raise ValueError('Категория не найдена')
            conn.execute(
                'UPDATE consumables SET min_limit=? WHERE id=?',
                (min_limit, item_id),
            )
            conn.execute(
                '''UPDATE consumable_products
                   SET category_id=?, category_source='manual' WHERE id=?''',
                (int(category_id), item['product_id']),
            )
            _record_event(
                conn, item, 'settings', actor,
                f'min_limit={min_limit};category_id={int(category_id)}',
            )
        return {'updated': True}
    finally:
        conn.close()


def set_item_active(db_path, item_id, active, actor, reason=''):
    initialize_consumables_schema(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            item = conn.execute(
                'SELECT * FROM consumables WHERE id=?', (item_id,),
            ).fetchone()
            if not item:
                raise ValueError('Расходник не найден')
            if active:
                conn.execute(
                    '''UPDATE consumables SET is_active=1, archived_at=NULL,
                           archived_by=NULL, archive_reason=NULL WHERE id=?''',
                    (item_id,),
                )
                _record_event(conn, item, 'restored', actor)
            else:
                conn.execute(
                    '''UPDATE consumables SET is_active=0, archived_at=?,
                           archived_by=?, archive_reason=? WHERE id=?''',
                    (_now(), actor, str(reason or '').strip()[:300], item_id),
                )
                _record_event(
                    conn, item, 'archived', actor,
                    str(reason or '').strip()[:300] or None,
                )
        return {'active': bool(active)}
    finally:
        conn.close()


def save_product_photo(db_path, item_id, photo, photo_mime, actor):
    initialize_consumables_schema(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            item = conn.execute(
                'SELECT * FROM consumables WHERE id=?', (item_id,),
            ).fetchone()
            if not item:
                raise ValueError('Расходник не найден')
            conn.execute(
                '''UPDATE consumable_products
                   SET photo=?, photo_mime=?, photo_updated_at=? WHERE id=?''',
                (photo, photo_mime, _now(), item['product_id']),
            )
            _record_event(conn, item, 'photo', actor)
        return {'saved': True, 'product_id': item['product_id']}
    finally:
        conn.close()


def product_photo(db_path, product_id):
    initialize_consumables_schema(db_path)
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            '''SELECT photo, photo_mime FROM consumable_products
               WHERE id=? AND photo IS NOT NULL''',
            (product_id,),
        ).fetchone()
    finally:
        conn.close()


def item_history(db_path, item_id, limit=30):
    initialize_consumables_schema(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        item = conn.execute(
            '''SELECT c.id, c.club, p.name FROM consumables c
               JOIN consumable_products p ON p.id=c.product_id
               WHERE c.id=?''',
            (item_id,),
        ).fetchone()
        if not item:
            raise ValueError('Расходник не найден')
        quantity_rows = conn.execute(
            '''SELECT id AS event_id, 'quantity' AS event_type, user_name AS actor,
                      updated_at AS created_at,
                      old_qty || ' → ' || new_qty AS details
               FROM consumables_history WHERE item_id=?''',
            (item_id,),
        ).fetchall()
        event_rows = conn.execute(
            '''SELECT id AS event_id, event_type, actor, created_at, details
               FROM consumable_events WHERE item_id=?''',
            (item_id,),
        ).fetchall()
        events = [dict(row) for row in (*quantity_rows, *event_rows)]
        events.sort(
            key=lambda row: (
                row.get('created_at') or '', int(row.get('event_id') or 0),
            ),
            reverse=True,
        )
        for event in events:
            event.pop('event_id', None)
        return {
            'item': dict(item),
            'events': events[:max(1, min(int(limit), 100))],
        }
    finally:
        conn.close()
