import unittest

from club_config_sync import (
    ConfigValidationError,
    CHECKLISTS_SHEET,
    CLUB_HEADERS,
    CLUBS_SHEET,
    QUESTIONS_SHEET,
    VALIDATION_SHEET,
    build_config,
    checklists_to_values,
    clubs_to_values,
    count_config,
    questions_to_values,
    read_config,
    validate_stable_identity,
)


def sample_config():
    return {
        'Тестовый клуб': {
            'tag': '@employee',
            'acc_name': 'Тестовый клуб',
            'is_physical': True,
            'require_geo': True,
            'coords': {'lat': 55.7, 'lon': 37.6},
            'radius': 500,
            'schedule': {
                'auto_close_time': '05:00:00',
                'status_close_time': '23:00:00',
                'early_check_time': '22:30:00',
                'open': {'weekdays': '10:00:00', 'weekend': '09:00:00'},
                'open_strict': {'weekdays': '10:15:00', 'weekend': '09:15:00'},
            },
            'checklists': {
                '✅ Открыть смену': ['Включить ПК'],
                '🚫 Закрыть смену': ['Выключить ПК'],
            },
            'questions': {
                '✅ Открыть смену': [
                    [{'type': 'photo', 'text': 'Фото ПК'}],
                    [{'type': 'photo', 'text': 'Фото ПК'}],
                ],
                '🚫 Закрыть смену': [
                    [{'type': 'num', 'text': 'Сколько нала?'}],
                    [{'type': 'num', 'text': 'Сколько нала?'}],
                ],
            },
        },
        'Глобально': {
            'tag': '@owner, @manager',
            'acc_name': 'Глобально',
            'is_physical': False,
            'require_geo': False,
        },
    }


class FakeWorksheet:
    def __init__(self, title):
        self.title = title
        self.values = []

    def update_values(self, _cell, values, extend=False):
        self.values = values

    def get_all_values(self, **_kwargs):
        return self.values


class FakeSpreadsheet:
    def __init__(self):
        self.items = []

    def worksheets(self):
        return self.items

    def add_worksheet(self, title, rows, cols):
        worksheet = FakeWorksheet(title)
        self.items.append(worksheet)
        return worksheet


class ClubConfigSyncTest(unittest.TestCase):
    def sheet_values(self):
        current = sample_config()
        clubs, ids = clubs_to_values(current)
        questions = questions_to_values(current, ids)
        checklists = checklists_to_values(current, ids)
        return clubs, questions, checklists

    def test_exported_config_round_trips_and_common_questions_use_all(self):
        clubs, questions, checklists = self.sheet_values()

        self.assertTrue(any(row[3] == 'all' for row in questions[1:]))
        compiled = build_config(clubs, questions, checklists)

        self.assertEqual(list(compiled), ['Тестовый клуб', 'Глобально'])
        self.assertEqual(compiled['Тестовый клуб']['radius'], 500)
        self.assertEqual(count_config(compiled), (2, 4, 2))

    def test_first_sync_creates_and_immediately_reads_new_sheets(self):
        spreadsheet = FakeSpreadsheet()

        compiled, worksheets, created = read_config(spreadsheet, sample_config())

        self.assertEqual(
            set(created),
            {CLUBS_SHEET, QUESTIONS_SHEET, CHECKLISTS_SHEET, VALIDATION_SHEET},
        )
        self.assertEqual(set(worksheets), set(created))
        self.assertEqual(count_config(compiled), (2, 4, 2))

        compiled_again, _worksheets, created_again = read_config(spreadsheet, compiled)
        self.assertEqual(created_again, [])
        self.assertEqual(compiled_again, compiled)

    def test_duplicate_question_inside_variant_is_rejected(self):
        clubs, questions, checklists = self.sheet_values()
        duplicate = list(questions[1])
        duplicate[0] = 'duplicate_id'
        duplicate[4] = 99
        questions.append(duplicate)

        with self.assertRaisesRegex(ConfigValidationError, 'продублирован'):
            build_config(clubs, questions, checklists)

    def test_invalid_sheet_does_not_silently_turn_variant_into_zero(self):
        clubs, questions, checklists = self.sheet_values()
        questions[1][3] = 'ошибка'

        with self.assertRaisesRegex(ConfigValidationError, 'Variants'):
            build_config(clubs, questions, checklists)

    def test_fractional_integer_is_rejected(self):
        clubs, questions, checklists = self.sheet_values()
        clubs[1][CLUB_HEADERS.index('SortOrder')] = '1.5'

        with self.assertRaisesRegex(ConfigValidationError, 'целым числом'):
            build_config(clubs, questions, checklists)

    def test_duplicate_checklist_item_is_rejected(self):
        clubs, questions, checklists = self.sheet_values()
        duplicate = list(checklists[1])
        duplicate[0] = 'duplicate_checklist_id'
        duplicate[3] = 99
        checklists.append(duplicate)

        with self.assertRaisesRegex(ConfigValidationError, 'продублирован'):
            build_config(clubs, questions, checklists)

    def test_tag_whitespace_is_removed(self):
        clubs, questions, checklists = self.sheet_values()
        clubs[1][CLUB_HEADERS.index('Tag')] = '@employee\n'

        compiled = build_config(clubs, questions, checklists)

        self.assertEqual(compiled['Тестовый клуб']['tag'], '@employee')

    def test_existing_club_identity_cannot_be_changed(self):
        clubs, questions, checklists = self.sheet_values()
        current = build_config(clubs, questions, checklists)
        clubs[1][1] = 'Новое имя'
        renamed = build_config(clubs, questions, checklists)

        with self.assertRaisesRegex(ConfigValidationError, 'историей'):
            validate_stable_identity(current, renamed)

    def test_nonphysical_club_can_keep_schedule_and_questions(self):
        current = sample_config()
        source = current['Тестовый клуб']
        current['Коллцентр'] = {
            'tag': '@callcenter',
            'acc_name': 'Коллцентр',
            'is_physical': False,
            'require_geo': False,
            'schedule': source['schedule'],
            'questions': source['questions'],
        }
        clubs, ids = clubs_to_values(current)
        compiled = build_config(
            clubs,
            questions_to_values(current, ids),
            checklists_to_values(current, ids),
        )

        self.assertIn('schedule', compiled['Коллцентр'])
        self.assertIn('questions', compiled['Коллцентр'])


if __name__ == '__main__':
    unittest.main()
