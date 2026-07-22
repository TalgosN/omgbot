import copy
import hashlib
import json
import os
import random
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
CLUBS_PATH = BASE_DIR / 'data' / 'clubs.json'
CLUBS_BACKUP_PATH = BASE_DIR / 'data' / 'clubs.json.bak'

_lock = threading.RLock()
_clubs = None
_status = {
    'loaded_at': None,
    'version': None,
    'source': None,
}


def _version(data):
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()[:12]


def _validate_clubs(clubs):
    if not isinstance(clubs, dict) or not clubs:
        raise ValueError('clubs.json должен содержать непустой объект клубов')
    used_ids = set()
    for name, info in clubs.items():
        if not isinstance(info, dict):
            raise ValueError(f'clubs.json: конфигурация клуба {name!r} должна быть объектом')
        club_id = str(info.get('_config_id') or '').strip()
        if not club_id:
            raise ValueError(f'clubs.json: у клуба {name!r} отсутствует _config_id')
        if club_id in used_ids:
            raise ValueError(f'clubs.json: _config_id {club_id!r} используется повторно')
        used_ids.add(club_id)
        if not str(info.get('shift_name') or '').strip():
            raise ValueError(f'clubs.json: у клуба {name!r} отсутствует shift_name')
        if not isinstance(info.get('schedule_visible'), bool):
            raise ValueError(f'clubs.json: у клуба {name!r} schedule_visible должен быть boolean')
        if info['schedule_visible'] and not str(info.get('schedule_emoji') or '').strip():
            raise ValueError(f'clubs.json: у клуба {name!r} отсутствует schedule_emoji')
    return clubs


def _write_atomic(data, path, backup=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f'{path.name}.tmp')
    try:
        with temp_path.open('w', encoding='utf-8', newline='\n') as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
            file.write('\n')
            file.flush()
            os.fsync(file.fileno())
        if backup and path.exists():
            shutil.copy2(path, CLUBS_BACKUP_PATH)
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _load_file(path=None):
    path = Path(path or CLUBS_PATH)
    with path.open('r', encoding='utf-8') as file:
        clubs = json.load(file)
    return _validate_clubs(clubs)


def reload_clubs(source='disk'):
    global _clubs
    clubs = _load_file()
    with _lock:
        _clubs = clubs
        _status.update({
            'loaded_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'version': _version(clubs),
            'source': source,
        })
        return copy.deepcopy(_clubs)


def get_clubs():
    global _clubs
    with _lock:
        if _clubs is None:
            return reload_clubs()
        return copy.deepcopy(_clubs)


def get_clublist():
    return tuple(
        name for name, info in get_clubs().items()
        if info.get('is_physical') is True
    )


def get_clublist_task():
    return tuple(get_clubs())


def get_schedule_locations():
    result = []
    for name, info in get_clubs().items():
        if not info['schedule_visible']:
            continue
        result.append({
            'name': name,
            'source_name': info['shift_name'],
            'emoji': info['schedule_emoji'],
        })
    return result


def select_question_set(club_config, action):
    question_variants = club_config.get('questions', {}).get(action, [[]])
    variant_index = random.randrange(len(question_variants))
    questions = question_variants[variant_index]

    checklist_variants = club_config.get('checklists', {}).get(action, [])
    if checklist_variants and isinstance(checklist_variants[0], list):
        checklist = checklist_variants[variant_index]
    else:
        checklist = checklist_variants
    return questions, checklist


def save_clubs(clubs, source='google'):
    global _clubs
    snapshot = _validate_clubs(copy.deepcopy(clubs))
    with _lock:
        _write_atomic(snapshot, CLUBS_PATH, backup=True)
        _clubs = snapshot
        _status.update({
            'loaded_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'version': _version(snapshot),
            'source': source,
        })
        return copy.deepcopy(_clubs)


def get_club_config_status():
    with _lock:
        if _clubs is None:
            reload_clubs()
        return dict(_status)
