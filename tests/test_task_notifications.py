import unittest

from task_notifications import (
    GENERAL_TASK_TYPE,
    REPAIR_TASK_TYPE,
    created_task_notification,
    progress_task_notification,
)


class TaskNotificationsTest(unittest.TestCase):
    def test_general_notification_has_natural_heading_and_escapes_html(self):
        full, short, confirmation = created_task_notification(
            GENERAL_TASK_TYPE,
            'Клуб <1>',
            'Вопрос & идея',
            '<b>Описание</b>',
            actor={'name': 'Скрытый автор', 'login': '@hidden'},
        )

        self.assertIn('Новое общее обращение', full)
        self.assertIn('Клуб &lt;1&gt;', full)
        self.assertNotIn('Клуб:', full)
        self.assertNotIn('Тема:', full)
        self.assertNotIn('Скрытый автор', full)
        self.assertNotIn('@hidden', short)
        self.assertIn('📍 <b>Клуб &lt;1&gt;</b>', short)
        self.assertIn('💬 <b>Вопрос &amp; идея</b>', short)
        self.assertIn('&lt;b&gt;Описание&lt;/b&gt;', full)
        self.assertNotIn('проблема-', full.lower())
        self.assertIn('Новое общее обращение', short)
        self.assertEqual(confirmation, 'Готово, общее обращение добавлено!')

    def test_repair_notifications_use_repair_request_wording(self):
        full, short, confirmation = created_task_notification(
            REPAIR_TASK_TYPE,
            'Марьино',
            'Не работает шлем',
            'Нет изображения',
        )
        returned_full, returned_short = progress_task_notification(
            'returned',
            REPAIR_TASK_TYPE,
            'Марьино',
            'Не работает шлем',
            'Проблема осталась',
        )

        self.assertIn('Новая заявка на ремонт', full)
        self.assertIn('Новая заявка на ремонт', short)
        self.assertEqual(confirmation, 'Готово, заявка на ремонт добавлена!')
        self.assertIn('Заявка на ремонт возвращена в работу', returned_full)
        self.assertIn('Заявка на ремонт возвращена в работу', returned_short)
        self.assertIn('📍 <b>Марьино</b>', short)
        self.assertIn('🔧 <b>Не работает шлем</b>', short)
        self.assertNotIn('Нет изображения', short)

        repair_with_actor, repair_short, _ = created_task_notification(
            REPAIR_TASK_TYPE,
            'Марьино',
            'Не работает шлем',
            'Нет изображения',
            actor={'name': 'Иван', 'login': '@ivan'},
        )
        self.assertIn('Создал:</b> Иван (@ivan)', repair_with_actor)
        self.assertIn('Создал:</b> Иван (@ivan)', repair_short)

        completed_full, completed_short = progress_task_notification(
            'completed', REPAIR_TASK_TYPE, 'Марьино', 'Не работает шлем', '',
        )
        self.assertEqual(completed_full, completed_short)
        self.assertIn('Заявка на ремонт выполнена', completed_full)
        self.assertNotIn('Причина возврата', completed_full)

        solution_full, solution_short = progress_task_notification(
            'solution', REPAIR_TASK_TYPE, 'Марьино', 'Не работает шлем',
            'Переподключил питание',
            actor={'name': 'Алексей', 'login': '@alex'},
        )
        self.assertIn('Ответил:</b> Алексей (@alex)', solution_full)
        self.assertIn('Ответил:</b> Алексей (@alex)', solution_short)


if __name__ == '__main__':
    unittest.main()
