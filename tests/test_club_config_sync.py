import unittest

from club_config_sync import (
    CLUB_HEADERS,
    CLUBS_SHEET,
    INSTRUCTIONS_SHEET,
    OPEN_ACTION,
    QUESTION_HEADERS,
    QUESTIONS_SHEET,
    REQUIRED_SHEETS,
    SCHEDULE_HEADERS,
    SCHEDULE_SHEET,
    SYSTEM_HEADERS,
    SYSTEM_SHEET,
    VALIDATION_SHEET,
    ConfigValidationError,
    build_config,
    count_config,
    read_config,
    validate_stable_identity,
)


def make_row(headers, **values):
    return [values.get(header, '') for header in headers]


def sheet_values():
    system = [
        SYSTEM_HEADERS,
        make_row(
            SYSTEM_HEADERS,
            ClubID='club_test',
            Клуб='Тестовый клуб',
            **{
                'Название для сообщений': 'Тестовый клуб',
                'Название в OMG Shift': 'Тестовый клуб OMG',
                'Физический клуб': True,
                'Проверять геолокацию': True,
                'Широта': 55.7,
                'Долгота': 37.6,
                'Радиус': 500,
                'Автозакрытие': '05:00:00',
                'Количество наборов': 2,
                'Эмодзи': '🎮',
                'Активен': True,
                'Порядок': 1,
            },
        ),
        make_row(
            SYSTEM_HEADERS,
            ClubID='club_global',
            Клуб='Глобально',
            **{
                'Название для сообщений': 'Глобально',
                'Название в OMG Shift': 'Глобально',
                'Физический клуб': False,
                'Проверять геолокацию': False,
                'Количество наборов': 0,
                'Активен': True,
                'Порядок': 2,
            },
        ),
    ]
    clubs = [
        CLUB_HEADERS,
        make_row(
            CLUB_HEADERS,
            Клуб='Тестовый клуб',
            **{'Telegram-теги': '@employee', 'Показывать в расписании': True},
        ),
        make_row(
            CLUB_HEADERS,
            Клуб='Глобально',
            **{'Telegram-теги': '@owner, @manager', 'Показывать в расписании': False},
        ),
    ]
    schedules = [
        SCHEDULE_HEADERS,
        make_row(
            SCHEDULE_HEADERS,
            Клуб='Тестовый клуб',
            **{
                'Контроль закрытия': '23:00',
                'Ранняя проверка': '22:30',
                'Открытие в будни': '10:00',
                'Открытие в выходные': '09:00',
                'Строгое время в будни': '10:15',
                'Строгое время в выходные': '09:15',
            },
        ),
        make_row(SCHEDULE_HEADERS, Клуб='Глобально'),
    ]
    questions = [
        QUESTION_HEADERS,
        make_row(
            QUESTION_HEADERS,
            Клуб='Тестовый клуб',
            Действие='Открытие',
            Набор='Все наборы',
            Номер=1,
            **{
                'Пункт чек-листа': 'Включить ПК',
                'Формат ответа': 'Фото',
                'Вопрос сотруднику': 'Фото ПК',
                'Используется': True,
            },
        ),
        make_row(
            QUESTION_HEADERS,
            Клуб='Тестовый клуб',
            Действие='Открытие',
            Набор='Все наборы',
            Номер=2,
            **{
                'Пункт чек-листа': 'Проверить кассу',
                'Используется': True,
            },
        ),
        make_row(
            QUESTION_HEADERS,
            Клуб='Тестовый клуб',
            Действие='Закрытие',
            Набор='Все наборы',
            Номер=1,
            **{
                'Пункт чек-листа': 'Выключить ПК',
                'Формат ответа': 'Число',
                'Вопрос сотруднику': 'Сколько нала?',
                'Используется': True,
            },
        ),
    ]
    return system, clubs, schedules, questions


class FakeWorksheet:
    def __init__(self, title, values):
        self.title = title
        self.values = values

    def update_values(self, _cell, values, extend=False):
        self.values = values

    def get_all_values(self, **_kwargs):
        return self.values


class FakeSpreadsheet:
    def __init__(self, values, missing=()):
        system, clubs, schedules, questions = values
        by_title = {
            INSTRUCTIONS_SHEET: [['Инструкция']],
            CLUBS_SHEET: clubs,
            SCHEDULE_SHEET: schedules,
            QUESTIONS_SHEET: questions,
            VALIDATION_SHEET: [['Параметр', 'Значение']],
            SYSTEM_SHEET: system,
        }
        self.items = [
            FakeWorksheet(title, rows)
            for title, rows in by_title.items()
            if title not in missing
        ]

    def worksheets(self):
        return self.items


class ClubConfigSyncTest(unittest.TestCase):
    def sheet_values(self):
        return sheet_values()

    def test_config_compiles_questions_and_matching_checklists(self):
        compiled = build_config(*self.sheet_values())
        club = compiled['Тестовый клуб']

        self.assertEqual(list(compiled), ['Тестовый клуб', 'Глобально'])
        self.assertEqual(club['radius'], 500)
        self.assertEqual(club['shift_name'], 'Тестовый клуб OMG')
        self.assertEqual(
            club['checklists'][OPEN_ACTION],
            [['Включить ПК', 'Проверить кассу']] * 2,
        )
        self.assertEqual(count_config(compiled), (2, 4, 6))

    def test_sync_reads_only_existing_required_sheets(self):
        values = self.sheet_values()
        values[1].append(['', '', False])
        values[3].append(['', '', '', '', '', '', '', False])
        compiled, worksheets = read_config(FakeSpreadsheet(values), {})

        self.assertEqual(set(worksheets), set(REQUIRED_SHEETS))
        self.assertEqual(count_config(compiled), (2, 4, 6))

    def test_missing_required_sheet_is_rejected(self):
        spreadsheet = FakeSpreadsheet(self.sheet_values(), missing=(QUESTIONS_SHEET,))

        with self.assertRaisesRegex(ConfigValidationError, 'Вопросы смены'):
            read_config(spreadsheet, {})

    def test_duplicate_question_inside_variant_is_rejected(self):
        system, clubs, schedules, questions = self.sheet_values()
        duplicate = list(questions[1])
        duplicate[QUESTION_HEADERS.index('Набор')] = 'A'
        duplicate[QUESTION_HEADERS.index('Номер')] = 99
        questions.append(duplicate)

        with self.assertRaisesRegex(ConfigValidationError, 'продублирован'):
            build_config(system, clubs, schedules, questions)

    def test_invalid_variant_is_rejected(self):
        system, clubs, schedules, questions = self.sheet_values()
        questions[1][QUESTION_HEADERS.index('Набор')] = 'ошибка'

        with self.assertRaisesRegex(ConfigValidationError, 'Набор'):
            build_config(system, clubs, schedules, questions)

    def test_fractional_integer_is_rejected(self):
        system, clubs, schedules, questions = self.sheet_values()
        system[1][SYSTEM_HEADERS.index('Порядок')] = '1.5'

        with self.assertRaisesRegex(ConfigValidationError, 'целым числом'):
            build_config(system, clubs, schedules, questions)

    def test_google_numeric_time_is_supported(self):
        system, clubs, schedules, questions = self.sheet_values()
        system[1][SYSTEM_HEADERS.index('Автозакрытие')] = 5 / 24

        compiled = build_config(system, clubs, schedules, questions)

        self.assertEqual(
            compiled['Тестовый клуб']['schedule']['auto_close_time'],
            '05:00:00',
        )

    def test_duplicate_checklist_items_are_deduplicated(self):
        system, clubs, schedules, questions = self.sheet_values()
        duplicate = list(questions[2])
        duplicate[QUESTION_HEADERS.index('Номер')] = 99
        questions.append(duplicate)

        compiled = build_config(system, clubs, schedules, questions)

        self.assertEqual(
            compiled['Тестовый клуб']['checklists'][OPEN_ACTION][0],
            ['Включить ПК', 'Проверить кассу'],
        )

    def test_tag_whitespace_is_removed(self):
        system, clubs, schedules, questions = self.sheet_values()
        clubs[1][CLUB_HEADERS.index('Telegram-теги')] = '@employee\n'

        compiled = build_config(system, clubs, schedules, questions)

        self.assertEqual(compiled['Тестовый клуб']['tag'], '@employee')

    def test_visible_club_requires_system_schedule_emoji(self):
        system, clubs, schedules, questions = self.sheet_values()
        system[1][SYSTEM_HEADERS.index('Эмодзи')] = ''

        with self.assertRaisesRegex(ConfigValidationError, 'эмодзи'):
            build_config(system, clubs, schedules, questions)

    def test_existing_club_identity_cannot_be_changed(self):
        system, clubs, schedules, questions = self.sheet_values()
        current = build_config(system, clubs, schedules, questions)
        system[1][SYSTEM_HEADERS.index('Клуб')] = 'Новое имя'
        clubs[1][CLUB_HEADERS.index('Клуб')] = 'Новое имя'
        schedules[1][SCHEDULE_HEADERS.index('Клуб')] = 'Новое имя'
        for question in questions[1:]:
            question[QUESTION_HEADERS.index('Клуб')] = 'Новое имя'
        renamed = build_config(system, clubs, schedules, questions)

        with self.assertRaisesRegex(ConfigValidationError, 'историей'):
            validate_stable_identity(current, renamed)

    def test_checklist_only_row_must_not_have_answer_type(self):
        system, clubs, schedules, questions = self.sheet_values()
        questions[2][QUESTION_HEADERS.index('Формат ответа')] = 'Текст'

        with self.assertRaisesRegex(ConfigValidationError, 'без вопроса'):
            build_config(system, clubs, schedules, questions)


if __name__ == '__main__':
    unittest.main()
