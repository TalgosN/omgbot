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
        'confirmation': 'Готово, общее обращение добавлено!',
    },
    REPAIR_TASK_TYPE: {
        'created': '🛠 <b>Новая заявка на ремонт</b>',
        'solution': '👀 <b>Решение по заявке на ремонт</b>',
        'returned': '⚠️ <b>Заявка на ремонт возвращена в работу</b>',
        'confirmation': 'Готово, заявка на ремонт добавлена!',
    },
    BOT_TASK_TYPE: {
        'created': '🤖 <b>Новое предложение по улучшению бота</b>',
        'solution': '👀 <b>Ответ по улучшению бота</b>',
        'returned': '⚠️ <b>Улучшение бота возвращено в работу</b>',
        'confirmation': 'Готово, предложение по улучшению бота добавлено!',
    },
}


def _copy(task_type):
    normalized = GENERAL_TASK_TYPE if task_type == LEGACY_GENERAL_TASK_TYPE else task_type
    return _TYPE_COPY.get(normalized, {
        'created': '📩 <b>Новое обращение</b>',
        'solution': '👀 <b>Ответ по обращению</b>',
        'returned': '⚠️ <b>Обращение возвращено в работу</b>',
        'confirmation': 'Готово, обращение добавлено!',
    })


def created_task_notification(task_type, club, title, description):
    copy = _copy(task_type)
    safe_club = html.escape(str(club or ''))
    safe_title = html.escape(str(title or '').strip())
    safe_description = html.escape(str(description or '')[:800])
    full = (
        f"{copy['created']}\n\n"
        f"🏢 <b>Клуб:</b> {safe_club}\n"
        f"📌 <b>Тема:</b> {safe_title}\n\n"
        f"📝 <b>Описание:</b>\n{safe_description}"
    )
    short = f"{copy['created']}: {safe_title}"
    return full, short, copy['confirmation']


def progress_task_notification(event, task_type, club, title, message):
    if event not in {'solution', 'returned'}:
        raise ValueError('Неизвестное событие обращения')
    copy = _copy(task_type)
    safe_club = html.escape(str(club or ''))
    safe_title = html.escape(str(title or '').strip())
    safe_message = html.escape(str(message or ''))
    label = 'Ответ' if event == 'solution' else 'Причина возврата'
    tail = (
        '\n\n👉 <b>Проверьте и подтвердите выполнение на доске задач!</b>'
        if event == 'solution' else ''
    )
    full = (
        f"{copy[event]}\n\n"
        f"🏢 <b>Клуб:</b> {safe_club}\n"
        f"📌 <b>Тема:</b> {safe_title}\n\n"
        f"💬 <b>{label}:</b>\n{safe_message}"
    )
    short = (
        f"{copy[event]}: {safe_title}\n"
        f"💬 <i>{safe_message}</i>{tail}"
    )
    return full, short
