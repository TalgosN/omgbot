import re
import math
from collections import defaultdict
from datetime import datetime

from club_config import DEFAULT_SCHEDULE_EMOJIS


CLUBS_SHEET = 'Clubs'
QUESTIONS_SHEET = 'Config Questions'
CHECKLISTS_SHEET = 'Config Checklists'
VALIDATION_SHEET = 'Config Validation'

OPEN_ACTION = '✅ Открыть смену'
CLOSE_ACTION = '🚫 Закрыть смену'
ACTION_CODES = {'open': OPEN_ACTION, 'close': CLOSE_ACTION}
ACTION_NAMES = {value: key for key, value in ACTION_CODES.items()}
QUESTION_TYPES = {'text', 'photo', 'num'}

CLUB_HEADERS = [
    'ClubID', 'Name', 'AccountName', 'ShiftName', 'ScheduleVisible', 'ScheduleEmoji',
    'Tag', 'Physical', 'GeoRequired',
    'Latitude', 'Longitude', 'Radius', 'AutoCloseTime', 'StatusCloseTime',
    'EarlyCheckTime', 'OpenWeekdays', 'OpenWeekend', 'StrictWeekdays',
    'StrictWeekend', 'QuestionVariants', 'Active', 'SortOrder',
]
QUESTION_HEADERS = [
    'QuestionID', 'ClubID', 'Action', 'Variants', 'Order', 'Type',
    'Question', 'Active',
]
CHECKLIST_HEADERS = [
    'ChecklistID', 'ClubID', 'Action', 'Order', 'Text', 'Active',
]


class ConfigValidationError(ValueError):
    pass


def _text(value):
    return '' if value is None else str(value).strip()


def _bool(value, sheet, row, field):
    if isinstance(value, bool):
        return value
    normalized = _text(value).casefold()
    if normalized in {'true', '1', 'да', 'yes'}:
        return True
    if normalized in {'false', '0', 'нет', 'no'}:
        return False
    raise ConfigValidationError(
        f'{sheet}, строка {row}: {field} должен быть TRUE или FALSE'
    )


def _integer(value, sheet, row, field, minimum=0):
    try:
        parsed = float(_text(value))
    except (TypeError, ValueError):
        raise ConfigValidationError(
            f'{sheet}, строка {row}: {field} должен быть целым числом'
        )
    if not math.isfinite(parsed) or not parsed.is_integer():
        raise ConfigValidationError(
            f'{sheet}, строка {row}: {field} должен быть целым числом'
        )
    number = int(parsed)
    if number < minimum:
        raise ConfigValidationError(
            f'{sheet}, строка {row}: {field} должен быть не меньше {minimum}'
        )
    return number


def _number(value, sheet, row, field):
    try:
        number = float(_text(value).replace(',', '.'))
    except (TypeError, ValueError):
        raise ConfigValidationError(
            f'{sheet}, строка {row}: {field} должен быть числом'
        )
    if not math.isfinite(number):
        raise ConfigValidationError(
            f'{sheet}, строка {row}: {field} должен быть числом'
        )
    return number


def _time(value, sheet, row, field):
    raw = _text(value)
    for pattern in ('%H:%M:%S', '%H:%M'):
        try:
            parsed = datetime.strptime(raw, pattern)
            return parsed.strftime('%H:%M:%S')
        except ValueError:
            pass
    raise ConfigValidationError(
        f'{sheet}, строка {row}: {field} должен иметь формат ЧЧ:ММ или ЧЧ:ММ:СС'
    )


def _tag(value, sheet, row):
    tag = re.sub(r'\s+', ' ', _text(value).replace('\n', ' ')).strip()
    if not tag:
        raise ConfigValidationError(f'{sheet}, строка {row}: Tag не заполнен')
    for part in [item.strip() for item in tag.split(',')]:
        if not re.fullmatch(r'@[A-Za-z0-9_]+', part):
            raise ConfigValidationError(
                f'{sheet}, строка {row}: некорректный Telegram-тег {part!r}'
            )
    return ', '.join(item.strip() for item in tag.split(','))


def _records(values, headers, sheet):
    if not values:
        raise ConfigValidationError(f'{sheet}: лист пуст')
    actual_headers = [_text(value) for value in values[0]]
    duplicates = sorted({name for name in actual_headers if name and actual_headers.count(name) > 1})
    if duplicates:
        raise ConfigValidationError(f'{sheet}: повторяющиеся заголовки: {", ".join(duplicates)}')
    missing = [header for header in headers if header not in actual_headers]
    if missing:
        raise ConfigValidationError(f'{sheet}: отсутствуют столбцы: {", ".join(missing)}')
    indexes = {header: actual_headers.index(header) for header in headers}
    result = []
    for row_number, values_row in enumerate(values[1:], start=2):
        if not any(_text(value) for value in values_row):
            continue
        record = {
            header: values_row[index] if index < len(values_row) else ''
            for header, index in indexes.items()
        }
        record['_row'] = row_number
        result.append(record)
    return result


def _parse_clubs(values):
    records = _records(values, CLUB_HEADERS, CLUBS_SHEET)
    clubs_by_id = {}
    names = set()
    sort_orders = set()
    for record in records:
        row = record['_row']
        club_id = _text(record['ClubID']).casefold()
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', club_id):
            raise ConfigValidationError(
                f'{CLUBS_SHEET}, строка {row}: некорректный ClubID {club_id!r}'
            )
        if club_id in clubs_by_id:
            raise ConfigValidationError(
                f'{CLUBS_SHEET}, строка {row}: ClubID {club_id!r} уже используется'
            )
        active = _bool(record['Active'], CLUBS_SHEET, row, 'Active')
        name = _text(record['Name'])
        if not name:
            raise ConfigValidationError(f'{CLUBS_SHEET}, строка {row}: Name не заполнен')
        name_key = name.casefold()
        if name_key in names:
            raise ConfigValidationError(
                f'{CLUBS_SHEET}, строка {row}: название {name!r} уже используется'
            )
        names.add(name_key)
        sort_order = _integer(record['SortOrder'], CLUBS_SHEET, row, 'SortOrder', 1)
        if active and sort_order in sort_orders:
            raise ConfigValidationError(
                f'{CLUBS_SHEET}, строка {row}: SortOrder {sort_order} уже используется'
            )
        if active:
            sort_orders.add(sort_order)
        physical = _bool(record['Physical'], CLUBS_SHEET, row, 'Physical')
        require_geo = _bool(record['GeoRequired'], CLUBS_SHEET, row, 'GeoRequired')
        variant_count = _integer(
            record['QuestionVariants'], CLUBS_SHEET, row, 'QuestionVariants',
            1 if physical else 0,
        )
        info = {
            '_config_id': club_id,
            'schedule_visible': _bool(
                record['ScheduleVisible'], CLUBS_SHEET, row, 'ScheduleVisible',
            ),
            'schedule_emoji': _text(record['ScheduleEmoji']) or '📍',
            'tag': _tag(record['Tag'], CLUBS_SHEET, row),
            'acc_name': _text(record['AccountName']) or name,
            'shift_name': _text(record['ShiftName']) or name,
            'is_physical': physical,
            'require_geo': require_geo,
        }
        if require_geo:
            info['coords'] = {
                'lat': _number(record['Latitude'], CLUBS_SHEET, row, 'Latitude'),
                'lon': _number(record['Longitude'], CLUBS_SHEET, row, 'Longitude'),
            }
            info['radius'] = _integer(record['Radius'], CLUBS_SHEET, row, 'Radius', 1)
        elif _text(record['Latitude']) and _text(record['Longitude']):
            info['coords'] = {
                'lat': _number(record['Latitude'], CLUBS_SHEET, row, 'Latitude'),
                'lon': _number(record['Longitude'], CLUBS_SHEET, row, 'Longitude'),
            }
            if _text(record['Radius']):
                info['radius'] = _integer(record['Radius'], CLUBS_SHEET, row, 'Radius', 1)
        schedule_fields = [
            'AutoCloseTime', 'StatusCloseTime', 'EarlyCheckTime',
            'OpenWeekdays', 'OpenWeekend', 'StrictWeekdays', 'StrictWeekend',
        ]
        has_schedule = any(_text(record[field]) for field in schedule_fields)
        if physical or has_schedule:
            if not all(_text(record[field]) for field in schedule_fields):
                raise ConfigValidationError(
                    f'{CLUBS_SHEET}, строка {row}: расписание должно быть заполнено полностью'
                )
            info['schedule'] = {
                'auto_close_time': _time(record['AutoCloseTime'], CLUBS_SHEET, row, 'AutoCloseTime'),
                'status_close_time': _time(record['StatusCloseTime'], CLUBS_SHEET, row, 'StatusCloseTime'),
                'early_check_time': _time(record['EarlyCheckTime'], CLUBS_SHEET, row, 'EarlyCheckTime'),
                'open': {
                    'weekdays': _time(record['OpenWeekdays'], CLUBS_SHEET, row, 'OpenWeekdays'),
                    'weekend': _time(record['OpenWeekend'], CLUBS_SHEET, row, 'OpenWeekend'),
                },
                'open_strict': {
                    'weekdays': _time(record['StrictWeekdays'], CLUBS_SHEET, row, 'StrictWeekdays'),
                    'weekend': _time(record['StrictWeekend'], CLUBS_SHEET, row, 'StrictWeekend'),
                },
            }
        clubs_by_id[club_id] = {
            'id': club_id,
            'name': name,
            'active': active,
            'sort_order': sort_order,
            'variant_count': variant_count,
            'info': info,
        }
    active_clubs = [club for club in clubs_by_id.values() if club['active']]
    if not active_clubs:
        raise ConfigValidationError(f'{CLUBS_SHEET}: нет активных клубов')
    return clubs_by_id


def _action(value, sheet, row):
    action = _text(value).casefold()
    if action not in ACTION_CODES:
        raise ConfigValidationError(
            f'{sheet}, строка {row}: Action должен быть open или close'
        )
    return action


def _variants(value, variant_count, sheet, row):
    raw = _text(value).casefold()
    if raw == 'all':
        return list(range(variant_count))
    result = []
    for item in raw.split(','):
        variant = _integer(item, sheet, row, 'Variants', 0)
        if variant >= variant_count:
            raise ConfigValidationError(
                f'{sheet}, строка {row}: варианта {variant} нет, допустимо 0..{variant_count - 1}'
            )
        if variant not in result:
            result.append(variant)
    if not result:
        raise ConfigValidationError(f'{sheet}, строка {row}: Variants не заполнен')
    return result


def _apply_questions(clubs_by_id, values):
    records = _records(values, QUESTION_HEADERS, QUESTIONS_SHEET)
    used_ids = set()
    positions = set()
    exact_questions = set()
    grouped = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for record in records:
        row = record['_row']
        question_id = _text(record['QuestionID']).casefold()
        if not question_id:
            raise ConfigValidationError(
                f'{QUESTIONS_SHEET}, строка {row}: QuestionID не заполнен'
            )
        if question_id in used_ids:
            raise ConfigValidationError(
                f'{QUESTIONS_SHEET}, строка {row}: QuestionID {question_id!r} уже используется'
            )
        used_ids.add(question_id)
        if not _bool(record['Active'], QUESTIONS_SHEET, row, 'Active'):
            continue
        club_id = _text(record['ClubID']).casefold()
        club = clubs_by_id.get(club_id)
        if not club or not club['active']:
            raise ConfigValidationError(
                f'{QUESTIONS_SHEET}, строка {row}: неизвестный активный ClubID {club_id!r}'
            )
        if club['variant_count'] == 0:
            raise ConfigValidationError(
                f'{QUESTIONS_SHEET}, строка {row}: для клуба {club["name"]!r} '
                f'QuestionVariants должен быть больше нуля'
            )
        action = _action(record['Action'], QUESTIONS_SHEET, row)
        variants = _variants(
            record['Variants'], club['variant_count'], QUESTIONS_SHEET, row,
        )
        order = _integer(record['Order'], QUESTIONS_SHEET, row, 'Order', 1)
        question_type = _text(record['Type']).casefold()
        if question_type not in QUESTION_TYPES:
            raise ConfigValidationError(
                f'{QUESTIONS_SHEET}, строка {row}: Type должен быть text, photo или num'
            )
        question = _text(record['Question'])
        if not question:
            raise ConfigValidationError(
                f'{QUESTIONS_SHEET}, строка {row}: Question не заполнен'
            )
        for variant in variants:
            position_key = (club_id, action, variant, order)
            if position_key in positions:
                raise ConfigValidationError(
                    f'{QUESTIONS_SHEET}, строка {row}: позиция {order} уже занята '
                    f'для {club["name"]}/{action}/вариант {variant}'
                )
            positions.add(position_key)
            exact_key = (club_id, action, variant, question_type, question.casefold())
            if exact_key in exact_questions:
                raise ConfigValidationError(
                    f'{QUESTIONS_SHEET}, строка {row}: вопрос продублирован '
                    f'в {club["name"]}/{action}/вариант {variant}'
                )
            exact_questions.add(exact_key)
            grouped[club_id][action][variant].append((
                order, {'text': question, 'type': question_type},
            ))

    for club_id, club in clubs_by_id.items():
        if not club['active'] or club['variant_count'] == 0:
            continue
        questions = {}
        for action_code, action_name in ACTION_CODES.items():
            variants = []
            for variant in range(club['variant_count']):
                rows = sorted(grouped[club_id][action_code][variant], key=lambda item: item[0])
                if not rows:
                    raise ConfigValidationError(
                        f'{QUESTIONS_SHEET}: нет вопросов для '
                        f'{club["name"]}/{action_code}/вариант {variant}'
                    )
                variants.append([question for _, question in rows])
            questions[action_name] = variants
        club['info']['questions'] = questions


def _apply_checklists(clubs_by_id, values):
    records = _records(values, CHECKLIST_HEADERS, CHECKLISTS_SHEET)
    used_ids = set()
    positions = set()
    exact_items = set()
    grouped = defaultdict(lambda: defaultdict(list))
    for record in records:
        row = record['_row']
        checklist_id = _text(record['ChecklistID']).casefold()
        if not checklist_id:
            raise ConfigValidationError(
                f'{CHECKLISTS_SHEET}, строка {row}: ChecklistID не заполнен'
            )
        if checklist_id in used_ids:
            raise ConfigValidationError(
                f'{CHECKLISTS_SHEET}, строка {row}: ChecklistID {checklist_id!r} уже используется'
            )
        used_ids.add(checklist_id)
        if not _bool(record['Active'], CHECKLISTS_SHEET, row, 'Active'):
            continue
        club_id = _text(record['ClubID']).casefold()
        club = clubs_by_id.get(club_id)
        if not club or not club['active']:
            raise ConfigValidationError(
                f'{CHECKLISTS_SHEET}, строка {row}: неизвестный активный ClubID {club_id!r}'
            )
        action = _action(record['Action'], CHECKLISTS_SHEET, row)
        order = _integer(record['Order'], CHECKLISTS_SHEET, row, 'Order', 1)
        text = _text(record['Text'])
        if not text:
            raise ConfigValidationError(
                f'{CHECKLISTS_SHEET}, строка {row}: Text не заполнен'
            )
        position_key = (club_id, action, order)
        if position_key in positions:
            raise ConfigValidationError(
                f'{CHECKLISTS_SHEET}, строка {row}: позиция {order} уже занята '
                f'для {club["name"]}/{action}'
            )
        positions.add(position_key)
        exact_key = (club_id, action, text.casefold())
        if exact_key in exact_items:
            raise ConfigValidationError(
                f'{CHECKLISTS_SHEET}, строка {row}: пункт чек-листа продублирован '
                f'для {club["name"]}/{action}'
            )
        exact_items.add(exact_key)
        grouped[club_id][action].append((order, text))

    for club_id, club in clubs_by_id.items():
        if not club['active']:
            continue
        checklists = {}
        for action_code, action_name in ACTION_CODES.items():
            rows = sorted(grouped[club_id][action_code], key=lambda item: item[0])
            if rows:
                checklists[action_name] = [text for _, text in rows]
        if checklists:
            club['info']['checklists'] = checklists


def build_config(club_values, question_values, checklist_values):
    clubs_by_id = _parse_clubs(club_values)
    _apply_questions(clubs_by_id, question_values)
    _apply_checklists(clubs_by_id, checklist_values)
    ordered = sorted(
        (club for club in clubs_by_id.values() if club['active']),
        key=lambda club: club['sort_order'],
    )
    return {club['name']: club['info'] for club in ordered}


def _club_ids(clubs):
    result = {}
    used = set()
    for name, info in clubs.items():
        club_id = _text(info.get('_config_id')).casefold()
        if re.fullmatch(r'[a-z0-9][a-z0-9_-]*', club_id) and club_id not in used:
            result[name] = club_id
            used.add(club_id)

    index = 1
    for name in clubs:
        if name in result:
            continue
        while f'club_{index:02d}' in used:
            index += 1
        club_id = f'club_{index:02d}'
        result[name] = club_id
        used.add(club_id)
        index += 1
    return result


def clubs_to_values(clubs):
    ids = _club_ids(clubs)
    rows = [CLUB_HEADERS]
    schedule_index = 0
    for sort_order, (name, info) in enumerate(clubs.items(), start=1):
        schedule = info.get('schedule', {})
        open_schedule = schedule.get('open', {})
        strict_schedule = schedule.get('open_strict', {})
        coords = info.get('coords', {})
        variant_count = max(
            [len(variants) for variants in info.get('questions', {}).values()] or [0]
        )
        schedule_visible = info.get('schedule_visible')
        if schedule_visible is None:
            schedule_visible = bool(info.get('schedule') or info.get('is_physical'))
        schedule_emoji = info.get('schedule_emoji')
        if schedule_visible and not schedule_emoji:
            schedule_emoji = DEFAULT_SCHEDULE_EMOJIS[
                schedule_index % len(DEFAULT_SCHEDULE_EMOJIS)
            ]
        if schedule_visible:
            schedule_index += 1
        rows.append([
            ids[name], name, info.get('acc_name', name), info.get('shift_name', name),
            schedule_visible,
            schedule_emoji or '📍', _text(info.get('tag')).replace('\n', ' '),
            bool(info.get('is_physical')), bool(info.get('require_geo')),
            coords.get('lat', ''), coords.get('lon', ''), info.get('radius', ''),
            schedule.get('auto_close_time', ''), schedule.get('status_close_time', ''),
            schedule.get('early_check_time', ''), open_schedule.get('weekdays', ''),
            open_schedule.get('weekend', ''), strict_schedule.get('weekdays', ''),
            strict_schedule.get('weekend', ''), variant_count, True, sort_order,
        ])
    return rows, ids


def questions_to_values(clubs, ids):
    rows = [QUESTION_HEADERS]
    for club_name, info in clubs.items():
        club_id = ids[club_name]
        for action_name, variants in info.get('questions', {}).items():
            action = ACTION_NAMES.get(action_name)
            if not action:
                continue
            cleaned = []
            for questions in variants:
                seen = set()
                variant_questions = []
                for question in questions:
                    key = (_text(question.get('type')).casefold(), _text(question.get('text')).casefold())
                    if key in seen:
                        continue
                    seen.add(key)
                    variant_questions.append(question)
                cleaned.append(variant_questions)
            grouped = defaultdict(set)
            details = {}
            for variant, questions in enumerate(cleaned):
                for order, question in enumerate(questions, start=1):
                    key = (order, _text(question.get('type')).casefold(), _text(question.get('text')))
                    grouped[key].add(variant)
                    details[key] = question
            for index, key in enumerate(sorted(grouped, key=lambda item: (item[0], item[2])), start=1):
                order, question_type, question_text = key
                variants_set = grouped[key]
                variants_value = (
                    'all' if variants_set == set(range(len(cleaned)))
                    else ','.join(str(value) for value in sorted(variants_set))
                )
                rows.append([
                    f'q_{club_id}_{action}_{index:03d}', club_id, action,
                    variants_value, order, question_type, question_text, True,
                ])
    return rows


def checklists_to_values(clubs, ids):
    rows = [CHECKLIST_HEADERS]
    for club_name, info in clubs.items():
        club_id = ids[club_name]
        for action_name, checklist in info.get('checklists', {}).items():
            action = ACTION_NAMES.get(action_name)
            if not action:
                continue
            seen = set()
            order = 0
            for text in checklist:
                normalized = _text(text)
                if not normalized or normalized.casefold() in seen:
                    continue
                seen.add(normalized.casefold())
                order += 1
                rows.append([
                    f'c_{club_id}_{action}_{order:03d}', club_id, action,
                    order, normalized, True,
                ])
    return rows


def validation_values(message='Листы созданы из текущего clubs.json'):
    return [
        ['Параметр', 'Значение'],
        ['Статус', message],
        ['Последняя проверка', datetime.now().strftime('%Y-%m-%d %H:%M:%S')],
        ['Допустимые Action', 'open, close'],
        ['Допустимые Type', 'text, photo, num'],
        ['Variants', 'all или номера через запятую: 0,1,2'],
        ['Правило публикации', 'При любой ошибке clubs.json не изменяется'],
    ]


def _worksheet_map(spreadsheet):
    return {worksheet.title: worksheet for worksheet in spreadsheet.worksheets()}


def _create_sheet(spreadsheet, title, values):
    rows = max(len(values) + 20, 100)
    columns = max(len(values[0]) + 2, 10)
    worksheet = spreadsheet.add_worksheet(title, rows=rows, cols=columns)
    worksheet.update_values('A1', values, extend=True)
    return worksheet


def ensure_config_sheets(spreadsheet, clubs):
    worksheets = _worksheet_map(spreadsheet)
    club_values, ids = clubs_to_values(clubs)
    seeds = {
        CLUBS_SHEET: club_values,
        QUESTIONS_SHEET: questions_to_values(clubs, ids),
        CHECKLISTS_SHEET: checklists_to_values(clubs, ids),
        VALIDATION_SHEET: validation_values(),
    }
    created = []
    for title, values in seeds.items():
        if title not in worksheets:
            worksheets[title] = _create_sheet(spreadsheet, title, values)
            created.append(title)
    return worksheets, created


def _sheet_values(worksheet):
    return worksheet.get_all_values(
        include_tailing_empty=False,
        include_tailing_empty_rows=False,
    )


def count_config(config):
    questions = 0
    checklists = 0
    for info in config.values():
        questions += sum(
            len(questions)
            for variants in info.get('questions', {}).values()
            for questions in variants
        )
        checklists += sum(len(items) for items in info.get('checklists', {}).values())
    return len(config), questions, checklists


def config_diff(old, new):
    old_names = set(old)
    new_names = set(new)
    changed = sorted(
        name for name in old_names & new_names if old[name] != new[name]
    )
    return {
        'added': sorted(new_names - old_names),
        'removed': sorted(old_names - new_names),
        'changed': changed,
    }


def validate_stable_identity(current_config, new_config):
    current_by_id = {
        _text(info.get('_config_id')).casefold(): name
        for name, info in current_config.items()
        if _text(info.get('_config_id'))
    }
    new_by_id = {
        _text(info.get('_config_id')).casefold(): name
        for name, info in new_config.items()
        if _text(info.get('_config_id'))
    }
    for club_id in current_by_id.keys() & new_by_id.keys():
        old_name = current_by_id[club_id]
        new_name = new_by_id[club_id]
        if old_name != new_name:
            raise ConfigValidationError(
                f'{CLUBS_SHEET}: нельзя менять Name существующего клуба '
                f'{old_name!r} на {new_name!r}. Название связано с историей и таблицами БД.'
            )

    current_by_name = {
        name: _text(info.get('_config_id')).casefold()
        for name, info in current_config.items()
        if _text(info.get('_config_id'))
    }
    new_by_name = {
        name: _text(info.get('_config_id')).casefold()
        for name, info in new_config.items()
        if _text(info.get('_config_id'))
    }
    for name in current_by_name.keys() & new_by_name.keys():
        if current_by_name[name] != new_by_name[name]:
            raise ConfigValidationError(
                f'{CLUBS_SHEET}: нельзя менять ClubID существующего клуба {name!r}'
            )


def read_config(spreadsheet, current_config):
    worksheets, created = ensure_config_sheets(spreadsheet, current_config)
    try:
        config = build_config(
            _sheet_values(worksheets[CLUBS_SHEET]),
            _sheet_values(worksheets[QUESTIONS_SHEET]),
            _sheet_values(worksheets[CHECKLISTS_SHEET]),
        )
        validate_stable_identity(current_config, config)
    except ConfigValidationError as error:
        try:
            write_validation(worksheets[VALIDATION_SHEET], f'ОШИБКА: {error}')
        except Exception:
            pass
        raise
    return config, worksheets, created


def write_validation(worksheet, message):
    worksheet.update_values('A1', validation_values(message), extend=True)
