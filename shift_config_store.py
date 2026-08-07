import copy
import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

from club_config import get_clubs, save_clubs


OPEN_ACTION = '✅ Открыть смену'
CLOSE_ACTION = '🚫 Закрыть смену'
ACTIONS = {'open': OPEN_ACTION, 'close': CLOSE_ACTION}
QUESTION_TYPES = {'text', 'photo', 'num'}
MAX_VARIANTS = 26
MAX_ITEMS = 100
MAX_TEXT_LENGTH = 1000

_lock = threading.RLock()


def _is_editable(info):
    return info.get('is_physical') is True or bool(info.get('questions'))


def _snapshot(clubs):
    return {
        str(info['_config_id']): {
            'club': name,
            'questions': copy.deepcopy(info.get('questions', {})),
            'checklists': copy.deepcopy(info.get('checklists', {})),
        }
        for name, info in clubs.items()
        if _is_editable(info)
    }


def _hash(snapshot):
    raw = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]


def _connect(db_path):
    connection = sqlite3.connect(db_path, timeout=30)
    connection.row_factory = sqlite3.Row
    return connection


@contextmanager
def _database(db_path):
    connection = _connect(db_path)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def _initialize(connection):
    connection.execute(
        '''
        CREATE TABLE IF NOT EXISTS shift_config_versions (
            ID INTEGER PRIMARY KEY AUTOINCREMENT,
            version_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            actor_login TEXT NOT NULL,
            action TEXT NOT NULL,
            snapshot_json TEXT NOT NULL
        )
        '''
    )
    connection.execute(
        'CREATE INDEX IF NOT EXISTS idx_shift_config_versions_created '
        'ON shift_config_versions(created_at DESC, ID DESC)'
    )


def _record(connection, snapshot, actor_login, action):
    version_hash = _hash(snapshot)
    connection.execute(
        '''
        INSERT INTO shift_config_versions (
            version_hash, created_at, actor_login, action, snapshot_json
        ) VALUES (?, ?, ?, ?, ?)
        ''',
        (
            version_hash,
            datetime.now(timezone.utc).isoformat(timespec='seconds'),
            str(actor_login or 'system'),
            action,
            json.dumps(snapshot, ensure_ascii=False, separators=(',', ':')),
        ),
    )
    return version_hash


def _ensure_current_version(connection, clubs):
    snapshot = _snapshot(clubs)
    version_hash = _hash(snapshot)
    exists = connection.execute(
        'SELECT 1 FROM shift_config_versions WHERE version_hash=? LIMIT 1',
        (version_hash,),
    ).fetchone()
    if not exists:
        _record(connection, snapshot, 'system', 'initial_import')
    return snapshot, version_hash


def _question(value, club, action, variant, index):
    if not isinstance(value, dict):
        raise ValueError(f'{club}, {action}, набор {variant}: вопрос {index} задан неверно')
    text = str(value.get('text') or '').strip()
    question_type = str(value.get('type') or '').strip()
    if not text:
        raise ValueError(f'{club}, {action}, набор {variant}: пустой вопрос {index}')
    if len(text) > MAX_TEXT_LENGTH:
        raise ValueError(f'{club}, {action}, набор {variant}: вопрос {index} длиннее {MAX_TEXT_LENGTH} символов')
    if question_type not in QUESTION_TYPES:
        raise ValueError(f'{club}, {action}, набор {variant}: неверный формат ответа')
    return {'text': text, 'type': question_type}


def _checklist_item(value, club, action, variant, index):
    text = str(value or '').strip()
    if not text:
        raise ValueError(f'{club}, {action}, набор {variant}: пустой пункт чек-листа {index}')
    if len(text) > MAX_TEXT_LENGTH:
        raise ValueError(f'{club}, {action}, набор {variant}: пункт {index} длиннее {MAX_TEXT_LENGTH} символов')
    return text


def _compile_payload(payload, current_clubs):
    raw_clubs = payload.get('clubs') if isinstance(payload, dict) else None
    if not isinstance(raw_clubs, list):
        raise ValueError('Передайте список клубов')
    editable = {
        str(info['_config_id']): (name, info)
        for name, info in current_clubs.items()
        if _is_editable(info)
    }
    received = set()
    result = copy.deepcopy(current_clubs)
    for raw_club in raw_clubs:
        if not isinstance(raw_club, dict):
            raise ValueError('Клуб задан неверно')
        club_id = str(raw_club.get('id') or '').strip()
        if club_id not in editable or club_id in received:
            raise ValueError('Найден неизвестный или повторяющийся клуб')
        received.add(club_id)
        club_name, _info = editable[club_id]
        actions = raw_club.get('actions')
        if not isinstance(actions, dict):
            raise ValueError(f'{club_name}: нет сценариев')
        questions = {}
        checklists = {}
        for action_key, action_name in ACTIONS.items():
            variants = actions.get(action_key)
            if not isinstance(variants, list) or not 1 <= len(variants) <= MAX_VARIANTS:
                raise ValueError(f'{club_name}: для «{action_name}» нужно от 1 до {MAX_VARIANTS} наборов')
            question_variants = []
            checklist_variants = []
            for variant_index, raw_variant in enumerate(variants, 1):
                if not isinstance(raw_variant, dict):
                    raise ValueError(f'{club_name}: набор {variant_index} задан неверно')
                raw_questions = raw_variant.get('questions')
                raw_checklist = raw_variant.get('checklist', [])
                if not isinstance(raw_questions, list) or not raw_questions:
                    raise ValueError(f'{club_name}, {action_name}, набор {variant_index}: добавьте хотя бы один вопрос')
                if not isinstance(raw_checklist, list):
                    raise ValueError(f'{club_name}, {action_name}, набор {variant_index}: чек-лист задан неверно')
                if len(raw_questions) > MAX_ITEMS or len(raw_checklist) > MAX_ITEMS:
                    raise ValueError(f'{club_name}, {action_name}: в наборе может быть не более {MAX_ITEMS} строк')
                question_variants.append([
                    _question(item, club_name, action_name, variant_index, index)
                    for index, item in enumerate(raw_questions, 1)
                ])
                checklist_variants.append([
                    _checklist_item(item, club_name, action_name, variant_index, index)
                    for index, item in enumerate(raw_checklist, 1)
                ])
            questions[action_name] = question_variants
            checklists[action_name] = checklist_variants
        result[club_name]['questions'] = questions
        if any(items for variants in checklists.values() for items in variants):
            result[club_name]['checklists'] = checklists
        else:
            result[club_name].pop('checklists', None)
    if received != set(editable):
        raise ValueError('В данных отсутствуют клубы из текущей конфигурации')
    return result


def _editor_payload(clubs, version_hash):
    items = []
    for name, info in clubs.items():
        if not _is_editable(info):
            continue
        actions = {}
        for action_key, action_name in ACTIONS.items():
            question_variants = info.get('questions', {}).get(action_name, [])
            checklist_variants = info.get('checklists', {}).get(action_name, [])
            actions[action_key] = [
                {
                    'questions': copy.deepcopy(questions),
                    'checklist': copy.deepcopy(checklist_variants[index])
                    if index < len(checklist_variants) else [],
                }
                for index, questions in enumerate(question_variants)
            ] or [{'questions': [], 'checklist': []}]
        items.append({'id': str(info['_config_id']), 'name': name, 'actions': actions})
    return {'version': version_hash, 'clubs': items}


def get_editor_config(db_path):
    with _lock, _database(db_path) as connection:
        _initialize(connection)
        clubs = get_clubs()
        _snapshot_value, version_hash = _ensure_current_version(connection, clubs)
        return _editor_payload(clubs, version_hash)


def save_editor_config(db_path, payload, actor_login):
    with _lock, _database(db_path) as connection:
        _initialize(connection)
        connection.commit()
        connection.execute('BEGIN IMMEDIATE')
        current = get_clubs()
        _current_snapshot, current_hash = _ensure_current_version(connection, current)
        if str(payload.get('version') or '') != current_hash:
            raise RuntimeError('Конфигурация уже изменилась. Обновите страницу и повторите правки.')
        updated = _compile_payload(payload, current)
        save_clubs(updated, source='shift_editor')
        new_snapshot = _snapshot(updated)
        version_hash = _record(connection, new_snapshot, actor_login, 'save')
        return _editor_payload(updated, version_hash)


def list_versions(db_path, limit=30):
    with _lock, _database(db_path) as connection:
        _initialize(connection)
        _snapshot_value, current_hash = _ensure_current_version(connection, get_clubs())
        rows = connection.execute(
            '''
            SELECT ID, version_hash, created_at, actor_login, action
            FROM shift_config_versions
            ORDER BY ID DESC
            LIMIT ?
            ''',
            (max(1, min(int(limit), 100)),),
        ).fetchall()
        versions = [dict(row) for row in rows]
        current_marked = False
        for version in versions:
            version['is_current'] = (
                not current_marked
                and version['version_hash'] == current_hash
            )
            current_marked = current_marked or version['is_current']
        return versions


def rollback_version(db_path, version_id, expected_version, actor_login):
    with _lock, _database(db_path) as connection:
        _initialize(connection)
        connection.commit()
        connection.execute('BEGIN IMMEDIATE')
        current = get_clubs()
        _current_snapshot, current_hash = _ensure_current_version(connection, current)
        if str(expected_version or '') != current_hash:
            raise RuntimeError('Конфигурация уже изменилась. Обновите страницу.')
        row = connection.execute(
            'SELECT snapshot_json FROM shift_config_versions WHERE ID=?',
            (int(version_id),),
        ).fetchone()
        if not row:
            raise LookupError('Версия не найдена')
        snapshot = json.loads(row['snapshot_json'])
        updated = copy.deepcopy(current)
        current_by_id = {str(info['_config_id']): name for name, info in updated.items()}
        if set(snapshot) != {
            club_id for club_id, name in current_by_id.items()
            if _is_editable(updated[name])
        }:
            raise ValueError('Состав клубов в этой версии отличается от текущего')
        for club_id, saved in snapshot.items():
            name = current_by_id[club_id]
            updated[name]['questions'] = copy.deepcopy(saved['questions'])
            if saved.get('checklists'):
                updated[name]['checklists'] = copy.deepcopy(saved['checklists'])
            else:
                updated[name].pop('checklists', None)
        save_clubs(updated, source='shift_editor_rollback')
        version_hash = _record(connection, _snapshot(updated), actor_login, f'rollback:{version_id}')
        return _editor_payload(updated, version_hash)
