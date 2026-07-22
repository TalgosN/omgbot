import unittest
from unittest.mock import patch

from club_config import select_question_set


class QuestionSetSelectionTest(unittest.TestCase):
    def test_questions_and_checklist_use_same_random_set(self):
        config = {
            'questions': {'open': [['question A'], ['question B']]},
            'checklists': {'open': [['checklist A'], ['checklist B']]},
        }

        with patch('club_config.random.randrange', return_value=1):
            questions, checklist = select_question_set(config, 'open')

        self.assertEqual(questions, ['question B'])
        self.assertEqual(checklist, ['checklist B'])

    def test_legacy_common_checklist_still_works(self):
        config = {
            'questions': {'open': [['question A'], ['question B']]},
            'checklists': {'open': ['common checklist']},
        }

        with patch('club_config.random.randrange', return_value=0):
            _, checklist = select_question_set(config, 'open')

        self.assertEqual(checklist, ['common checklist'])


if __name__ == '__main__':
    unittest.main()
