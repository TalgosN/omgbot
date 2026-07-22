import math
import re
from collections import defaultdict
from datetime import datetime


INSTRUCTIONS_SHEET = '📘 Инструкция'
CLUBS_SHEET = '🏷 Клубы'
SCHEDULE_SHEET = '🕒 Время работы'
QUESTIONS_SHEET = '❓ Вопросы смены'
VALIDATION_SHEET = '🚦 Проверка'
SYSTEM_SHEET = '_Система'
REQUIRED_SHEETS = (
    INSTRUCTIONS_SHEET,
    CLUBS_SHEET,
    SCHEDULE_SHEET,
    QUESTIONS_SHEET,
    VALIDATION_SHEET,
    SYSTEM_SHEET,
)

OPEN_ACTION = '✅ Открыть смену'
CLOSE_ACTION = '🚫 Закрыть смену'
ACTION_CODES = {'open': OPEN_ACTION, 'close': CLOSE_ACTION}
ACTION_LABELS = {'открытие': 'open', 'закрытие': 'close'}
QUESTION_TYPE_LABELS = {'текст': 'text', 'фото': 'photo', 'число': 'num'}
ALL_VARIANTS = 'все наборы'

SYSTEM_HEADERS = [
    'ClubID', 'Клуб', 'Название для сообщений', 'Название в OMG Shift',
    'Физический клуб', 'Проверять геолокацию', 'Широта', 'Долгота', 'Радиус',
    'Автозакрытие', 'Количество наборов', 'Эмодзи', 'Активен', 'Порядок',
]
CLUB_HEADERS = ['Клуб', 'Telegram-теги', 'Показывать в расписании']
SCHEDULE_HEADERS = [
    'Клуб', 'Контроль закрытия', 'Ранняя проверка', 'Открытие в будни',
    'Открытие в выходные', 'Строгое время в будни', 'Строгое время в выходные',
]
QUESTION_HEADERS = [
    'Клуб', 'Действие', 'Набор', 'Номер', 'Пункт чек-листа',
    'Формат ответа', 'Вопрос сотруднику', 'Используется',
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
        f'{sheet}, строка {row}: поле «{field}» должно быть чекбоксом'
    )


def _integer(value, sheet, row, field, minimum=0):
    try:
        parsed = float(_text(value))
    except (TypeError, ValueError):
        raise ConfigValidationError(
            f'{sheet}, строка {row}: «{field}» должен быть целым числом'
        )
    if not math.isfinite(parsed) or not parsed.is_integer():
        raise ConfigValidationError(
            f'{sheet}, строка {row}: «{field}» должен быть целым числом'
        )
    number = int(parsed)
    if number < minimum:
        raise ConfigValidationError(
            f'{sheet}, строка {row}: «{field}» должен быть не меньше {minimum}'
        )
    return number


def _number(value, sheet, row, field):
    try:
        number = float(_text(value).replace(',', '.'))
    except (TypeError, ValueError):
        raise ConfigValidationError(
            f'{sheet}, строка {row}: «{field}» должно быть числом'
        )
    if not math.isfinite(number):
        raise ConfigValidationError(
            f'{sheet}, строка {row}: «{field}» должно быть числом'
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
    try:
        day_fraction = float(raw.replace(',', '.'))
    except ValueError:
        day_fraction = None
    if day_fraction is not None and 0 <= day_fraction < 1:
        seconds = round(day_fraction * 24 * 60 * 60)
        hours, remainder = divmod(seconds, 60 * 60)
        minutes, seconds = divmod(remainder, 60)
        return f'{hours:02d}:{minutes:02d}:{seconds:02d}'
    raise ConfigValidationError(
        f'{sheet}, строка {row}: «{field}» должно иметь формат ЧЧ:ММ'
    )


def _tag(value, sheet, row):
    tag = re.sub(r'\s+', ' ', _text(value).replace('\n', ' ')).strip()
    if not tag:
        raise ConfigValidationError(f'{sheet}, строка {row}: Telegram-теги не заполнены')
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
        if not any(
            _text(value) and _text(value).casefold() != 'false'
            for value in values_row
        ):
            continue
        record = {
            header: values_row[index] if index < len(values_row) else ''
            for header, index in indexes.items()
        }
        record['_row'] = row_number
        result.append(record)
    return result


def _parse_system(values):
    records = _records(values, SYSTEM_HEADERS, SYSTEM_SHEET)
    clubs_by_id = {}
    names = set()
    sort_orders = set()
    for record in records:
        row = record['_row']
        club_id = _text(record['ClubID']).casefold()
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', club_id):
            raise ConfigValidationError(f'{SYSTEM_SHEET}, строка {row}: некорректный ClubID {club_id!r}')
        if club_id in clubs_by_id:
            raise ConfigValidationError(f'{SYSTEM_SHEET}, строка {row}: ClubID {club_id!r} уже используется')
        name = _text(record['Клуб'])
        if not name:
            raise ConfigValidationError(f'{SYSTEM_SHEET}, строка {row}: название клуба не заполнено')
        name_key = name.casefold()
        if name_key in names:
            raise ConfigValidationError(f'{SYSTEM_SHEET}, строка {row}: клуб {name!r} продублирован')
        names.add(name_key)
        active = _bool(record['Активен'], SYSTEM_SHEET, row, 'Активен')
        sort_order = _integer(record['Порядок'], SYSTEM_SHEET, row, 'Порядок', 1)
        if active and sort_order in sort_orders:
            raise ConfigValidationError(f'{SYSTEM_SHEET}, строка {row}: порядок {sort_order} уже используется')
        if active:
            sort_orders.add(sort_order)
        physical = _bool(record['Физический клуб'], SYSTEM_SHEET, row, 'Физический клуб')
        require_geo = _bool(record['Проверять геолокацию'], SYSTEM_SHEET, row, 'Проверять геолокацию')
        variant_count = _integer(record['Количество наборов'], SYSTEM_SHEET, row, 'Количество наборов', 1 if physical else 0)
        if variant_count > 26:
            raise ConfigValidationError(f'{SYSTEM_SHEET}, строка {row}: нельзя задать больше 26 наборов')
        account_name = _text(record['Название для сообщений'])
        shift_name = _text(record['Название в OMG Shift'])
        if not account_name or not shift_name:
            raise ConfigValidationError(f'{SYSTEM_SHEET}, строка {row}: системные названия клуба не заполнены')
        info = {
            '_config_id': club_id,
            'schedule_emoji': _text(record['Эмодзи']),
            'acc_name': account_name,
            'shift_name': shift_name,
            'is_physical': physical,
            'require_geo': require_geo,
        }
        latitude = _text(record['Широта'])
        longitude = _text(record['Долгота'])
        radius = _text(record['Радиус'])
        if require_geo and not (latitude and longitude and radius):
            raise ConfigValidationError(f'{SYSTEM_SHEET}, строка {row}: для проверки геолокации нужны координаты и радиус')
        if latitude or longitude:
            if not (latitude and longitude):
                raise ConfigValidationError(f'{SYSTEM_SHEET}, строка {row}: широта и долгота заполняются вместе')
            info['coords'] = {
                'lat': _number(latitude, SYSTEM_SHEET, row, 'Широта'),
                'lon': _number(longitude, SYSTEM_SHEET, row, 'Долгота'),
            }
            if radius:
                info['radius'] = _integer(radius, SYSTEM_SHEET, row, 'Радиус', 1)
        clubs_by_id[club_id] = {
            'id': club_id,
            'name': name,
            'active': active,
            'sort_order': sort_order,
            'variant_count': variant_count,
            'auto_close_time': _text(record['Автозакрытие']),
            'info': info,
        }
    if not any(club['active'] for club in clubs_by_id.values()):
        raise ConfigValidationError(f'{SYSTEM_SHEET}: нет активных клубов')
    return clubs_by_id


def _clubs_by_name(clubs_by_id):
    return {club['name'].casefold(): club for club in clubs_by_id.values()}


def _apply_club_settings(clubs_by_id, values):
    records = _records(values, CLUB_HEADERS, CLUBS_SHEET)
    by_name = _clubs_by_name(clubs_by_id)
    seen = set()
    for record in records:
        row = record['_row']
        name = _text(record['Клуб'])
        key = name.casefold()
        club = by_name.get(key)
        if not club:
            raise ConfigValidationError(f'{CLUBS_SHEET}, строка {row}: неизвестный клуб {name!r}')
        if key in seen:
            raise ConfigValidationError(f'{CLUBS_SHEET}, строка {row}: клуб {name!r} продублирован')
        seen.add(key)
        if not club['active']:
            continue
        club['info']['tag'] = _tag(record['Telegram-теги'], CLUBS_SHEET, row)
        club['info']['schedule_visible'] = _bool(record['Показывать в расписании'], CLUBS_SHEET, row, 'Показывать в расписании')
        if club['info']['schedule_visible'] and not club['info']['schedule_emoji']:
            raise ConfigValidationError(f'{SYSTEM_SHEET}: у клуба {name!r} не заполнен эмодзи расписания')
    missing = [club['name'] for club in clubs_by_id.values() if club['active'] and club['name'].casefold() not in seen]
    if missing:
        raise ConfigValidationError(f'{CLUBS_SHEET}: нет строк для клубов: {", ".join(missing)}')


def _apply_schedules(clubs_by_id, values):
    records = _records(values, SCHEDULE_HEADERS, SCHEDULE_SHEET)
    by_name = _clubs_by_name(clubs_by_id)
    seen = set()
    time_fields = SCHEDULE_HEADERS[1:]
    for record in records:
        row = record['_row']
        name = _text(record['Клуб'])
        key = name.casefold()
        club = by_name.get(key)
        if not club:
            raise ConfigValidationError(f'{SCHEDULE_SHEET}, строка {row}: неизвестный клуб {name!r}')
        if key in seen:
            raise ConfigValidationError(f'{SCHEDULE_SHEET}, строка {row}: клуб {name!r} продублирован')
        seen.add(key)
        values_present = [_text(record[field]) for field in time_fields]
        has_schedule = bool(club['auto_close_time'] or any(values_present))
        if not has_schedule and not club['info']['is_physical']:
            continue
        if not club['auto_close_time'] or not all(values_present):
            raise ConfigValidationError(f'{SCHEDULE_SHEET}, строка {row}: время работы клуба {name!r} должно быть заполнено полностью')
        club['info']['schedule'] = {
            'auto_close_time': _time(club['auto_close_time'], SYSTEM_SHEET, row, 'Автозакрытие'),
            'status_close_time': _time(record['Контроль закрытия'], SCHEDULE_SHEET, row, 'Контроль закрытия'),
            'early_check_time': _time(record['Ранняя проверка'], SCHEDULE_SHEET, row, 'Ранняя проверка'),
            'open': {
                'weekdays': _time(record['Открытие в будни'], SCHEDULE_SHEET, row, 'Открытие в будни'),
                'weekend': _time(record['Открытие в выходные'], SCHEDULE_SHEET, row, 'Открытие в выходные'),
            },
            'open_strict': {
                'weekdays': _time(record['Строгое время в будни'], SCHEDULE_SHEET, row, 'Строгое время в будни'),
                'weekend': _time(record['Строгое время в выходные'], SCHEDULE_SHEET, row, 'Строгое время в выходные'),
            },
        }
    missing = [club['name'] for club in clubs_by_id.values() if club['active'] and club['info']['is_physical'] and club['name'].casefold() not in seen]
    if missing:
        raise ConfigValidationError(f'{SCHEDULE_SHEET}: нет строк для клубов: {", ".join(missing)}')


def _action(value, sheet, row):
    label = _text(value).casefold()
    if label not in ACTION_LABELS:
        raise ConfigValidationError(f'{sheet}, строка {row}: «Действие» должно быть «Открытие» или «Закрытие»')
    return ACTION_LABELS[label]


def _variants(value, variant_count, sheet, row):
    raw = _text(value).casefold()
    if raw == ALL_VARIANTS:
        return list(range(variant_count))
    if len(raw) == 1 and 'a' <= raw <= 'z':
        variant = ord(raw) - ord('a')
        if variant < variant_count:
            return [variant]
    allowed = ', '.join(chr(ord('A') + index) for index in range(variant_count))
    raise ConfigValidationError(f'{sheet}, строка {row}: «Набор» должен быть «Все наборы» или одним из: {allowed}')


def _question_type(value, sheet, row):
    label = _text(value).casefold()
    if label not in QUESTION_TYPE_LABELS:
        raise ConfigValidationError(f'{sheet}, строка {row}: «Формат ответа» должен быть «Текст», «Фото» или «Число»')
    return QUESTION_TYPE_LABELS[label]


def _apply_questions(clubs_by_id, values):
    records = _records(values, QUESTION_HEADERS, QUESTIONS_SHEET)
    by_name = _clubs_by_name(clubs_by_id)
    positions = set()
    exact_questions = set()
    grouped_questions = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    grouped_checklists = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for record in records:
        row = record['_row']
        if not _bool(record['Используется'], QUESTIONS_SHEET, row, 'Используется'):
            continue
        name = _text(record['Клуб'])
        club = by_name.get(name.casefold())
        if not club or not club['active']:
            raise ConfigValidationError(f'{QUESTIONS_SHEET}, строка {row}: неизвестный активный клуб {name!r}')
        if club['variant_count'] == 0:
            raise ConfigValidationError(f'{QUESTIONS_SHEET}, строка {row}: для клуба {name!r} наборы вопросов отключены')
        action = _action(record['Действие'], QUESTIONS_SHEET, row)
        variants = _variants(record['Набор'], club['variant_count'], QUESTIONS_SHEET, row)
        order = _integer(record['Номер'], QUESTIONS_SHEET, row, 'Номер', 1)
        checklist = _text(record['Пункт чек-листа'])
        question = _text(record['Вопрос сотруднику'])
        if not checklist and not question:
            raise ConfigValidationError(f'{QUESTIONS_SHEET}, строка {row}: заполните пунк чек-листа, вопрос или оба поля')
        question_type = None
        if question:
            question_type = _question_type(record['Формат ответа'], QUESTIONS_SHEET, row)
        elif _text(record['Формат ответа']):
            raise ConfigValidationError(f'{QUESTIONS_SHEET}, строка {row}: формат ответа не нужен без вопроса')
        for variant in variants:
            if question:
                position_key = (club['id'], action, variant, order)
                if position_key in positions:
                    raise ConfigValidationError(
                        f'{QUESTIONS_SHEET}, строка {row}: номер {order} уже занят '
                        f'для {name}/{record["Действие"]}/набор {chr(65 + variant)}'
                    )
                positions.add(position_key)
                exact_key = (club['id'], action, variant, question_type, question.casefold())
                if exact_key in exact_questions:
                    raise ConfigValidationError(f'{QUESTIONS_SHEET}, строка {row}: вопрос продублирован в {name}/набор {chr(65 + variant)}')
                exact_questions.add(exact_key)
                grouped_questions[club['id']][action][variant].append((order, {'text': question, 'type': question_type}))
            if checklist:
                grouped_checklists[club['id']][action][variant].append((order, checklist))

    for club_id, club in clubs_by_id.items():
        if not club['active'] or club['variant_count'] == 0:
            continue
        questions = {}
        checklists = {}
        for action_code, action_name in ACTION_CODES.items():
            question_variants = []
            checklist_variants = []
            for variant in range(club['variant_count']):
                question_rows = sorted(grouped_questions[club_id][action_code][variant], key=lambda item: item[0])
                if not question_rows:
                    raise ConfigValidationError(f'{QUESTIONS_SHEET}: нет вопросов для {club["name"]}/{action_code}/набор {chr(65 + variant)}')
                question_variants.append([question for _, question in question_rows])
                checklist_rows = sorted(grouped_checklists[club_id][action_code][variant], key=lambda item: item[0])
                unique_items = []
                used_items = set()
                for _, text in checklist_rows:
                    key = text.casefold()
                    if key not in used_items:
                        used_items.add(key)
                        unique_items.append(text)
                checklist_variants.append(unique_items)
            questions[action_name] = question_variants
            checklists[action_name] = checklist_variants
        club['info']['questions'] = questions
        if any(items for variants in checklists.values() for items in variants):
            club['info']['checklists'] = checklists


def build_config(system_values, club_values, schedule_values, question_values):
    clubs_by_id = _parse_system(system_values)
    _apply_club_settings(clubs_by_id, club_values)
    _apply_schedules(clubs_by_id, schedule_values)
    _apply_questions(clubs_by_id, question_values)
    ordered = sorted(
        (club for club in clubs_by_id.values() if club['active']),
        key=lambda club: club['sort_order'],
    )
    return {club['name']: club['info'] for club in ordered}


def validation_values(message='Конфигурация проверена'):
    return [
        ['Параметр', 'Значение'],
        ['Статус', message],
        ['Последняя проверка', datetime.now().strftime('%Y-%m-%d %H:%M:%S')],
        ['Действия', 'Открытие, Закрытие'],
        ['Форматы ответа', 'Текст, Фото, Число'],
        ['Наборы', 'Все наборы или A, B, C'],
        ['Правило публикации', 'При любой ошибке clubs.json не изменяется'],
    ]


def _worksheet_map(spreadsheet):
    worksheets = {worksheet.title: worksheet for worksheet in spreadsheet.worksheets()}
    missing = [title for title in REQUIRED_SHEETS if title not in worksheets]
    if missing:
        raise ConfigValidationError(f'В таблице отсутствуют обязательные листы: {", ".join(missing)}')
    return worksheets


def _sheet_values(worksheet):
    return worksheet.get_all_values(include_tailing_empty=False, include_tailing_empty_rows=False)


def count_config(config):
    questions = 0
    checklists = 0
    for info in config.values():
        questions += sum(len(items) for variants in info.get('questions', {}).values() for items in variants)
        checklists += sum(len(items) for variants in info.get('checklists', {}).values() for items in variants)
    return len(config), questions, checklists


def config_diff(old, new):
    old_names = set(old)
    new_names = set(new)
    changed = sorted(name for name in old_names & new_names if old[name] != new[name])
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
            raise ConfigValidationError(f'{SYSTEM_SHEET}: нельзя менять название существующего клуба {old_name!r} на {new_name!r}: оно связано с историей и БД')
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
            raise ConfigValidationError(f'{SYSTEM_SHEET}: нельзя менять ClubID существующего клуба {name!r}')


def read_config(spreadsheet, current_config):
    worksheets = _worksheet_map(spreadsheet)
    try:
        config = build_config(
            _sheet_values(worksheets[SYSTEM_SHEET]),
            _sheet_values(worksheets[CLUBS_SHEET]),
            _sheet_values(worksheets[SCHEDULE_SHEET]),
            _sheet_values(worksheets[QUESTIONS_SHEET]),
        )
        validate_stable_identity(current_config, config)
    except ConfigValidationError as error:
        try:
            write_validation(worksheets[VALIDATION_SHEET], f'ОШИБКА: {error}')
        except Exception:
            pass
        raise
    return config, worksheets


def write_validation(worksheet, message):
    worksheet.update_values('A1', validation_values(message), extend=True)
