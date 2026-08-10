import unittest
from unittest.mock import patch

from club_config import select_question_set


class QuestionSetSelectionTest(unittest.TestCase):
    def test_checklist_is_derived_from_selected_questions(self):
        config = {
            'questions': {'open': [
                [{'text': 'question A', 'type': 'text', 'checklist': 'checklist A'}],
                [{'text': 'question B', 'type': 'text', 'checklist': 'checklist B'}],
            ]},
        }

        with patch('club_config.random.randrange', return_value=1):
            questions, checklist = select_question_set(config, 'open')

        self.assertEqual(questions[0]['text'], 'question B')
        self.assertEqual(checklist, ['checklist B'])

    def test_empty_question_checklist_is_not_rendered(self):
        config = {
            'questions': {'open': [[
                {'text': 'question A', 'type': 'text', 'checklist': ''},
                {'text': 'question B', 'type': 'text'},
            ]]},
        }

        with patch('club_config.random.randrange', return_value=0):
            _, checklist = select_question_set(config, 'open')

        self.assertEqual(checklist, [])


if __name__ == '__main__':
    unittest.main()
