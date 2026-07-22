import unittest

from club_config_sync import (
    CHECKLIST_HEADERS,
    CHECKLISTS_SHEET,
    CLUB_HEADERS,
    CLUBS_SHEET,
    ConfigValidationError,
    QUESTION_HEADERS,
    QUESTIONS_SHEET,
    VALIDATION_SHEET,
    build_config,
    count_config,
    read_config,
    validate_stable_identity,
)


def make_row(headers, **values):
    return [values.get(header, '') for header in headers]


def sheet_values():
    clubs = [
        CLUB_HEADERS,
        make_row(
            CLUB_HEADERS,
            ClubID='club_test',
            Name='Тестовый клуб',
            AccountName='Тестовый клуб',
            ShiftName='Тестовый клуб OMG',
            ScheduleVisible=True,
            ScheduleEmoji='🎮',
            Tag='@employee',
            Physical=True,
            GeoRequired=True,
            Latitude=55.7,
            Longitude=37.6,
            Radius=500,
            AutoCloseTime='05:00:00',
            StatusCloseTime='23:00:00',
            EarlyCheckTime='22:30:00',
            OpenWeekdays='10:00:00',
            OpenWeekend='09:00:00',
            StrictWeekdays='10:15:00',
            StrictWeekend='09:15:00',
            QuestionVariants=2,
            Active=True,
            SortOrder=1,
        ),
        make_row(
            CLUB_HEADERS,
            ClubID='club_global',
            Name='Глобально',
            AccountName='Глобально',
            ShiftName='Глобально',
            ScheduleVisible=False,
            Tag='@owner, @manager',
            Physical=False,
            GeoRequired=False,
            QuestionVariants=0,
            Active=True,
            SortOrder=2,
        ),
    ]
    questions = [
        QUESTION_HEADERS,
        make_row(
            QUESTION_HEADERS,
            QuestionID='q_open',
            ClubID='club_test',
            Action='open',
            Variants='all',
            Order=1,
            Type='photo',
            Question='Фото ПК',
            Active=True,
        ),
        make_row(
            QUESTION_HEADERS,
            QuestionID='q_close',
            ClubID='club_test',
            Action='close',
            Variants='all',
            Order=1,
            Type='num',
            Question='Сколько нала?',
            Active=True,
        ),
    ]
    checklists = [
        CHECKLIST_HEADERS,
        make_row(
            CHECKLIST_HEADERS,
            ChecklistID='c_open',
            ClubID='club_test',
            Action='open',
            Order=1,
            Text='Включить ПК',
            Active=True,
        ),
        make_row(
            CHECKLIST_HEADERS,
            ChecklistID='c_close',
            ClubID='club_test',
            Action='close',
            Order=1,
            Text='Выключить ПК',
            Active=True,
        ),
    ]
    return clubs, questions, checklists


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
        clubs, questions, checklists = values
        by_title = {
            CLUBS_SHEET: clubs,
            QUESTIONS_SHEET: questions,
            CHECKLISTS_SHEET: checklists,
            VALIDATION_SHEET: [['Параметр', 'Значение']],
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

    def test_config_compiles_shared_questions(self):
        clubs, questions, checklists = self.sheet_values()
        compiled = build_config(clubs, questions, checklists)

        self.assertEqual(list(compiled), ['Тестовый клуб', 'Глобально'])
        self.assertEqual(compiled['Тестовый клуб']['radius'], 500)
        self.assertEqual(compiled['Тестовый клуб']['shift_name'], 'Тестовый клуб OMG')
        self.assertEqual(count_config(compiled), (2, 4, 2))

    def test_sync_reads_existing_sheets_without_creating_anything(self):
        values = self.sheet_values()
        spreadsheet = FakeSpreadsheet(values)

        compiled, worksheets = read_config(spreadsheet, {})

        self.assertEqual(
            set(worksheets),
            {CLUBS_SHEET, QUESTIONS_SHEET, CHECKLISTS_SHEET, VALIDATION_SHEET},
        )
        self.assertEqual(count_config(compiled), (2, 4, 2))

    def test_missing_required_sheet_is_rejected(self):
        spreadsheet = FakeSpreadsheet(self.sheet_values(), missing=(QUESTIONS_SHEET,))

        with self.assertRaisesRegex(ConfigValidationError, 'Config Questions'):
            read_config(spreadsheet, {})

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
        questions[1][QUESTION_HEADERS.index('Variants')] = 'ошибка'

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

    def test_visible_club_requires_explicit_schedule_emoji(self):
        clubs, questions, checklists = self.sheet_values()
        clubs[1][CLUB_HEADERS.index('ScheduleEmoji')] = ''

        with self.assertRaisesRegex(ConfigValidationError, 'ScheduleEmoji'):
            build_config(clubs, questions, checklists)

    def test_existing_club_identity_cannot_be_changed(self):
        clubs, questions, checklists = self.sheet_values()
        current = build_config(clubs, questions, checklists)
        clubs[1][CLUB_HEADERS.index('Name')] = 'Новое имя'
        renamed = build_config(clubs, questions, checklists)

        with self.assertRaisesRegex(ConfigValidationError, 'историей'):
            validate_stable_identity(current, renamed)

    def test_nonphysical_club_can_keep_schedule_and_questions(self):
        clubs, questions, checklists = self.sheet_values()
        callcenter = list(clubs[1])
        callcenter[CLUB_HEADERS.index('ClubID')] = 'club_callcenter'
        callcenter[CLUB_HEADERS.index('Name')] = 'Коллцентр'
        callcenter[CLUB_HEADERS.index('AccountName')] = 'Коллцентр'
        callcenter[CLUB_HEADERS.index('ShiftName')] = 'Коллцентр'
        callcenter[CLUB_HEADERS.index('Physical')] = False
        callcenter[CLUB_HEADERS.index('GeoRequired')] = False
        callcenter[CLUB_HEADERS.index('Latitude')] = ''
        callcenter[CLUB_HEADERS.index('Longitude')] = ''
        callcenter[CLUB_HEADERS.index('Radius')] = ''
        callcenter[CLUB_HEADERS.index('SortOrder')] = 3
        clubs.append(callcenter)

        for source in questions[1:3]:
            question = list(source)
            question[QUESTION_HEADERS.index('QuestionID')] += '_callcenter'
            question[QUESTION_HEADERS.index('ClubID')] = 'club_callcenter'
            questions.append(question)

        compiled = build_config(clubs, questions, checklists)

        self.assertIn('schedule', compiled['Коллцентр'])
        self.assertIn('questions', compiled['Коллцентр'])


if __name__ == '__main__':
    unittest.main()
