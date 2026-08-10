import html


LEGACY_GENERAL_TASK_TYPE = 'Вопрос/жалоба/предложение'
GENERAL_TASK_TYPE = 'Общее обращение'
REPAIR_TASK_TYPE = 'Ремонт'
BOT_TASK_TYPE = 'Улучшение бота'


_TYPE_COPY = {
    GENERAL_TASK_TYPE: {
        'created': '📩 <b>Новое общее обращение</b>',
        'solution': '👀 <b>Ответ по общему обращению</b>',
        'returned': '⚠️ <b>Общее обращение возвращено в работу</b>',
        'completed': '✅ <b>Общее обращение выполнено</b>',
        'confirmation': 'Готово, общее обращение добавлено!',
    },
    REPAIR_TASK_TYPE: {
        'created': '🛠 <b>Новая заявка на ремонт</b>',
        'solution': '👀 <b>Решение по заявке на ремонт</b>',
        'returned': '⚠️ <b>Заявка на ремонт возвращена в работу</b>',
        'completed': '✅ <b>Заявка на ремонт выполнена</b>',
        'confirmation': 'Готово, заявка на ремонт добавлена!',
    },
    BOT_TASK_TYPE: {
        'created': '🤖 <b>Новое предложение по улучшению бота</b>',
        'solution': '👀 <b>Ответ по улучшению бота</b>',
        'returned': '⚠️ <b>Улучшение бота возвращено в работу</b>',
        'completed': '✅ <b>Предложение по улучшению бота выполнено</b>',
        'confirmation': 'Готово, предложение по улучшению бота добавлено!',
    },
}


def _copy(task_type):
    normalized = GENERAL_TASK_TYPE if task_type == LEGACY_GENERAL_TASK_TYPE else task_type
    return _TYPE_COPY.get(normalized, {
        'created': '📩 <b>Новое обращение</b>',
        'solution': '👀 <b>Ответ по обращению</b>',
        'returned': '⚠️ <b>Обращение возвращено в работу</b>',
        'completed': '✅ <b>Обращение выполнено</b>',
        'confirmation': 'Готово, обращение добавлено!',
    })


def _task_identity(task_type, club, title):
    normalized = GENERAL_TASK_TYPE if task_type == LEGACY_GENERAL_TASK_TYPE else task_type
    title_icon = {
        GENERAL_TASK_TYPE: '💬',
        REPAIR_TASK_TYPE: '🔧',
        BOT_TASK_TYPE: '🤖',
    }.get(normalized, '📌')
    safe_club = html.escape(str(club or '').strip())
    safe_title = html.escape(str(title or '').strip())
    return f"📍 <b>{safe_club}</b>\n{title_icon} <b>{safe_title}</b>"


def created_task_notification(task_type, club, title, description):
    copy = _copy(task_type)
    identity = _task_identity(task_type, club, title)
    safe_description = html.escape(str(description or '')[:800])
    full = (
        f"{copy['created']}\n\n"
        f"{identity}\n\n"
        f"📝 <b>Описание:</b>\n{safe_description}"
    )
    short = f"{copy['created']}\n\n{identity}"
    return full, short, copy['confirmation']


def progress_task_notification(event, task_type, club, title, message):
    if event not in {'solution', 'returned', 'completed'}:
        raise ValueError('Неизвестное событие обращения')
    copy = _copy(task_type)
    identity = _task_identity(task_type, club, title)
    safe_message = html.escape(str(message or ''))
    label = 'Ответ' if event == 'solution' else 'Причина возврата'
    tail = (
        '\n\n👉 <b>Проверьте и подтвердите выполнение на доске задач!</b>'
        if event == 'solution' else ''
    )
    full = f"{copy[event]}\n\n{identity}"
    short = full
    if event != 'completed':
        full += f"\n\n💬 <b>{label}:</b>\n{safe_message}"
        short += f"\n\n💬 <i>{safe_message}</i>{tail}"
    return full, short
