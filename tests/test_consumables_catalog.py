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

    def test_legacy_bot_consumables_buttons_are_hidden(self):
        for buttons in constants.funclist.values():
            self.assertNotIn('📦 Расходники', buttons)
        self.assertNotIn('📦 Расходники (Админ)', constants.admin_funclist)
        self.assertNotIn(
            '📦 Расходники (Админ)', constants.owner_admin_funclist,
        )


if __name__ == '__main__':
    unittest.main()
