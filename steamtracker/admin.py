"""Административное управление промо внутри Виарыча."""

import threading
from datetime import datetime
from html import escape, unescape

from permissions import ROLE_MANAGER, ROLE_OWNER, require_role

from .config import Settings
from .db import TrackerStorage
from .llm import build_generator
from .management import CatalogManagementService
from .promo import DryRunPublisher, PromotionWorkflow
from .sheets import GoogleSheetsManager
from .store import SteamStoreClient
from .weekly import MOSCOW, WeeklyPromotionService, week_period


CALLBACK_PREFIX = "stpa"
PAGE_SIZE = 8
_context_messages: dict[int, set[int]] = {}
_context_lock = threading.Lock()
_promotion_action_locks: dict[int, threading.Lock] = {}
_promotion_action_registry_lock = threading.Lock()

STATUS_LABELS = {
    "draft": "⚪ Черновик",
    "review": "🟡 На согласовании",
    "approved": "🟢 Согласовано",
    "postponed": "⏸ Отложено",
    "cancelled": "🗄 Отменено",
}
CHANNEL_LABELS = {
    "employees": "👥 Сотрудники",
    "telegram": "✈️ Telegram",
    "vk": "🔵 VK",
}
OUTBOX_STATUS_LABELS = {
    "pending": "⏳ ожидает",
    "ready_dry_run": "🧪 dry-run готов",
    "sent": "✅ отправлено",
    "error": "❌ ошибка",
}


def _telegram_types():
    from telebot import types

    return types


def _runtime() -> tuple[Settings, TrackerStorage]:
    settings = Settings.from_env()
    storage = TrackerStorage(settings.db_path)
    storage.initialize()
    return settings, storage


def _workflow(
    settings: Settings,
    storage: TrackerStorage,
) -> PromotionWorkflow:
    if settings.publish_mode != "dry_run":
        raise RuntimeError(
            "Админ-раздел промо пока разрешён только в PUBLISH_MODE=dry_run"
        )
    return PromotionWorkflow(
        storage,
        build_generator(settings),
        DryRunPublisher(),
    )


def _weekly_service(
    settings: Settings,
    storage: TrackerStorage,
) -> WeeklyPromotionService:
    return WeeklyPromotionService(
        storage,
        _workflow(settings, storage),
        GoogleSheetsManager(settings),
    )


def _sync_promotion_best_effort(
    settings: Settings,
    storage: TrackerStorage,
    promotion_id: int,
) -> str | None:
    try:
        GoogleSheetsManager(settings).sync_promotion(
            storage,
            promotion_id,
            apply=True,
        )
    except Exception as error:
        return f"Google Sheets не обновлён: {error}"
    return None


def _message_id(message) -> int | None:
    value = getattr(message, "message_id", None)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _remember_context_message(chat_id: int, message) -> None:
    message_id = _message_id(message)
    if message_id is None:
        return
    with _context_lock:
        _context_messages.setdefault(int(chat_id), set()).add(message_id)


def _delete_message(chat_id: int, message_id: int, bot) -> None:
    try:
        bot.delete_message(chat_id, message_id)
    except Exception:
        pass


def _promotion_action_lock(promotion_id: int) -> threading.Lock:
    with _promotion_action_registry_lock:
        return _promotion_action_locks.setdefault(
            promotion_id,
            threading.Lock(),
        )


def _clear_context(
    chat_id: int,
    bot,
    *,
    source_message=None,
) -> None:
    with _context_lock:
        message_ids = _context_messages.pop(int(chat_id), set())
    source_id = _message_id(source_message)
    if source_id is not None:
        message_ids.add(source_id)
    for message_id in message_ids:
        _delete_message(chat_id, message_id, bot)


def _send_context_message(
    chat_id: int,
    bot,
    text: str,
    *,
    reply_markup=None,
    parse_mode: str | None = None,
    source_message=None,
):
    _clear_context(chat_id, bot, source_message=source_message)
    message = bot.send_message(
        chat_id,
        text,
        parse_mode=parse_mode,
        reply_markup=reply_markup,
    )
    _remember_context_message(chat_id, message)
    return message


def _send_context_photo(
    chat_id: int,
    bot,
    photo: str,
    *,
    caption: str,
    reply_markup=None,
    parse_mode: str | None = None,
    source_message=None,
):
    _clear_context(chat_id, bot, source_message=source_message)
    message = bot.send_photo(
        chat_id,
        photo=photo,
        caption=caption,
        parse_mode=parse_mode,
        reply_markup=reply_markup,
    )
    _remember_context_message(chat_id, message)
    return message


def _hide_reply_keyboard(chat_id: int, bot) -> None:
    types = _telegram_types()
    message = bot.send_message(
        chat_id,
        "Открываю раздел промо…",
        reply_markup=types.ReplyKeyboardRemove(),
    )
    message_id = _message_id(message)
    if message_id is not None:
        _delete_message(chat_id, message_id, bot)


def promotion_admin_menu(message, bot):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    _hide_reply_keyboard(message.chat.id, bot)
    show_promo_plane_selector(message, bot)


def show_promo_plane_selector(message, bot, *, source_message=None):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    types = _telegram_types()
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton(
            "🟢 Рабочие промо",
            callback_data=f"{CALLBACK_PREFIX}:plane:real",
        ),
        types.InlineKeyboardButton(
            "🧪 Тестовая зона",
            callback_data=f"{CALLBACK_PREFIX}:plane:test",
        ),
        types.InlineKeyboardButton(
            "⬅️ Админ-панель",
            callback_data=f"{CALLBACK_PREFIX}:admin:0",
        ),
    )
    _send_context_message(
        message.chat.id,
        bot,
        (
            "📣 <b>Управление промо</b>\n\n"
            "Выберите пространство работы. Рабочие и тестовые "
            "промо полностью разделены."
        ),
        parse_mode="HTML",
        reply_markup=markup,
        source_message=source_message,
    )


def _actor_id(update) -> str:
    if isinstance(update, (int, str)) and str(update).lstrip("-").isdigit():
        return str(update)
    user = getattr(update, "from_user", None)
    user_id = getattr(user, "id", None)
    if user_id is None or getattr(user, "is_bot", False):
        message = getattr(update, "message", update)
        chat = getattr(message, "chat", None)
        user_id = getattr(chat, "id", None)
    return str(user_id or "")


def _actor_name(update, user) -> str:
    keys = set(user.keys()) if hasattr(user, "keys") else set()
    for field in ("login", "nick_name", "first_name"):
        if field in keys and user[field]:
            return str(user[field])
    telegram_user = getattr(update, "from_user", None)
    username = getattr(telegram_user, "username", None)
    if username:
        return f"@{username}"
    return _actor_id(update)


def _is_owner(user) -> bool:
    return int(user["status"]) >= ROLE_OWNER


def _claim_required(storage, promotion_id: int, update, user) -> dict:
    row = dict(storage.promotion_admin_row(promotion_id))
    current_actor = _actor_id(update)
    if not row.get("claimed_by"):
        raise ValueError("Сначала нажмите «Взять в работу»")
    if str(row["claimed_by"]) != current_actor:
        if _is_owner(user):
            raise ValueError(
                "Промо занято другим сотрудником. "
                "Сначала нажмите «Перехватить»."
            )
        raise ValueError(
            f"Промо уже в работе у "
            f"{row.get('claimed_name') or row['claimed_by']}"
        )
    return row


def show_real_dashboard(message, bot, *, source_message=None):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    _, storage = _runtime()
    today = datetime.now(MOSCOW).date().isoformat()
    current = storage.current_promotion(today)
    types = _telegram_types()
    markup = types.InlineKeyboardMarkup(row_width=1)
    lines = ["🟢 <b>Рабочие промо</b>", ""]
    if current is None:
        lines.append("Игра недели ещё не создана.")
    else:
        lines.extend(
            [
                f"Текущее промо: <b>#{current['id']}</b>",
                f"Игра: <b>{escape(str(current['steam_name']))}</b>",
                (
                    "Статус: "
                    f"{STATUS_LABELS.get(current['status'], current['status'])}"
                ),
            ]
        )
        markup.add(
            types.InlineKeyboardButton(
                "⭐ Открыть игру недели",
                callback_data=f"{CALLBACK_PREFIX}:open:{current['id']}",
            )
        )
    markup.add(
        types.InlineKeyboardButton(
            "➕ Создать рабочее промо",
            callback_data=f"{CALLBACK_PREFIX}:create:real",
        )
    )
    markup.row(
        types.InlineKeyboardButton(
            "📚 История",
            callback_data=f"{CALLBACK_PREFIX}:history:real",
        ),
        types.InlineKeyboardButton(
            "⚙️ Настройки",
            callback_data=f"{CALLBACK_PREFIX}:settings:0",
        ),
    )
    markup.add(
        types.InlineKeyboardButton(
            "⬅️ Выбор пространства",
            callback_data=f"{CALLBACK_PREFIX}:menu:0",
        )
    )
    _send_context_message(
        message.chat.id,
        bot,
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=markup,
        source_message=source_message,
    )


def show_test_dashboard(
    message,
    bot,
    *,
    source_message=None,
    notice: str | None = None,
):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    _, storage = _runtime()
    tests = storage.list_promotions(is_test=True, limit=1)
    types = _telegram_types()
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton(
            "🧪 Создать тестовый вариант",
            callback_data=f"{CALLBACK_PREFIX}:createtest:0",
        ),
        types.InlineKeyboardButton(
            "📚 Тестовые варианты",
            callback_data=f"{CALLBACK_PREFIX}:list:test:0",
        ),
        types.InlineKeyboardButton(
            "⬅️ Выбор пространства",
            callback_data=f"{CALLBACK_PREFIX}:menu:0",
        ),
    )
    text = (
        "🧪 <b>Тестовая зона</b>\n\n"
        "Тестовые промо не участвуют в рабочей ротации и их нельзя "
        "согласовать.\n"
        f"Записи: {'есть' if tests else 'пока нет'}."
    )
    if notice:
        text = f"{escape(notice)}\n\n{text}"
    _send_context_message(
        message.chat.id,
        bot,
        text,
        parse_mode="HTML",
        reply_markup=markup,
        source_message=source_message,
    )


def show_history_menu(message, bot, *, source_message=None):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    types = _telegram_types()
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton(
            "🟡 В работе",
            callback_data=f"{CALLBACK_PREFIX}:list:review:0",
        ),
        types.InlineKeyboardButton(
            "🟢 Согласованные",
            callback_data=f"{CALLBACK_PREFIX}:list:approved:0",
        ),
    )
    markup.add(
        types.InlineKeyboardButton(
            "🗄 Архив",
            callback_data=f"{CALLBACK_PREFIX}:list:archive:0",
        ),
        types.InlineKeyboardButton(
            "⬅️ Рабочие промо",
            callback_data=f"{CALLBACK_PREFIX}:plane:real",
        ),
    )
    _send_context_message(
        message.chat.id,
        bot,
        "📚 <b>История рабочих промо</b>\n\nВыберите состояние.",
        parse_mode="HTML",
        reply_markup=markup,
        source_message=source_message,
    )


def show_create_real_menu(message, bot, *, source_message=None):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    types = _telegram_types()
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton(
            "🎲 Случайная игра недели",
            callback_data=f"{CALLBACK_PREFIX}:createweekly:0",
        ),
        types.InlineKeyboardButton(
            "🎮 Выбрать игру по AppID",
            callback_data=f"{CALLBACK_PREFIX}:requestappid:0",
        ),
        types.InlineKeyboardButton(
            "⬅️ Рабочие промо",
            callback_data=f"{CALLBACK_PREFIX}:plane:real",
        ),
    )
    _send_context_message(
        message.chat.id,
        bot,
        "➕ <b>Создание рабочего промо</b>\n\nВыберите способ.",
        parse_mode="HTML",
        reply_markup=markup,
        source_message=source_message,
    )


def _card_markup(row: dict, *, actor_id: str, is_owner: bool):
    types = _telegram_types()
    promotion_id = int(row["id"])
    status = row["status"]
    is_test = bool(row["is_test"])
    prefix = CALLBACK_PREFIX
    markup = types.InlineKeyboardMarkup(row_width=2)
    claimed_by = str(row.get("claimed_by") or "")
    is_claimant = claimed_by == actor_id
    mutable = status in {"draft", "review"}

    if any(
        row.get(field)
        for field in ("employee_text", "telegram_text", "vk_text")
    ):
        markup.add(
            types.InlineKeyboardButton(
                "👁 Посмотреть тексты",
                callback_data=f"{prefix}:texts:{promotion_id}",
            )
        )

    if mutable and not claimed_by:
        markup.add(
            types.InlineKeyboardButton(
                "🙋 Взять в работу",
                callback_data=f"{prefix}:claim:{promotion_id}",
            )
        )
    elif mutable and not is_claimant:
        if is_owner:
            markup.add(
                types.InlineKeyboardButton(
                    "🛡 Перехватить",
                    callback_data=f"{prefix}:takeover:{promotion_id}",
                )
            )
        else:
            markup.add(
                types.InlineKeyboardButton(
                    "👤 Уже в работе",
                    callback_data=f"{prefix}:noop:{promotion_id}",
                )
            )

    if mutable and is_claimant:
        if status == "draft":
            markup.add(
                types.InlineKeyboardButton(
                    "✨ Сгенерировать",
                    callback_data=f"{prefix}:generate:{promotion_id}",
                )
            )
        elif not is_test:
            markup.add(
                types.InlineKeyboardButton(
                    "✅ Согласовать",
                    callback_data=f"{prefix}:approve:{promotion_id}",
                )
            )
        markup.add(
            types.InlineKeyboardButton(
                "✏️ Изменить",
                callback_data=f"{prefix}:edit:{promotion_id}",
            ),
            types.InlineKeyboardButton(
                "⋯ Ещё",
                callback_data=f"{prefix}:more:{promotion_id}",
            ),
        )
    elif row.get("outbox_total"):
        markup.add(
            types.InlineKeyboardButton(
                "⋯ Подробнее",
                callback_data=f"{prefix}:more:{promotion_id}",
            )
        )

    markup.add(
        types.InlineKeyboardButton(
            "⬅️ Тестовая зона" if is_test else "⬅️ Рабочие промо",
            callback_data=(
                f"{prefix}:plane:test"
                if is_test
                else f"{prefix}:plane:real"
            ),
        ),
    )
    return markup


def _promotion_caption(row: dict) -> str:
    test_label = "\n🧪 <b>Тестовый вариант</b>" if row["is_test"] else ""
    image_source = {
        "steam": "Steam",
        "manager": "менеджер",
        "none": "нет",
    }.get(row.get("image_source"), "нет")
    if row["outbox_total"]:
        delivery = (
            f"{row['outbox_ready']}/{row['outbox_total']} готово"
            + (
                f", ошибок: {row['outbox_errors']}"
                if row["outbox_errors"]
                else ""
            )
        )
    else:
        delivery = "ещё не создана"
    period = (
        f"{row['valid_from'] or '—'} — {row['valid_to'] or '—'}"
    )
    claimant = (
        escape(str(row.get("claimed_name") or row.get("claimed_by")))
        if row.get("claimed_by")
        else "не назначен"
    )
    return (
        f"📣 <b>Промо #{row['id']}</b>{test_label}\n\n"
        f"🎮 <b>{escape(str(row['steam_name']))}</b>\n"
        f"AppID: <code>{row['app_id']}</code>\n"
        f"Статус: {STATUS_LABELS.get(row['status'], row['status'])}\n"
        f"Период: {escape(period)}\n"
        f"Акция: {escape(str(row['discount_text']))}\n"
        f"Ответственный: {claimant}\n"
        f"Изображение: {image_source}\n"
        f"Отправка: {delivery}"
    )


def send_promotion_card(
    chat_id,
    promotion_id: int,
    bot,
    *,
    update=None,
    user=None,
    notice: str | None = None,
    source_message=None,
):
    _, storage = _runtime()
    row = dict(storage.promotion_admin_row(promotion_id))
    caption = _promotion_caption(row)
    if notice:
        caption = f"{escape(notice)}\n\n{caption}"
    actor = update if update is not None else chat_id
    user = user or require_role(actor, bot, ROLE_MANAGER)
    if not user:
        return
    markup = _card_markup(
        row,
        actor_id=_actor_id(actor),
        is_owner=_is_owner(user),
    )
    image_url = row.get("publish_image_url")
    if image_url:
        try:
            _send_context_photo(
                chat_id,
                bot,
                photo=image_url,
                caption=caption,
                parse_mode="HTML",
                reply_markup=markup,
                source_message=source_message,
            )
            return
        except Exception:
            pass
    _send_context_message(
        chat_id,
        bot,
        caption,
        parse_mode="HTML",
        reply_markup=markup,
        source_message=source_message,
    )


def show_current_promotion(message, bot, *, source_message=None):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    _, storage = _runtime()
    today = datetime.now(MOSCOW).date().isoformat()
    row = storage.current_promotion(today)
    if row is None:
        show_real_dashboard(
            message,
            bot,
            source_message=source_message,
        )
        return
    send_promotion_card(
        message.chat.id,
        int(row["id"]),
        bot,
        update=message,
        source_message=source_message,
    )


def _list_scope(scope: str):
    if scope == "review":
        return False, ("draft", "review"), "На согласовании"
    if scope == "approved":
        return False, ("approved",), "Согласованные"
    if scope == "archive":
        return False, ("postponed", "cancelled"), "Архив"
    if scope == "test":
        return True, None, "Тестовые варианты"
    return False, None, "Все промо"


def show_promotion_list(
    message,
    bot,
    *,
    scope: str,
    page: int,
    source_message=None,
):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    _, storage = _runtime()
    is_test, statuses, title = _list_scope(scope)
    offset = max(page, 0) * PAGE_SIZE
    rows = storage.list_promotions(
        is_test=is_test,
        statuses=statuses,
        limit=PAGE_SIZE + 1,
        offset=offset,
    )
    has_next = len(rows) > PAGE_SIZE
    rows = rows[:PAGE_SIZE]
    types = _telegram_types()
    markup = types.InlineKeyboardMarkup(row_width=1)
    for row in rows:
        status = STATUS_LABELS.get(row["status"], row["status"])
        label = (
            f"#{row['id']} · {row['steam_name']} · "
            f"{status.split(' ', 1)[0]}"
        )
        markup.add(
            types.InlineKeyboardButton(
                label[:60],
                callback_data=f"{CALLBACK_PREFIX}:open:{row['id']}",
            )
        )
    filters = types.InlineKeyboardButton
    navigation = []
    if page > 0:
        navigation.append(
            filters(
                "⬅️",
                callback_data=f"{CALLBACK_PREFIX}:list:{scope}:{page - 1}",
            )
        )
    if has_next:
        navigation.append(
            filters(
                "➡️",
                callback_data=f"{CALLBACK_PREFIX}:list:{scope}:{page + 1}",
            )
        )
    if navigation:
        markup.row(*navigation)
    if is_test:
        markup.add(
            filters(
                "🧪 Создать тестовый вариант",
                callback_data=f"{CALLBACK_PREFIX}:createtest:0",
            ),
            filters(
                "⬅️ Тестовая зона",
                callback_data=f"{CALLBACK_PREFIX}:plane:test",
            ),
        )
    else:
        markup.row(
            filters(
                "🟡 В работе",
                callback_data=f"{CALLBACK_PREFIX}:list:review:0",
            ),
            filters(
                "🟢 Согласованные",
                callback_data=f"{CALLBACK_PREFIX}:list:approved:0",
            ),
        )
        markup.add(
            filters(
                "🗄 Архив",
                callback_data=f"{CALLBACK_PREFIX}:list:archive:0",
            ),
            filters(
                "⬅️ История",
                callback_data=f"{CALLBACK_PREFIX}:history:real",
            ),
        )
    text = (
        f"📋 <b>{escape(title)}</b>\n"
        f"Страница {page + 1}."
    )
    if not rows:
        text += "\n\nЗаписей нет."
    _send_context_message(
        message.chat.id,
        bot,
        text,
        parse_mode="HTML",
        reply_markup=markup,
        source_message=source_message,
    )


def create_weekly_promotion(
    message,
    bot,
    *,
    update=None,
    user=None,
    source_message=None,
):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    wait = _send_context_message(
        message.chat.id,
        bot,
        "⏳ Формирую игру недели...",
        source_message=source_message,
    )
    try:
        settings, storage = _runtime()
        result = _weekly_service(settings, storage).run(force=True)
        send_promotion_card(
            message.chat.id,
            result.promotion_id,
            bot,
            update=update or message,
            user=user,
            notice=f"✅ Промо #{result.promotion_id} готово.",
            source_message=wait,
        )
    except Exception as error:
        types = _telegram_types()
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton(
                "⬅️ К созданию",
                callback_data=f"{CALLBACK_PREFIX}:create:real",
            )
        )
        _send_context_message(
            message.chat.id,
            bot,
            f"❌ Не удалось создать промо: {error}",
            reply_markup=markup,
            source_message=wait,
        )


def _promotion_defaults(settings: Settings) -> tuple[str, str, str]:
    values = GoogleSheetsManager(settings).read_tracker_settings()
    discount = str(values.get("weekly_discount") or "").strip()
    if not discount:
        raise ValueError("В настройках не заполнена скидка")
    start, end = week_period()
    return discount, start.isoformat(), end.isoformat()


def request_manual_promotion_app_id(
    message,
    bot,
    *,
    source_message=None,
):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    types = _telegram_types()
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton(
            "Отмена",
            callback_data=f"{CALLBACK_PREFIX}:create:real",
        )
    )
    prompt = _send_context_message(
        message.chat.id,
        bot,
        "Введите Steam AppID игры одним сообщением.",
        reply_markup=markup,
        source_message=source_message,
    )
    bot.register_next_step_handler(prompt, create_manual_promotion, bot)


def create_manual_promotion(message, bot):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    message_id = _message_id(message)
    if message_id is not None:
        _delete_message(message.chat.id, message_id, bot)
    if message.text == "Отмена":
        show_create_real_menu(message, bot)
        return
    try:
        app_id = int(str(message.text).strip())
    except (TypeError, ValueError):
        types = _telegram_types()
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton(
                "⬅️ К созданию",
                callback_data=f"{CALLBACK_PREFIX}:create:real",
            )
        )
        _send_context_message(
            message.chat.id,
            bot,
            "❌ AppID должен состоять из цифр.",
            reply_markup=markup,
        )
        return
    wait = _send_context_message(
        message.chat.id,
        bot,
        "⏳ Создаю промо...",
    )
    try:
        settings, storage = _runtime()
        discount, valid_from, valid_to = _promotion_defaults(settings)
        promotion_id = storage.create_promotion(
            app_id=app_id,
            discount_text=discount,
            valid_from=valid_from,
            valid_to=valid_to,
            manager_comment=None,
            image_url=None,
        )
        _workflow(settings, storage).generate(promotion_id)
        warning = _sync_promotion_best_effort(
            settings,
            storage,
            promotion_id,
        )
        text = f"✅ Промо #{promotion_id} готово."
        if warning:
            text += f"\n⚠️ {warning}"
        send_promotion_card(
            message.chat.id,
            promotion_id,
            bot,
            update=message,
            notice=text,
            source_message=wait,
        )
    except Exception as error:
        types = _telegram_types()
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton(
                "⬅️ К созданию",
                callback_data=f"{CALLBACK_PREFIX}:create:real",
            )
        )
        _send_context_message(
            message.chat.id,
            bot,
            f"❌ Не удалось создать промо: {error}",
            reply_markup=markup,
            source_message=wait,
        )


def create_test_promotion(
    message,
    bot,
    *,
    update=None,
    user=None,
    source_message=None,
):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    wait = _send_context_message(
        message.chat.id,
        bot,
        "⏳ Создаю тестовый вариант...",
        source_message=source_message,
    )
    try:
        settings, storage = _runtime()
        discount, valid_from, valid_to = _promotion_defaults(settings)
        promotion_id = storage.create_promotion(
            app_id=storage.random_approved_game_id(),
            discount_text=discount,
            valid_from=valid_from,
            valid_to=valid_to,
            manager_comment="Тестовая генерация из админ-панели",
            image_url=None,
            is_test=True,
        )
        _workflow(settings, storage).generate(promotion_id)
        warning = _sync_promotion_best_effort(
            settings,
            storage,
            promotion_id,
        )
        text = f"✅ Тестовое промо #{promotion_id} готово."
        if warning:
            text += f"\n⚠️ {warning}"
        send_promotion_card(
            message.chat.id,
            promotion_id,
            bot,
            update=update or message,
            user=user,
            notice=text,
            source_message=wait,
        )
    except Exception as error:
        types = _telegram_types()
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton(
                "⬅️ В тестовую зону",
                callback_data=f"{CALLBACK_PREFIX}:plane:test",
            )
        )
        _send_context_message(
            message.chat.id,
            bot,
            f"❌ Не удалось создать тест: {error}",
            reply_markup=markup,
            source_message=wait,
        )


def show_text_menu(
    message,
    promotion_id: int,
    bot,
    *,
    source_message=None,
) -> None:
    if not require_role(message, bot, ROLE_MANAGER):
        return
    types = _telegram_types()
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton(
            "👥 Сотрудникам",
            callback_data=f"{CALLBACK_PREFIX}:viewemployee:{promotion_id}",
        ),
        types.InlineKeyboardButton(
            "✈️ Telegram",
            callback_data=f"{CALLBACK_PREFIX}:viewtelegram:{promotion_id}",
        ),
        types.InlineKeyboardButton(
            "🔵 VK",
            callback_data=f"{CALLBACK_PREFIX}:viewvk:{promotion_id}",
        ),
        types.InlineKeyboardButton(
            "⬅️ К карточке",
            callback_data=f"{CALLBACK_PREFIX}:open:{promotion_id}",
        ),
    )
    _send_context_message(
        message.chat.id,
        bot,
        f"👁 <b>Тексты промо #{promotion_id}</b>\n\nВыберите текст.",
        parse_mode="HTML",
        reply_markup=markup,
        source_message=source_message,
    )


def show_promotion_text(
    message,
    promotion_id: int,
    channel: str,
    bot,
    *,
    source_message=None,
) -> None:
    if not require_role(message, bot, ROLE_MANAGER):
        return
    _, storage = _runtime()
    row = dict(storage.promotion_admin_row(promotion_id))
    config = {
        "employee": ("👥 <b>СОТРУДНИКАМ</b>", "employee_text", "HTML"),
        "telegram": ("✈️ <b>TELEGRAM</b>", "telegram_text", "HTML"),
        "vk": ("🔵 VK", "vk_text", None),
    }
    title, field, parse_mode = config[channel]
    body = str(row.get(field) or "Текст пока не создан.")
    if len(body) > 3800:
        body = body[:3800] + "\n\n[Текст сокращён для просмотра в Telegram]"
    types = _telegram_types()
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton(
            "⬅️ К текстам",
            callback_data=f"{CALLBACK_PREFIX}:texts:{promotion_id}",
        ),
        types.InlineKeyboardButton(
            "📣 К карточке",
            callback_data=f"{CALLBACK_PREFIX}:open:{promotion_id}",
        ),
    )
    _send_context_message(
        message.chat.id,
        bot,
        f"{title}\n\n{body}",
        parse_mode=parse_mode,
        reply_markup=markup,
        source_message=source_message,
    )


def _edit_markup(promotion_id: int):
    types = _telegram_types()
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton(
            "👥 Текст сотрудникам",
            callback_data=f"{CALLBACK_PREFIX}:edemployee:{promotion_id}",
        ),
        types.InlineKeyboardButton(
            "✈️ Текст Telegram",
            callback_data=f"{CALLBACK_PREFIX}:edtelegram:{promotion_id}",
        ),
        types.InlineKeyboardButton(
            "🔵 Текст VK",
            callback_data=f"{CALLBACK_PREFIX}:edvk:{promotion_id}",
        ),
        types.InlineKeyboardButton(
            "👥 Переделать сотрудникам",
            callback_data=f"{CALLBACK_PREFIX}:regenemployee:{promotion_id}",
        ),
        types.InlineKeyboardButton(
            "📣 Переделать анонсы",
            callback_data=f"{CALLBACK_PREFIX}:regensocial:{promotion_id}",
        ),
        types.InlineKeyboardButton(
            "🔄 Переделать всё",
            callback_data=f"{CALLBACK_PREFIX}:regenall:{promotion_id}",
        ),
        types.InlineKeyboardButton(
            "⬅️ К карточке",
            callback_data=f"{CALLBACK_PREFIX}:open:{promotion_id}",
        ),
    )
    return markup


def show_edit_menu(
    message,
    promotion_id: int,
    bot,
    *,
    source_message=None,
) -> None:
    if not require_role(message, bot, ROLE_MANAGER):
        return
    _send_context_message(
        message.chat.id,
        bot,
        f"✏️ <b>Изменение промо #{promotion_id}</b>\n\nВыберите действие.",
        parse_mode="HTML",
        reply_markup=_edit_markup(promotion_id),
        source_message=source_message,
    )


def show_more_menu(
    message,
    promotion_id: int,
    bot,
    *,
    update=None,
    user=None,
    source_message=None,
) -> None:
    user = user or require_role(message, bot, ROLE_MANAGER)
    if not user:
        return
    _, storage = _runtime()
    row = dict(storage.promotion_admin_row(promotion_id))
    actor = _actor_id(update or message)
    is_claimant = str(row.get("claimed_by") or "") == actor
    is_test = bool(row["is_test"])
    mutable = row["status"] in {"draft", "review"}
    types = _telegram_types()
    markup = types.InlineKeyboardMarkup(row_width=2)
    if mutable and is_claimant:
        if not is_test and row.get("cycle_number"):
            markup.add(
                types.InlineKeyboardButton(
                    "🎲 Другая игра",
                    callback_data=f"{CALLBACK_PREFIX}:replace:{promotion_id}",
                )
            )
        if not is_test:
            markup.row(
                types.InlineKeyboardButton(
                    "⏸ Отложить",
                    callback_data=(
                        f"{CALLBACK_PREFIX}:confirmpostpone:{promotion_id}"
                    ),
                ),
                types.InlineKeyboardButton(
                    "🗑 Отменить",
                    callback_data=(
                        f"{CALLBACK_PREFIX}:confirmcancel:{promotion_id}"
                    ),
                ),
            )
            if _is_owner(user) and not row.get("outbox_total"):
                markup.add(
                    types.InlineKeyboardButton(
                        "🧪 Пометить тестовым",
                        callback_data=(
                            f"{CALLBACK_PREFIX}:confirmmarktest:{promotion_id}"
                        ),
                    )
                )
        elif _is_owner(user):
            markup.add(
                types.InlineKeyboardButton(
                    "🗑 Удалить тест",
                    callback_data=(
                        f"{CALLBACK_PREFIX}:confirmdelete:{promotion_id}"
                    ),
                )
            )
        markup.add(
            types.InlineKeyboardButton(
                "🔓 Освободить",
                callback_data=f"{CALLBACK_PREFIX}:release:{promotion_id}",
            )
        )
    if row.get("outbox_total"):
        markup.add(
            types.InlineKeyboardButton(
                "📦 Техническая очередь",
                callback_data=f"{CALLBACK_PREFIX}:outbox:{promotion_id}",
            )
        )
    markup.add(
        types.InlineKeyboardButton(
            "⬅️ К карточке",
            callback_data=f"{CALLBACK_PREFIX}:open:{promotion_id}",
        )
    )
    _send_context_message(
        message.chat.id,
        bot,
        f"⋯ <b>Дополнительные действия промо #{promotion_id}</b>",
        parse_mode="HTML",
        reply_markup=markup,
        source_message=source_message,
    )


def _request_manual_text(call, channel: str, bot) -> None:
    labels = {
        "employee": "текст для сотрудников",
        "telegram": "текст Telegram",
        "vk": "текст VK",
    }
    types = _telegram_types()
    markup = types.InlineKeyboardMarkup()
    promotion_id = int(call.data.rsplit(":", 1)[1])
    markup.add(
        types.InlineKeyboardButton(
            "Отмена",
            callback_data=f"{CALLBACK_PREFIX}:open:{promotion_id}",
        )
    )
    msg = _send_context_message(
        call.message.chat.id,
        bot,
        f"Отправьте новый {labels[channel]}. Название игры и скидка "
        "должны остаться без изменений.",
        reply_markup=markup,
        source_message=call.message,
    )
    bot.register_next_step_handler(
        msg,
        save_manual_text,
        promotion_id,
        channel,
        bot,
    )


def save_manual_text(message, promotion_id: int, channel: str, bot):
    user = require_role(message, bot, ROLE_MANAGER)
    if not user:
        return
    message_id = _message_id(message)
    if message_id is not None:
        _delete_message(message.chat.id, message_id, bot)
    if message.text == "Отмена":
        send_promotion_card(
            message.chat.id,
            promotion_id,
            bot,
            update=message,
            user=user,
            notice="Редактирование отменено.",
        )
        return
    text = str(message.text or "").strip()
    limits = {"employee": 3500, "telegram": 3500, "vk": 6000}
    if not text or len(text) > limits[channel]:
        send_promotion_card(
            message.chat.id,
            promotion_id,
            bot,
            update=message,
            user=user,
            notice=(
                f"❌ Текст должен содержать от 1 до "
                f"{limits[channel]} символов."
            ),
        )
        return
    settings, storage = _runtime()
    try:
        _claim_required(storage, promotion_id, message, user)
    except Exception as error:
        send_promotion_card(
            message.chat.id,
            promotion_id,
            bot,
            update=message,
            user=user,
            notice=f"❌ {error}",
        )
        return
    promotion = dict(storage.promotion_admin_row(promotion_id))
    plain_text = unescape(text)
    if promotion["steam_name"] not in plain_text:
        send_promotion_card(
            message.chat.id,
            promotion_id,
            bot,
            update=message,
            user=user,
            notice="❌ Название игры должно присутствовать без изменений.",
        )
        return
    if promotion["discount_text"] not in plain_text:
        send_promotion_card(
            message.chat.id,
            promotion_id,
            bot,
            update=message,
            user=user,
            notice="❌ Точное значение скидки должно присутствовать в тексте.",
        )
        return
    values = {
        "employee_text": text if channel == "employee" else None,
        "telegram_text": text if channel == "telegram" else None,
        "vk_text": text if channel == "vk" else None,
    }
    action_lock = _promotion_action_lock(promotion_id)
    if not action_lock.acquire(blocking=False):
        send_promotion_card(
            message.chat.id,
            promotion_id,
            bot,
            update=message,
            user=user,
            notice="❌ Действие с этим промо уже выполняется.",
        )
        return
    try:
        storage.save_partial_generated_texts(
            promotion_id,
            **values,
        )
        storage.record_generation(
            promotion_id,
            provider="manual",
            model=None,
            prompt_version="manual-admin-v1",
            input_data={
                "channel": channel,
                "editor": str(message.from_user.id),
            },
            output_data={channel: text},
        )
        warning = _sync_promotion_best_effort(
            settings,
            storage,
            promotion_id,
        )
        response = "✅ Текст сохранён."
        if warning:
            response += f"\n⚠️ {warning}"
    except Exception as error:
        response = f"❌ {error}"
    finally:
        action_lock.release()
    send_promotion_card(
        message.chat.id,
        promotion_id,
        bot,
        update=message,
        user=user,
        notice=response,
    )


def show_outbox(
    message,
    bot,
    *,
    promotion_id: int | None = None,
    source_message=None,
):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    _, storage = _runtime()
    rows = storage.outbox_admin_rows(limit=30)
    if promotion_id is not None:
        rows = [
            row for row in rows
            if int(row["promotion_id"]) == promotion_id
        ]
    if not rows:
        text = "📦 Очередь отправки пуста."
    else:
        lines = ["📦 <b>Очередь отправки</b>", ""]
        for row in rows:
            channel = CHANNEL_LABELS.get(row["channel"], row["channel"])
            status = OUTBOX_STATUS_LABELS.get(
                row["status"],
                row["status"],
            )
            lines.append(
                f"#{row['promotion_id']} · {channel}: {status}"
            )
            if row["error"]:
                lines.append(f"Ошибка: {escape(str(row['error'])[:250])}")
        text = "\n".join(lines)
    types = _telegram_types()
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton(
            "🔄 Обновить",
            callback_data=(
                f"{CALLBACK_PREFIX}:outbox:{promotion_id or 0}"
            ),
        ),
        types.InlineKeyboardButton(
            "⬅️ К карточке" if promotion_id else "⬅️ Настройки",
            callback_data=(
                f"{CALLBACK_PREFIX}:open:{promotion_id}"
                if promotion_id
                else f"{CALLBACK_PREFIX}:settings:0"
            ),
        ),
    )
    _send_context_message(
        message.chat.id,
        bot,
        text,
        parse_mode="HTML",
        reply_markup=markup,
        source_message=source_message,
    )


def show_catalog(
    message,
    bot,
    *,
    source_message=None,
    notice: str | None = None,
):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    settings, storage = _runtime()
    summary = storage.summary()
    rotation = storage.rotation_summary()
    types = _telegram_types()
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton(
            "📄 Открыть Google-таблицу",
            url=(
                "https://docs.google.com/spreadsheets/d/"
                f"{settings.spreadsheet_id}/edit"
            ),
        )
    )
    markup.add(
        types.InlineKeyboardButton(
            "🔄 Применить изменения листа «Игры»",
            callback_data=f"{CALLBACK_PREFIX}:synccatalog:0",
        )
    )
    markup.add(
        types.InlineKeyboardButton(
            "⬅️ Настройки",
            callback_data=f"{CALLBACK_PREFIX}:settings:0",
        )
    )
    body = (
        "🎮 <b>Каталог игр</b>\n\n"
        f"Согласовано: {summary['approved_games']}\n"
        f"Обогащено Steam: {summary['enriched_games']}\n"
        f"Цикл ротации: {rotation['cycle_number']}\n"
        f"Уже использовано: {rotation['used_games']}\n"
        f"Доступно: {rotation['available_games']}"
    )
    if notice:
        body = f"{escape(notice)}\n\n{body}"
    _send_context_message(
        message.chat.id,
        bot,
        body,
        parse_mode="HTML",
        reply_markup=markup,
        source_message=source_message,
    )


def show_promo_settings(
    message,
    bot,
    *,
    source_message=None,
    notice: str | None = None,
):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    settings, _ = _runtime()
    try:
        values = GoogleSheetsManager(settings).read_tracker_settings()
    except Exception as error:
        types = _telegram_types()
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton(
                "⬅️ Рабочие промо",
                callback_data=f"{CALLBACK_PREFIX}:plane:real",
            )
        )
        _send_context_message(
            message.chat.id,
            bot,
            f"❌ Не удалось прочитать настройки: {error}",
            reply_markup=markup,
            source_message=source_message,
        )
        return
    sheet_enabled = str(
        values.get("weekly_promo_enabled") or ""
    ).strip().casefold() in {"1", "true", "yes", "on", "да"}
    fully_enabled = settings.weekly_promo_enabled and sheet_enabled
    types = _telegram_types()
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton(
            "💰 Изменить скидку",
            callback_data=f"{CALLBACK_PREFIX}:editdiscount:0",
        ),
        types.InlineKeyboardButton(
            (
                "⏸ Выключить автогенерацию"
                if sheet_enabled
                else "▶️ Включить автогенерацию"
            ),
            callback_data=f"{CALLBACK_PREFIX}:toggleweekly:0",
        ),
        types.InlineKeyboardButton(
            "🎮 Каталог игр",
            callback_data=f"{CALLBACK_PREFIX}:catalog:0",
        ),
        types.InlineKeyboardButton(
            "📦 Техническая очередь",
            callback_data=f"{CALLBACK_PREFIX}:outbox:0",
        ),
        types.InlineKeyboardButton(
            "⬅️ Рабочие промо",
            callback_data=f"{CALLBACK_PREFIX}:plane:real",
        ),
    )
    body = (
        "⚙️ <b>Настройки промо</b>\n\n"
        f"Скидка: {escape(values.get('weekly_discount', 'не задана'))}\n"
        "Расписание сервера: понедельник, 10:30 МСК\n"
        f"Разрешено на сервере: "
        f"{'да' if settings.weekly_promo_enabled else 'нет'}\n"
        f"Включено менеджером: {'да' if sheet_enabled else 'нет'}\n"
        f"Итог: {'🟢 включено' if fully_enabled else '⏸ выключено'}\n"
        f"Режим: "
        f"{'автоматический' if fully_enabled else 'ручной'}\n"
        f"Генератор: {escape(settings.generator_provider)}\n"
        "Публикация: dry-run"
    )
    if notice:
        body = f"{escape(notice)}\n\n{body}"
    _send_context_message(
        message.chat.id,
        bot,
        body,
        parse_mode="HTML",
        reply_markup=markup,
        source_message=source_message,
    )


def _confirmation_markup(
    promotion_id: int,
    action: str,
    confirm_text: str,
):
    types = _telegram_types()
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton(
            confirm_text,
            callback_data=f"{CALLBACK_PREFIX}:{action}:{promotion_id}",
        ),
        types.InlineKeyboardButton(
            "Нет",
            callback_data=f"{CALLBACK_PREFIX}:open:{promotion_id}",
        ),
    )
    return markup


def _request_discount(call, bot):
    types = _telegram_types()
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton(
            "Отмена",
            callback_data=f"{CALLBACK_PREFIX}:settings:0",
        )
    )
    msg = _send_context_message(
        call.message.chat.id,
        bot,
        "Введите точный текст скидки, например: 100 рублей",
        reply_markup=markup,
        source_message=call.message,
    )
    bot.register_next_step_handler(msg, save_discount, bot)


def save_discount(message, bot):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    message_id = _message_id(message)
    if message_id is not None:
        _delete_message(message.chat.id, message_id, bot)
    if message.text == "Отмена":
        show_promo_settings(message, bot)
        return
    value = str(message.text or "").strip()
    if not value or len(value) > 80:
        types = _telegram_types()
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton(
                "⬅️ К настройкам",
                callback_data=f"{CALLBACK_PREFIX}:settings:0",
            )
        )
        _send_context_message(
            message.chat.id,
            bot,
            "Скидка должна содержать от 1 до 80 символов.",
            reply_markup=markup,
        )
        return
    try:
        settings, _ = _runtime()
        GoogleSheetsManager(settings).update_tracker_setting(
            "weekly_discount",
            value,
        )
        notice = "✅ Скидка обновлена."
    except Exception as error:
        notice = f"❌ {error}"
    show_promo_settings(message, bot, notice=notice)


def _regenerate(
    call,
    bot,
    promotion_id: int,
    section: str,
    user,
) -> None:
    settings, storage = _runtime()
    _claim_required(storage, promotion_id, call, user)
    wait = _send_context_message(
        call.message.chat.id,
        bot,
        "⏳ Генерирую новый вариант…",
        source_message=call.message,
    )
    _workflow(settings, storage).regenerate(
        promotion_id,
        section=section,
    )
    warning = _sync_promotion_best_effort(
        settings,
        storage,
        promotion_id,
    )
    text = "✅ Новый вариант готов."
    if warning:
        text += f"\n⚠️ {warning}"
    send_promotion_card(
        call.message.chat.id,
        promotion_id,
        bot,
        update=call,
        user=user,
        notice=text,
        source_message=wait,
    )


def _clear_next_step_handler(chat_id: int, bot) -> None:
    try:
        bot.clear_step_handler_by_chat_id(chat_id)
    except Exception:
        pass


def _show_callback_error(call, bot, error: Exception) -> None:
    parts = str(call.data or "").split(":")
    promotion_id = (
        int(parts[2])
        if len(parts) > 2 and parts[2].isdigit()
        else None
    )
    types = _telegram_types()
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton(
            "⬅️ К карточке" if promotion_id else "⬅️ В меню промо",
            callback_data=(
                f"{CALLBACK_PREFIX}:open:{promotion_id}"
                if promotion_id
                else f"{CALLBACK_PREFIX}:menu:0"
            ),
        )
    )
    _send_context_message(
        call.message.chat.id,
        bot,
        f"❌ Ошибка промо: {error}",
        reply_markup=markup,
        source_message=call.message,
    )


def register_promo_admin_callbacks(bot) -> None:
    @bot.callback_query_handler(
        func=lambda call: bool(
            call.data
            and call.data.startswith(f"{CALLBACK_PREFIX}:")
        )
    )
    def promo_admin_callback(call):
        user = require_role(call, bot, ROLE_MANAGER)
        if not user:
            return
        parts = call.data.split(":")
        action = parts[1]
        chat_id = call.message.chat.id
        action_lock = None
        try:
            bot.answer_callback_query(call.id)
            if action in {
                "admin",
                "menu",
                "plane",
                "history",
                "create",
                "settings",
                "catalog",
                "list",
                "open",
            }:
                _clear_next_step_handler(chat_id, bot)

            if action == "admin":
                _clear_context(
                    chat_id,
                    bot,
                    source_message=call.message,
                )
                from menu import admin_menu

                admin_menu(call.message, bot)
                return
            if action == "menu":
                show_promo_plane_selector(
                    call.message,
                    bot,
                    source_message=call.message,
                )
                return
            if action == "plane":
                if parts[2] == "test":
                    show_test_dashboard(
                        call.message,
                        bot,
                        source_message=call.message,
                    )
                else:
                    show_real_dashboard(
                        call.message,
                        bot,
                        source_message=call.message,
                    )
                return
            if action == "history":
                show_history_menu(
                    call.message,
                    bot,
                    source_message=call.message,
                )
                return
            if action == "create":
                show_create_real_menu(
                    call.message,
                    bot,
                    source_message=call.message,
                )
                return
            if action == "createtest":
                create_test_promotion(
                    call.message,
                    bot,
                    update=call,
                    user=user,
                    source_message=call.message,
                )
                return
            if action == "requestappid":
                request_manual_promotion_app_id(
                    call.message,
                    bot,
                    source_message=call.message,
                )
                return
            if action == "settings":
                show_promo_settings(
                    call.message,
                    bot,
                    source_message=call.message,
                )
                return
            if action == "catalog":
                show_catalog(
                    call.message,
                    bot,
                    source_message=call.message,
                )
                return
            if action == "list":
                show_promotion_list(
                    call.message,
                    bot,
                    scope=parts[2],
                    page=int(parts[3]),
                    source_message=call.message,
                )
                return

            promotion_id = int(parts[2])
            if action in {
                "claim",
                "takeover",
                "release",
                "generate",
                "approve",
                "regenall",
                "regenemployee",
                "regensocial",
                "replace",
                "postpone",
                "cancel",
                "marktest",
                "delete",
            }:
                action_lock = _promotion_action_lock(promotion_id)
                if not action_lock.acquire(blocking=False):
                    action_lock = None
                    raise ValueError(
                        "Действие с этим промо уже выполняется"
                    )
            if action == "open":
                send_promotion_card(
                    chat_id,
                    promotion_id,
                    bot,
                    update=call,
                    user=user,
                    source_message=call.message,
                )
            elif action == "noop":
                send_promotion_card(
                    chat_id,
                    promotion_id,
                    bot,
                    update=call,
                    user=user,
                    notice="Промо уже взято в работу другим сотрудником.",
                    source_message=call.message,
                )
            elif action == "claim":
                settings, storage = _runtime()
                storage.claim_promotion(
                    promotion_id,
                    claimed_by=_actor_id(call),
                    claimed_name=_actor_name(call, user),
                )
                warning = _sync_promotion_best_effort(
                    settings,
                    storage,
                    promotion_id,
                )
                notice = "✅ Промо взято в работу."
                if warning:
                    notice += f"\n⚠️ {warning}"
                send_promotion_card(
                    chat_id,
                    promotion_id,
                    bot,
                    update=call,
                    user=user,
                    notice=notice,
                    source_message=call.message,
                )
            elif action == "takeover":
                if not _is_owner(user):
                    raise PermissionError(
                        "Перехват доступен только руководству"
                    )
                settings, storage = _runtime()
                storage.claim_promotion(
                    promotion_id,
                    claimed_by=_actor_id(call),
                    claimed_name=_actor_name(call, user),
                    force=True,
                )
                warning = _sync_promotion_best_effort(
                    settings,
                    storage,
                    promotion_id,
                )
                notice = "🛡 Промо передано вам."
                if warning:
                    notice += f"\n⚠️ {warning}"
                send_promotion_card(
                    chat_id,
                    promotion_id,
                    bot,
                    update=call,
                    user=user,
                    notice=notice,
                    source_message=call.message,
                )
            elif action == "release":
                settings, storage = _runtime()
                storage.release_promotion_claim(
                    promotion_id,
                    actor_id=_actor_id(call),
                    force=_is_owner(user),
                )
                warning = _sync_promotion_best_effort(
                    settings,
                    storage,
                    promotion_id,
                )
                notice = "🔓 Промо освобождено."
                if warning:
                    notice += f"\n⚠️ {warning}"
                send_promotion_card(
                    chat_id,
                    promotion_id,
                    bot,
                    update=call,
                    user=user,
                    notice=notice,
                    source_message=call.message,
                )
            elif action == "texts":
                show_text_menu(
                    call.message,
                    promotion_id,
                    bot,
                    source_message=call.message,
                )
            elif action in {"viewemployee", "viewtelegram", "viewvk"}:
                channel = {
                    "viewemployee": "employee",
                    "viewtelegram": "telegram",
                    "viewvk": "vk",
                }[action]
                show_promotion_text(
                    call.message,
                    promotion_id,
                    channel,
                    bot,
                    source_message=call.message,
                )
            elif action == "generate":
                settings, storage = _runtime()
                _claim_required(storage, promotion_id, call, user)
                wait = _send_context_message(
                    chat_id,
                    bot,
                    "⏳ Генерирую тексты…",
                    source_message=call.message,
                )
                _workflow(settings, storage).generate(promotion_id)
                warning = _sync_promotion_best_effort(
                    settings,
                    storage,
                    promotion_id,
                )
                notice = "✅ Тексты готовы."
                if warning:
                    notice += f"\n⚠️ {warning}"
                send_promotion_card(
                    chat_id,
                    promotion_id,
                    bot,
                    update=call,
                    user=user,
                    notice=notice,
                    source_message=wait,
                )
            elif action == "approve":
                settings, storage = _runtime()
                _claim_required(storage, promotion_id, call, user)
                _workflow(settings, storage).approve_and_dispatch(
                    promotion_id,
                    approved_by=_actor_id(call),
                )
                warning = _sync_promotion_best_effort(
                    settings,
                    storage,
                    promotion_id,
                )
                notice = (
                    "✅ Промо согласовано. Создана только dry-run очередь."
                )
                if warning:
                    notice += f"\n⚠️ {warning}"
                send_promotion_card(
                    chat_id,
                    promotion_id,
                    bot,
                    update=call,
                    user=user,
                    notice=notice,
                    source_message=call.message,
                )
            elif action == "edit":
                _, storage = _runtime()
                _claim_required(storage, promotion_id, call, user)
                show_edit_menu(
                    call.message,
                    promotion_id,
                    bot,
                    source_message=call.message,
                )
            elif action in {"edemployee", "edtelegram", "edvk"}:
                _, storage = _runtime()
                _claim_required(storage, promotion_id, call, user)
                channel = {
                    "edemployee": "employee",
                    "edtelegram": "telegram",
                    "edvk": "vk",
                }[action]
                _request_manual_text(call, channel, bot)
            elif action == "regenall":
                _regenerate(call, bot, promotion_id, "all", user)
            elif action == "regenemployee":
                _regenerate(call, bot, promotion_id, "employee", user)
            elif action == "regensocial":
                _regenerate(call, bot, promotion_id, "social", user)
            elif action == "more":
                show_more_menu(
                    call.message,
                    promotion_id,
                    bot,
                    update=call,
                    user=user,
                    source_message=call.message,
                )
            elif action == "replace":
                settings, storage = _runtime()
                _claim_required(storage, promotion_id, call, user)
                replacement = _weekly_service(
                    settings,
                    storage,
                ).replace(promotion_id)
                storage.claim_promotion(
                    replacement.promotion_id,
                    claimed_by=_actor_id(call),
                    claimed_name=_actor_name(call, user),
                )
                send_promotion_card(
                    chat_id,
                    replacement.promotion_id,
                    bot,
                    update=call,
                    user=user,
                    notice=(
                        "✅ Выбрано новое промо "
                        f"#{replacement.promotion_id}."
                    ),
                    source_message=call.message,
                )
            elif action in {
                "confirmpostpone",
                "confirmcancel",
                "confirmmarktest",
                "confirmdelete",
            }:
                _, storage = _runtime()
                _claim_required(storage, promotion_id, call, user)
                if action in {"confirmmarktest", "confirmdelete"}:
                    if not _is_owner(user):
                        raise PermissionError(
                            "Это действие доступно только руководству"
                        )
                configs = {
                    "confirmpostpone": (
                        "Отложить",
                        "postpone",
                        "Да, отложить",
                    ),
                    "confirmcancel": (
                        "Отменить",
                        "cancel",
                        "Да, отменить",
                    ),
                    "confirmmarktest": (
                        "Пометить тестовым",
                        "marktest",
                        "Да, это тест",
                    ),
                    "confirmdelete": (
                        "Полностью удалить тестовое",
                        "delete",
                        "Удалить навсегда",
                    ),
                }
                title, confirm_action, button_text = configs[action]
                _send_context_message(
                    chat_id,
                    bot,
                    f"{title} промо #{promotion_id}?",
                    reply_markup=_confirmation_markup(
                        promotion_id,
                        confirm_action,
                        button_text,
                    ),
                    source_message=call.message,
                )
            elif action in {"postpone", "cancel"}:
                settings, storage = _runtime()
                _claim_required(storage, promotion_id, call, user)
                target_status = (
                    "postponed" if action == "postpone" else "cancelled"
                )
                storage.set_promotion_status(promotion_id, target_status)
                warning = _sync_promotion_best_effort(
                    settings,
                    storage,
                    promotion_id,
                )
                notice = (
                    "⏸ Промо отложено."
                    if action == "postpone"
                    else "🗄 Промо отменено и сохранено в истории."
                )
                if warning:
                    notice += f"\n⚠️ {warning}"
                send_promotion_card(
                    chat_id,
                    promotion_id,
                    bot,
                    update=call,
                    user=user,
                    notice=notice,
                    source_message=call.message,
                )
            elif action == "marktest":
                if not _is_owner(user):
                    raise PermissionError(
                        "Это действие доступно только руководству"
                    )
                settings, storage = _runtime()
                _claim_required(storage, promotion_id, call, user)
                storage.mark_promotion_as_test(promotion_id)
                warning = _sync_promotion_best_effort(
                    settings,
                    storage,
                    promotion_id,
                )
                notice = "🧪 Промо помечено тестовым."
                if warning:
                    notice += f"\n⚠️ {warning}"
                send_promotion_card(
                    chat_id,
                    promotion_id,
                    bot,
                    update=call,
                    user=user,
                    notice=notice,
                    source_message=call.message,
                )
            elif action == "delete":
                if not _is_owner(user):
                    raise PermissionError(
                        "Это действие доступно только руководству"
                    )
                settings, storage = _runtime()
                _claim_required(storage, promotion_id, call, user)
                storage.delete_test_promotion(promotion_id)
                warning = None
                try:
                    GoogleSheetsManager(settings).remove_promotion(
                        promotion_id
                    )
                except Exception as error:
                    warning = f"строка Google не удалена: {error}"
                notice = f"🗑 Тестовое промо #{promotion_id} удалено."
                if warning:
                    notice += f"\n⚠️ {warning}"
                show_test_dashboard(
                    call.message,
                    bot,
                    source_message=call.message,
                    notice=notice,
                )
            elif action == "outbox":
                show_outbox(
                    call.message,
                    bot,
                    promotion_id=promotion_id or None,
                    source_message=call.message,
                )
            elif action == "createweekly":
                create_weekly_promotion(
                    call.message,
                    bot,
                    update=call,
                    user=user,
                    source_message=call.message,
                )
            elif action == "synccatalog":
                settings, storage = _runtime()
                wait = _send_context_message(
                    chat_id,
                    bot,
                    "⏳ Применяю изменения каталога…",
                    source_message=call.message,
                )
                manager = GoogleSheetsManager(settings)
                result = CatalogManagementService(
                    storage,
                    SteamStoreClient(),
                ).sync(
                    manager.read_catalog_rows(),
                    apply=True,
                )
                if result.errors:
                    raise ValueError("; ".join(result.errors))
                show_catalog(
                    call.message,
                    bot,
                    source_message=wait,
                    notice=(
                        "✅ Каталог применён: "
                        f"{result.active_games} активных, "
                        f"{result.excluded_games} исключено."
                    ),
                )
            elif action == "editdiscount":
                _request_discount(call, bot)
            elif action == "toggleweekly":
                if not _is_owner(user):
                    raise PermissionError(
                        "Это действие доступно только руководству"
                    )
                settings, _ = _runtime()
                manager = GoogleSheetsManager(settings)
                values = manager.read_tracker_settings()
                enabled = str(
                    values.get("weekly_promo_enabled") or ""
                ).strip().casefold() in {
                    "1",
                    "true",
                    "yes",
                    "on",
                    "да",
                }
                if not enabled and not settings.weekly_promo_enabled:
                    raise ValueError(
                        "Автоматический режим запрещён на сервере. "
                        "Сначала задайте "
                        "STEAMTRACKER_WEEKLY_PROMO_ENABLED=true "
                        "и перезапустите bot"
                    )
                manager.update_tracker_setting(
                    "weekly_promo_enabled",
                    "false" if enabled else "true",
                )
                show_promo_settings(
                    call.message,
                    bot,
                    source_message=call.message,
                    notice="✅ Настройка автогенерации обновлена.",
                )
            else:
                raise ValueError("Неизвестное действие промо")
        except Exception as error:
            _show_callback_error(call, bot, error)
        finally:
            if action_lock is not None:
                action_lock.release()
