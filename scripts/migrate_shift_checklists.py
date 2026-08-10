import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from club_config import get_clubs, save_clubs


def migrate():
    clubs = get_clubs()
    changed = False
    removed_items = 0

    for info in clubs.values():
        for variants in info.get('checklists', {}).values():
            removed_items += sum(len(items) for items in variants if isinstance(items, list))
        if 'checklists' in info:
            info.pop('checklists', None)
            changed = True

        for variants in info.get('questions', {}).values():
            for questions in variants:
                for question in questions:
                    if isinstance(question, dict) and 'checklist' not in question:
                        question['checklist'] = ''
                        changed = True

    if changed:
        save_clubs(clubs, source='shift_checklist_migration')

    return changed, removed_items


if __name__ == '__main__':
    migrated, removed = migrate()
    print(f'Shift checklist migration changed={migrated}, removed_items={removed}')
