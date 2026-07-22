import copy
import hashlib
import json
import os
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
CLUBS_PATH = BASE_DIR / 'data' / 'clubs.json'
CLUBS_BACKUP_PATH = BASE_DIR / 'data' / 'clubs.json.bak'
DEFAULT_SCHEDULE_EMOJIS = ('🟢', '🟣', '🟠', '🔴', '🟡', '🔈')

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
    if not isinstance(clubs, dict) or not clubs:
        raise ValueError('clubs.json должен содержать непустой объект клубов')

    if 'КЦ' in clubs and 'Коллцентр' not in clubs:
        callcenter = clubs.pop('КЦ')
        if callcenter.get('acc_name') == 'КЦ':
            callcenter['acc_name'] = 'Коллцентр'
        clubs['Коллцентр'] = callcenter
        _write_atomic(clubs, path)
    return clubs


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
        visible = info.get('schedule_visible')
        if visible is None:
            visible = bool(info.get('schedule') or info.get('is_physical'))
        if not visible:
            continue
        index = len(result)
        result.append({
            'name': name,
            'source_name': info.get('shift_name') or name,
            'emoji': info.get('schedule_emoji') or DEFAULT_SCHEDULE_EMOJIS[
                index % len(DEFAULT_SCHEDULE_EMOJIS)
            ],
        })
    return result


def save_clubs(clubs, source='google'):
    global _clubs
    if not isinstance(clubs, dict) or not clubs:
        raise ValueError('Нельзя сохранить пустую конфигурацию клубов')
    snapshot = copy.deepcopy(clubs)
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
