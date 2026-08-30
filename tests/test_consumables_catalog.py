import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import constants
import consumables_catalog


CLUBS = {
    'Дмитровка': {'require_geo': True},
    'Марьино': {'require_geo': True},
    'Коллцентр': {'require_geo': False},
}


class ConsumablesCatalogTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / 'consumables.db')
        conn = sqlite3.connect(self.db_path)
        conn.executescript(
            '''CREATE TABLE consumables (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   club TEXT, name TEXT NOT NULL,
                   quantity INTEGER DEFAULT 0, min_limit INTEGER DEFAULT 5
               );
               CREATE TABLE consumables_history (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   item_id INTEGER, club TEXT, name TEXT, user_name TEXT,
                   old_qty INTEGER, new_qty INTEGER, updated_at TIMESTAMP
               );
               INSERT INTO consumables (club, name, quantity, min_limit)
               VALUES ('Дмитровка', 'Burn Манго', 7, 3),
                      ('Марьино', 'Burn манго', 2, 3),
                      ('Дмитровка', 'Туалетная бумага', 8, 4);'''
        )
        conn.commit()
        conn.close()
        self.clubs_patch = patch.object(
            consumables_catalog, 'get_clubs', return_value=CLUBS,
        )
        self.clubs_patch.start()
        consumables_catalog.initialize_consumables_schema(self.db_path)

    def tearDown(self):
        self.clubs_patch.stop()
        self.temp_dir.cleanup()

    def test_migration_builds_global_products_and_categories(self):
        conn = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(
                conn.execute('SELECT COUNT(*) FROM consumables').fetchone()[0],
                3,
            )
            self.assertEqual(
                conn.execute(
                    'SELECT COUNT(*) FROM consumable_products'
                ).fetchone()[0],
                2,
            )
            self.assertEqual(
                conn.execute(
                    'SELECT COUNT(*) FROM consumable_categories'
                ).fetchone()[0],
                7,
            )
            product_ids = conn.execute(
                '''SELECT product_id FROM consumables
                   WHERE lower(name) LIKE 'burn%' ORDER BY id'''
            ).fetchall()
            self.assertEqual(product_ids[0][0], product_ids[1][0])
        finally:
            conn.close()

        payload = consumables_catalog.inventory_payload(
            self.db_path, 'Марьино',
        )
        self.assertEqual(payload['clubs'], ['Дмитровка', 'Марьино'])
        self.assertEqual(payload['summary']['low'], 1)
        self.assertEqual(payload['items'][0]['category_slug'], 'energy')

    def test_archived_item_is_offered_for_restore_instead_of_duplicated(self):
        consumables_catalog.set_item_active(
            self.db_path, 1, False, '@manager', 'временно не закупаем',
        )
        conn = sqlite3.connect(self.db_path)
        try:
            category_id = conn.execute(
                "SELECT id FROM consumable_categories WHERE slug='energy'"
            ).fetchone()[0]
        finally:
            conn.close()
        result = consumables_catalog.add_inventory_item(
            self.db_path, 'Дмитровка', ' burn  манго ', category_id,
            0, 5, '@manager',
        )
        self.assertEqual(result['conflict'], 'archived')
        self.assertEqual(result['item_id'], 1)

        consumables_catalog.set_item_active(
            self.db_path, result['item_id'], True, '@manager',
        )
        payload = consumables_catalog.inventory_payload(
            self.db_path, 'Дмитровка',
        )
        self.assertEqual(len(payload['items']), 2)
        self.assertTrue(next(item for item in payload['items'] if item['id'] == 1)['is_active'])

    def test_quantity_history_and_low_transition_are_preserved(self):
        first = consumables_catalog.update_quantity(
            self.db_path, 1, 3, '@employee',
        )
        second = consumables_catalog.update_quantity(
            self.db_path, 1, 2, '@employee',
        )
        self.assertTrue(first['became_low'])
        self.assertFalse(second['became_low'])
        history = consumables_catalog.item_history(self.db_path, 1)
        quantity_events = [
            event for event in history['events']
            if event['event_type'] == 'quantity'
        ]
        self.assertEqual(len(quantity_events), 2)
        self.assertEqual(quantity_events[0]['details'], '3 → 2')

    def test_product_photo_is_shared_between_clubs(self):
        consumables_catalog.save_product_photo(
            self.db_path, 1, b'jpeg-content', 'image/jpeg', '@manager',
        )
        product_id = consumables_catalog.inventory_payload(
            self.db_path, 'Марьино',
        )['items'][0]['product_id']
        photo = consumables_catalog.product_photo(self.db_path, product_id)
        self.assertEqual(photo[0], b'jpeg-content')
        self.assertEqual(photo[1], 'image/jpeg')

    def test_manager_can_add_category_without_duplicate_name(self):
        created = consumables_catalog.add_category(
            self.db_path, 'Снеки', '🍪', '@manager',
        )
        self.assertEqual(created['name'], 'Снеки')
        payload = consumables_catalog.inventory_payload(self.db_path)
        self.assertIn('Снеки', [
            category['name'] for category in payload['categories']
        ])
        with self.assertRaisesRegex(ValueError, 'уже есть'):
            consumables_catalog.add_category(
                self.db_path, 'снеки', '🍫', '@manager',
            )

    def test_fresh_migration_merges_semantic_aliases(self):
        alias_db = str(Path(self.temp_dir.name) / 'aliases.db')
        conn = sqlite3.connect(alias_db)
        conn.executescript(
            '''CREATE TABLE consumables (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   club TEXT, name TEXT NOT NULL,
                   quantity INTEGER DEFAULT 0, min_limit INTEGER DEFAULT 5
               );
               INSERT INTO consumables (club, name, quantity, min_limit)
               VALUES ('Дмитровка', 'Крем-мыло', 4, 2),
                      ('Марьино', 'Средство для рук', 7, 3);'''
        )
        conn.commit()
        conn.close()

        consumables_catalog.initialize_consumables_schema(alias_db)

        conn = sqlite3.connect(alias_db)
        try:
            products = conn.execute(
                '''SELECT id, name FROM consumable_products
                   WHERE normalized_name=?''',
                (consumables_catalog.normalize_product_name('Жидкое мыло'),),
            ).fetchall()
            inventory = conn.execute(
                '''SELECT id, name, quantity, product_id FROM consumables
                   ORDER BY id'''
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(len(products), 1)
        self.assertEqual(products[0][1], 'Жидкое мыло')
        self.assertEqual([row[0] for row in inventory], [1, 2])
        self.assertEqual([row[1] for row in inventory], ['Жидкое мыло'] * 2)
        self.assertEqual([row[2] for row in inventory], [4, 7])
        self.assertEqual({row[3] for row in inventory}, {products[0][0]})

    def test_existing_catalog_aliases_are_merged_without_losing_history(self):
        conn = sqlite3.connect(self.db_path)
        category_id = conn.execute(
            "SELECT id FROM consumable_categories WHERE slug='energy'"
        ).fetchone()[0]
        first_product = conn.execute(
            '''INSERT INTO consumable_products
                   (name, normalized_name, category_id, created_at, created_by)
               VALUES (?, ?, ?, '2026-08-01 10:00:00', 'migration')''',
            (
                'Adrenaline Energy Power синий',
                consumables_catalog.normalize_product_name(
                    'Adrenaline Energy Power синий'
                ),
                category_id,
            ),
        ).lastrowid
        second_product = conn.execute(
            '''INSERT INTO consumable_products
                   (name, normalized_name, category_id, photo, photo_mime,
                    created_at, created_by)
               VALUES (?, ?, ?, ?, 'image/jpeg',
                       '2026-08-01 10:00:00', 'migration')''',
            (
                'Adrenaline Синий',
                consumables_catalog.normalize_product_name('Adrenaline Синий'),
                category_id,
                b'product-photo',
            ),
        ).lastrowid
        first_item = conn.execute(
            '''INSERT INTO consumables
                   (club, name, quantity, min_limit, product_id, is_active)
               VALUES ('Дмитровка', 'Adrenaline Energy Power синий',
                       3, 2, ?, 1)''',
            (first_product,),
        ).lastrowid
        second_item = conn.execute(
            '''INSERT INTO consumables
                   (club, name, quantity, min_limit, product_id, is_active)
               VALUES ('Марьино', 'Adrenaline Синий', 6, 2, ?, 1)''',
            (second_product,),
        ).lastrowid
        conn.execute(
            '''INSERT INTO consumables_history
                   (item_id, club, name, user_name, old_qty, new_qty,
                    updated_at)
               VALUES (?, 'Марьино', 'Adrenaline Синий', '@admin', 5, 6,
                       '2026-08-02 10:00:00')''',
            (second_item,),
        )
        conn.execute(
            '''INSERT INTO consumable_events
                   (item_id, product_id, club, event_type, actor, created_at)
               VALUES (?, ?, 'Марьино', 'quantity', '@admin',
                       '2026-08-02 10:00:00')''',
            (second_item, second_product),
        )
        conn.commit()
        conn.close()

        consumables_catalog._initialized_paths.discard(
            str(Path(self.db_path).resolve())
        )
        consumables_catalog.initialize_consumables_schema(self.db_path)

        conn = sqlite3.connect(self.db_path)
        try:
            products = conn.execute(
                '''SELECT id, name, photo FROM consumable_products
                   WHERE normalized_name=?''',
                (consumables_catalog.normalize_product_name(
                    'Adrenaline Energy Power синий'
                ),),
            ).fetchall()
            inventory = conn.execute(
                '''SELECT id, name, quantity, product_id FROM consumables
                   WHERE id IN (?, ?) ORDER BY id''',
                (first_item, second_item),
            ).fetchall()
            history_count = conn.execute(
                'SELECT COUNT(*) FROM consumables_history WHERE item_id=?',
                (second_item,),
            ).fetchone()[0]
            event_product = conn.execute(
                '''SELECT product_id FROM consumable_events
                   WHERE item_id=?''',
                (second_item,),
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(len(products), 1)
        self.assertEqual(products[0][1], 'Adrenaline Energy Power синий')
        self.assertEqual(products[0][2], b'product-photo')
        self.assertEqual([row[0] for row in inventory], [first_item, second_item])
        self.assertEqual([row[2] for row in inventory], [3, 6])
        self.assertEqual({row[3] for row in inventory}, {products[0][0]})
        self.assertEqual(history_count, 1)
        self.assertEqual(event_product, products[0][0])

    def test_legacy_bot_consumables_buttons_are_hidden(self):
        for buttons in constants.funclist.values():
            self.assertNotIn('📦 Расходники', buttons)
        self.assertNotIn('📦 Расходники (Админ)', constants.admin_funclist)
        self.assertNotIn(
            '📦 Расходники (Админ)', constants.owner_admin_funclist,
        )


if __name__ == '__main__':
    unittest.main()
