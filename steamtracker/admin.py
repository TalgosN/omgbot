"""Административное управление промо внутри Виарыча."""

import hashlib
import threading
from datetime import datetime
from html import escape, unescape

import pytz
from permissions import ROLE_EMPLOYEE, ROLE_MANAGER, ROLE_OWNER, require_role

from .config import Settings
from .db import TrackerStorage
from .llm import build_generator
from .promo import DryRunPublisher, PromotionWorkflow
from .sheets import GoogleSheetsManager
from .steam import LicenseSyncService, SteamClient
from .store import SteamStoreClient
from .weekly import (
    MOSCOW,
    WeeklyPromotionService,
    setting_enabled,
    week_period,
)


CALLBACK_PREFIX = "stpa"
PAGE_SIZE = 8
REPORT_PAGE_SIZE = 5
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


def _sync_tracker_data_best_effort(
    settings: Settings,
    storage: TrackerStorage,
) -> str | None:
    if not settings.google_export_enabled:
        return None
    try:
        GoogleSheetsManager(settings).sync_tracker_data(
            storage,
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


def _send_context_photo_and_message(
    chat_id: int,
    bot,
    photo: str,
    text: str,
    *,
    reply_markup=None,
    parse_mode: str | None = None,
    source_message=None,
):
    _clear_context(chat_id, bot, source_message=source_message)
    try:
        photo_message = bot.send_photo(chat_id, photo=photo)
        _remember_context_message(chat_id, photo_message)
    except Exception:
        pass
    message = bot.send_message(
        chat_id,
        text,
        parse_mode=parse_mode,
        reply_markup=reply_markup,
    )
    _remember_context_message(chat_id, message)
    return message


def _hide_reply_keyboard(chat_id: int, bot) -> None:
    types = _telegram_types()
    message = bot.send_message(
        chat_id,
        "Открываю Steam Tracker…",
        reply_markup=types.ReplyKeyboardRemove(),
    )
    message_id = _message_id(message)
    if message_id is not None:
        _delete_message(chat_id, message_id, bot)


def promotion_admin_menu(message, bot):
    if not require_role(message, bot, ROLE_EMPLOYEE):
        return
    _hide_reply_keyboard(message.chat.id, bot)
    show_tracker_dashboard(message, bot)


def show_tracker_dashboard(
    message,
    bot,
    *,
    source_message=None,
    notice: str | None = None,
):
    user = require_role(message, bot, ROLE_EMPLOYEE)
    if not user:
        return
    types = _telegram_types()
    markup = types.InlineKeyboardMarkup(row_width=1)
    is_manager = int(user["status"]) >= ROLE_MANAGER
    if is_manager:
        markup.add(
            types.InlineKeyboardButton(
                "📣 Промо",
                callback_data=f"{CALLBACK_PREFIX}:menu:0",
            ),
            types.InlineKeyboardButton(
                "🕹 Каталог игр",
                callback_data=f"{CALLBACK_PREFIX}:catalog:all:0",
            ),
        )
    else:
        markup.add(
            types.InlineKeyboardButton(
                "🕹 Каталог игр",
                callback_data=f"{CALLBACK_PREFIX}:catalog:active:0",
            )
        )
    if _is_owner(user):
        markup.add(
            types.InlineKeyboardButton(
                "💻 ПК и аккаунты",
                callback_data=f"{CALLBACK_PREFIX}:accounts:0",
            ),
            types.InlineKeyboardButton(
                "⚙️ Настройки",
                callback_data=f"{CALLBACK_PREFIX}:settings:0",
            ),
            types.InlineKeyboardButton(
                "🔄 Синхронизация",
                callback_data=f"{CALLBACK_PREFIX}:syncmenu:0",
            ),
        )
    if is_manager:
        markup.add(
            types.InlineKeyboardButton(
                "📜 История изменений",
                callback_data=f"{CALLBACK_PREFIX}:audit:0",
            )
        )
    markup.add(
        types.InlineKeyboardButton(
            "⬅️ Главное меню",
            callback_data=f"{CALLBACK_PREFIX}:home:0",
        )
    )
    text = "🎮 <b>Steam Tracker</b>\n\n"
    if is_manager:
        text += "Управление каталогом, лицензиями и игрой недели."
    else:
        text += "Каталог игр и проверка наличия лицензий на ПК."
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
            "⬅️ Steam Tracker",
            callback_data=f"{CALLBACK_PREFIX}:tracker:0",
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


def _is_manager(user) -> bool:
    return int(user["status"]) >= ROLE_MANAGER


def _format_playtime_hours(minutes: int | None) -> str:
    hours = max(0, int(minutes or 0)) / 60
    if hours.is_integer():
        value = f"{int(hours):,}".replace(",", " ")
    else:
        value = f"{hours:,.1f}".replace(",", " ").replace(".", ",")
    return f"{value} ч"


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
    user = require_role(message, bot, ROLE_MANAGER)
    if not user:
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
    markup.add(
        types.InlineKeyboardButton(
            "📚 История",
            callback_data=f"{CALLBACK_PREFIX}:history:real",
        )
    )
    if _is_owner(user):
        markup.add(
            types.InlineKeyboardButton(
                "📦 Техническая очередь",
                callback_data=f"{CALLBACK_PREFIX}:outbox:0",
            )
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


def _promotion_defaults(storage: TrackerStorage) -> tuple[str, str, str]:
    values = storage.tracker_settings()
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
        discount, valid_from, valid_to = _promotion_defaults(storage)
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
        discount, valid_from, valid_to = _promotion_defaults(storage)
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
    status: str = "all",
    page: int = 0,
    source_message=None,
    notice: str | None = None,
):
    user = require_role(message, bot, ROLE_EMPLOYEE)
    if not user:
        return
    can_manage = _is_manager(user)
    if not can_manage:
        status = "active"
    _, storage = _runtime()
    query_status = None if status == "all" else status
    rows = storage.managed_games(
        status=query_status,
        limit=PAGE_SIZE + 1,
        offset=max(0, page) * PAGE_SIZE,
    )
    has_next = len(rows) > PAGE_SIZE
    rows = rows[:PAGE_SIZE]
    types = _telegram_types()
    markup = types.InlineKeyboardMarkup(row_width=2)
    if can_manage:
        markup.row(
            types.InlineKeyboardButton(
                "➕ Добавить",
                callback_data=f"{CALLBACK_PREFIX}:gadd:0",
            ),
            types.InlineKeyboardButton(
                "🔎 Найти",
                callback_data=f"{CALLBACK_PREFIX}:gsearch:0",
            ),
        )
        markup.add(
            types.InlineKeyboardButton(
                "📋 Отсутствующие лицензии",
                callback_data=f"{CALLBACK_PREFIX}:missingreport:0",
            )
        )
        markup.row(
            types.InlineKeyboardButton(
                "🟢 Активные",
                callback_data=f"{CALLBACK_PREFIX}:catalog:active:0",
            ),
            types.InlineKeyboardButton(
                "📝 Черновики",
                callback_data=f"{CALLBACK_PREFIX}:catalog:draft:0",
            ),
        )
        markup.row(
            types.InlineKeyboardButton(
                "⏸ На паузе",
                callback_data=f"{CALLBACK_PREFIX}:catalog:paused:0",
            ),
            types.InlineKeyboardButton(
                "🚫 Исключённые",
                callback_data=f"{CALLBACK_PREFIX}:catalog:excluded:0",
            ),
        )
        markup.row(
            types.InlineKeyboardButton(
                "⚠️ Без лицензии",
                callback_data=f"{CALLBACK_PREFIX}:catalog:no_license:0",
            ),
            types.InlineKeyboardButton(
                "📚 Все",
                callback_data=f"{CALLBACK_PREFIX}:catalog:all:0",
            ),
        )
    status_icons = {
        "active": "🟢",
        "draft": "📝",
        "paused": "⏸",
        "excluded": "🚫",
    }
    for row in rows:
        icon = status_icons.get(row["catalog_status"], "🎮")
        license_icon = "✅" if row["owned_count"] else "⚠️"
        markup.add(
            types.InlineKeyboardButton(
                (
                    f"{icon} {license_icon} "
                    f"{row['steam_name']} [{row['app_id']}]"
                )[:64],
                callback_data=f"{CALLBACK_PREFIX}:game:{row['app_id']}",
            )
        )
    navigation = []
    if page > 0:
        navigation.append(
            types.InlineKeyboardButton(
                "⬅️",
                callback_data=(
                    f"{CALLBACK_PREFIX}:catalog:{status}:{page - 1}"
                ),
            )
        )
    if has_next:
        navigation.append(
            types.InlineKeyboardButton(
                "➡️",
                callback_data=(
                    f"{CALLBACK_PREFIX}:catalog:{status}:{page + 1}"
                ),
            )
        )
    if navigation:
        markup.row(*navigation)
    markup.add(
        types.InlineKeyboardButton(
            "⬅️ Steam Tracker",
            callback_data=f"{CALLBACK_PREFIX}:tracker:0",
        )
    )
    filter_labels = {
        "all": "все",
        "active": "активные",
        "draft": "черновики",
        "paused": "на паузе",
        "excluded": "исключённые",
        "no_license": "без лицензии",
    }
    body = (
        "🕹 <b>Каталог игр</b>\n\n"
        f"Фильтр: {filter_labels.get(status, status)}\n"
        f"Страница: {page + 1}\n\n"
        "✅ означает, что лицензия найдена хотя бы на одном "
        "активном Steam-аккаунте."
    )
    if not can_manage:
        body = (
            "🕹 <b>Каталог игр</b>\n\n"
            f"Страница: {page + 1}\n\n"
            "Выберите игру, чтобы открыть карточку и проверить "
            "наличие на ПК."
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


def show_game_card(
    message,
    app_id: int,
    bot,
    *,
    source_message=None,
    notice: str | None = None,
):
    user = require_role(message, bot, ROLE_EMPLOYEE)
    if not user:
        return
    _, storage = _runtime()
    row = dict(storage.managed_game(app_id))
    can_manage = _is_manager(user)
    if not can_manage and row["catalog_status"] != "active":
        raise PermissionError("Сотрудникам доступны только активные игры")
    types = _telegram_types()
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton(
            "💻 Наличие на ПК",
            callback_data=f"{CALLBACK_PREFIX}:glicenses:{app_id}",
        )
    )
    if can_manage:
        markup.add(
            types.InlineKeyboardButton(
                "✏️ Изменить",
                callback_data=f"{CALLBACK_PREFIX}:geditmenu:{app_id}",
            ),
            types.InlineKeyboardButton(
                "⚙️ Статус",
                callback_data=f"{CALLBACK_PREFIX}:gstatusmenu:{app_id}",
            ),
            types.InlineKeyboardButton(
                "🔄 Обновить из Steam",
                callback_data=f"{CALLBACK_PREFIX}:grefresh:{app_id}",
            ),
        )
    markup.add(
        types.InlineKeyboardButton(
            "⬅️ Каталог",
            callback_data=f"{CALLBACK_PREFIX}:catalog:all:0",
        )
    )
    labels = {
        "active": "🟢 Активна",
        "draft": "📝 Черновик",
        "paused": "⏸ На паузе",
        "excluded": "🚫 Исключена",
    }
    description = (
        row.get("manager_description")
        or row.get("store_description")
        or row.get("base_description")
        or "не заполнено"
    )
    description = str(description)
    if len(description) > 2500:
        description = description[:2497] + "..."
    manager_comment = str(row.get("manager_comment") or "нет")
    if len(manager_comment) > 600:
        manager_comment = manager_comment[:597] + "..."
    body = (
        f"🎮 <b>{escape(row['steam_name'])}</b>\n"
        f"AppID: <code>{row['app_id']}</code>\n"
        f"Статус: {labels.get(row['catalog_status'], row['catalog_status'])}\n"
        f"Лицензии: {row['owned_count']}/{row['account_count']}\n"
        f"Игроков: {row['player_count'] or 'не указано'}\n"
        f"Наиграно во всех клубах: "
        f"{_format_playtime_hours(row.get('total_playtime_minutes'))}\n"
        f"Место по популярности: "
        f"{row.get('popularity_rank') or '—'}\n"
        f"Последнее промо: {row['last_promotion'] or 'не было'}\n\n"
        f"<b>Описание:</b>\n{escape(description)}\n\n"
        f"<b>Комментарий:</b> "
        f"{escape(manager_comment)}"
    )
    if notice:
        body = f"{escape(notice)}\n\n{body}"
    if row.get("header_image"):
        _send_context_photo_and_message(
            message.chat.id,
            bot,
            str(row["header_image"]),
            body,
            parse_mode="HTML",
            reply_markup=markup,
            source_message=source_message,
        )
    else:
        _send_context_message(
            message.chat.id,
            bot,
            body,
            parse_mode="HTML",
            reply_markup=markup,
            source_message=source_message,
        )


def show_game_edit_menu(
    message,
    app_id: int,
    bot,
    *,
    source_message=None,
):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    _, storage = _runtime()
    row = storage.managed_game(app_id)
    types = _telegram_types()
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton(
            "👥 Изменить количество игроков",
            callback_data=f"{CALLBACK_PREFIX}:geditplayers:{app_id}",
        ),
        types.InlineKeyboardButton(
            "📝 Изменить описание",
            callback_data=f"{CALLBACK_PREFIX}:geditdesc:{app_id}",
        ),
        types.InlineKeyboardButton(
            "💬 Изменить комментарий",
            callback_data=f"{CALLBACK_PREFIX}:geditcomment:{app_id}",
        ),
        types.InlineKeyboardButton(
            "⬅️ К игре",
            callback_data=f"{CALLBACK_PREFIX}:game:{app_id}",
        ),
    )
    _send_context_message(
        message.chat.id,
        bot,
        (
            f"✏️ <b>Изменить: {escape(row['steam_name'])}</b>\n\n"
            "Выберите поле."
        ),
        parse_mode="HTML",
        reply_markup=markup,
        source_message=source_message,
    )


def show_game_status_menu(
    message,
    app_id: int,
    bot,
    *,
    source_message=None,
):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    _, storage = _runtime()
    row = storage.managed_game(app_id)
    labels = {
        "active": "🟢 Активна",
        "draft": "📝 Черновик",
        "paused": "⏸ На паузе",
        "excluded": "🚫 Исключена",
    }
    options = [
        ("active", "🟢 Активировать"),
        ("draft", "📝 В черновики"),
        ("paused", "⏸ Поставить на паузу"),
        ("excluded", "🚫 Исключить"),
    ]
    types = _telegram_types()
    markup = types.InlineKeyboardMarkup(row_width=1)
    for status, label in options:
        if status == row["catalog_status"]:
            continue
        markup.add(
            types.InlineKeyboardButton(
                label,
                callback_data=(
                    f"{CALLBACK_PREFIX}:gstatus:{status}:{app_id}"
                ),
            )
        )
    markup.add(
        types.InlineKeyboardButton(
            "⬅️ К игре",
            callback_data=f"{CALLBACK_PREFIX}:game:{app_id}",
        )
    )
    _send_context_message(
        message.chat.id,
        bot,
        (
            f"⚙️ <b>Статус: {escape(row['steam_name'])}</b>\n\n"
            f"Сейчас: {labels.get(row['catalog_status'], row['catalog_status'])}"
        ),
        parse_mode="HTML",
        reply_markup=markup,
        source_message=source_message,
    )


def request_game_add(message, bot, *, source_message=None):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    types = _telegram_types()
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton(
            "Отмена",
            callback_data=f"{CALLBACK_PREFIX}:catalog:all:0",
        )
    )
    prompt = _send_context_message(
        message.chat.id,
        bot,
        "Введите Steam AppID новой игры.",
        reply_markup=markup,
        source_message=source_message,
    )
    bot.register_next_step_handler(prompt, save_game_add, bot)


def save_game_add(message, bot):
    user = require_role(message, bot, ROLE_MANAGER)
    if not user:
        return
    try:
        app_id = int(str(message.text or "").strip())
        if app_id <= 0:
            raise ValueError
    except (TypeError, ValueError):
        show_catalog(message, bot, notice="❌ AppID должен быть положительным числом.")
        return
    wait = _send_context_message(message.chat.id, bot, "⏳ Проверяю Steam Store…")
    try:
        settings, storage = _runtime()
        metadata = SteamStoreClient().get_metadata(app_id)
        storage.add_managed_game(
            app_id=app_id,
            steam_name=metadata.name,
            actor_id=_actor_id(message),
            actor_name=_actor_name(message, user),
        )
        storage.save_game_metadata(
            app_id,
            steam_name=metadata.name,
            store_description=metadata.description,
            genres=metadata.genres,
            categories=metadata.categories,
            header_image=metadata.header_image,
            screenshots=metadata.screenshots,
            is_free=metadata.is_free,
            source_language=metadata.source_language,
        )
        warning = _sync_tracker_data_best_effort(settings, storage)
        notice = "✅ Игра добавлена как черновик."
        if warning:
            notice += f"\n⚠️ {warning}"
        show_game_card(
            message,
            app_id,
            bot,
            source_message=wait,
            notice=notice,
        )
    except Exception as error:
        show_catalog(
            message,
            bot,
            source_message=wait,
            notice=f"❌ Не удалось добавить игру: {error}",
        )


def request_game_search(message, bot, *, source_message=None):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    prompt = _send_context_message(
        message.chat.id,
        bot,
        "Введите часть названия или AppID.",
        source_message=source_message,
    )
    bot.register_next_step_handler(prompt, show_game_search_results, bot)


def show_game_search_results(message, bot):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    query = str(message.text or "").strip()
    _, storage = _runtime()
    rows = storage.managed_games(search=query, limit=20)
    types = _telegram_types()
    markup = types.InlineKeyboardMarkup(row_width=1)
    for row in rows:
        markup.add(
            types.InlineKeyboardButton(
                f"{row['steam_name']} [{row['app_id']}]"[:64],
                callback_data=f"{CALLBACK_PREFIX}:game:{row['app_id']}",
            )
        )
    markup.add(
        types.InlineKeyboardButton(
            "⬅️ Каталог",
            callback_data=f"{CALLBACK_PREFIX}:catalog:all:0",
        )
    )
    _send_context_message(
        message.chat.id,
        bot,
        (
            f"🔎 <b>Поиск:</b> {escape(query)}\n\n"
            f"Найдено: {len(rows)}"
        ),
        parse_mode="HTML",
        reply_markup=markup,
    )


def request_game_field(call, bot, field: str, app_id: int):
    prompts = {
        "players": "Введите количество игроков целым числом.",
        "description": "Введите понятное описание игры для сотрудников.",
        "comment": "Введите внутренний комментарий менеджера. Для очистки отправьте -",
    }
    prompt = _send_context_message(
        call.message.chat.id,
        bot,
        prompts[field],
        source_message=call.message,
    )
    bot.register_next_step_handler(
        prompt,
        save_game_field,
        bot,
        field,
        app_id,
    )


def save_game_field(message, bot, field: str, app_id: int):
    user = require_role(message, bot, ROLE_MANAGER)
    if not user:
        return
    try:
        settings, storage = _runtime()
        row = dict(storage.managed_game(app_id))
        value = str(message.text or "").strip()
        player_count = row["player_count"]
        description = row["manager_description"]
        comment = row["manager_comment"]
        if field == "players":
            player_count = int(value)
            if player_count < 1:
                raise ValueError("Количество игроков должно быть не меньше 1")
        elif field == "description":
            if not value or len(value) > 4000:
                raise ValueError("Описание должно содержать от 1 до 4000 символов")
            description = value
        else:
            if len(value) > 1000:
                raise ValueError("Комментарий не должен превышать 1000 символов")
            comment = None if value == "-" else value
        storage.update_managed_game(
            app_id,
            player_count=player_count,
            manager_description=description,
            manager_comment=comment,
            actor_id=_actor_id(message),
            actor_name=_actor_name(message, user),
        )
        warning = _sync_tracker_data_best_effort(settings, storage)
        notice = "✅ Игра обновлена."
        if warning:
            notice += f"\n⚠️ {warning}"
    except Exception as error:
        notice = f"❌ {error}"
    show_game_card(message, app_id, bot, notice=notice)


def show_game_licenses(
    message,
    app_id: int,
    bot,
    *,
    source_message=None,
):
    user = require_role(message, bot, ROLE_EMPLOYEE)
    if not user:
        return
    _, storage = _runtime()
    game = storage.managed_game(app_id)
    if not _is_manager(user) and game["catalog_status"] != "active":
        raise PermissionError("Сотрудникам доступны только активные игры")
    rows = storage.missing_game_license_rows(app_id)
    lines = [f"💻 <b>{escape(game['steam_name'])}: наличие</b>", ""]
    if not game["account_count"]:
        lines.append("Активных Steam-аккаунтов пока нет.")
    elif not rows:
        lines.append(
            f"✅ Игра есть на всех {game['account_count']} активных ПК."
        )
    else:
        installed = int(game["account_count"]) - len(rows)
        lines.extend(
            [
                (
                    f"Есть на {installed} из "
                    f"{game['account_count']} активных ПК."
                ),
                "",
                "<b>Лицензия отсутствует:</b>",
            ]
        )
        for row in rows:
            lines.append(
                f"❌ {escape(row['club_name'] or 'Без клуба')} / "
                f"{escape(row['vanity_url'] or row['steam_id'])}"
            )
    types = _telegram_types()
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton(
            "⬅️ К игре",
            callback_data=f"{CALLBACK_PREFIX}:game:{app_id}",
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


def _club_token(club_name: str) -> str:
    return hashlib.sha256(club_name.encode("utf-8")).hexdigest()[:16]


def _missing_report_club(
    storage: TrackerStorage,
    token: str,
) -> str:
    for row in storage.missing_license_club_summary():
        if _club_token(row["club_name"]) == token:
            return str(row["club_name"])
    raise ValueError("Клуб из отчёта больше не найден")


def show_missing_license_report(
    message,
    bot,
    *,
    source_message=None,
):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    _, storage = _runtime()
    rows = storage.missing_license_club_summary()
    summary = storage.summary()
    problem_rows = [
        row for row in rows if int(row["missing_license_count"] or 0) > 0
    ]
    types = _telegram_types()
    markup = types.InlineKeyboardMarkup(row_width=1)
    lines = ["📋 <b>Отсутствующие лицензии</b>", ""]
    if not summary["accounts"]:
        lines.append("Активных Steam-аккаунтов пока нет.")
    elif not summary["approved_games"]:
        lines.append("Активных игр пока нет.")
    elif not problem_rows:
        lines.append(
            "✅ Все активные игры есть на всех активных ПК."
        )
    else:
        total = sum(
            int(row["missing_license_count"])
            for row in problem_rows
        )
        lines.extend(
            [
                f"Всего отсутствующих лицензий: <b>{total}</b>",
                "Выберите клуб, чтобы увидеть игры и ПК.",
            ]
        )
        for row in problem_rows:
            markup.add(
                types.InlineKeyboardButton(
                    (
                        f"🏢 {row['club_name']}: "
                        f"{row['missing_license_count']} "
                        f"({row['games_with_gaps']} игр)"
                    )[:64],
                    callback_data=(
                        f"{CALLBACK_PREFIX}:missingclub:"
                        f"{_club_token(row['club_name'])}:0"
                    ),
                )
            )
    markup.add(
        types.InlineKeyboardButton(
            "⬅️ Каталог",
            callback_data=f"{CALLBACK_PREFIX}:catalog:all:0",
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


def show_missing_license_club(
    message,
    token: str,
    page: int,
    bot,
    *,
    source_message=None,
):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    _, storage = _runtime()
    club_name = _missing_report_club(storage, token)
    rows = storage.missing_license_rows_for_club(
        club_name,
        limit=REPORT_PAGE_SIZE + 1,
        offset=max(0, page) * REPORT_PAGE_SIZE,
    )
    has_next = len(rows) > REPORT_PAGE_SIZE
    rows = rows[:REPORT_PAGE_SIZE]
    lines = [
        f"🏢 <b>{escape(club_name)}</b>",
        "Отсутствующие лицензии активных игр:",
        "",
    ]
    types = _telegram_types()
    markup = types.InlineKeyboardMarkup(row_width=1)
    if not rows:
        lines.append("✅ Пропусков больше нет.")
    for row in rows:
        zones = str(row["missing_zones"] or "")
        if len(zones) > 500:
            zones = zones[:497] + "..."
        lines.extend(
            [
                f"<b>{escape(row['steam_name'])}</b>",
                f"Нет на ПК: {escape(zones)}",
                "",
            ]
        )
        markup.add(
            types.InlineKeyboardButton(
                f"🎮 {row['steam_name']}"[:64],
                callback_data=f"{CALLBACK_PREFIX}:game:{row['app_id']}",
            )
        )
    navigation = []
    if page > 0:
        navigation.append(
            types.InlineKeyboardButton(
                "⬅️",
                callback_data=(
                    f"{CALLBACK_PREFIX}:missingclub:{token}:{page - 1}"
                ),
            )
        )
    if has_next:
        navigation.append(
            types.InlineKeyboardButton(
                "➡️",
                callback_data=(
                    f"{CALLBACK_PREFIX}:missingclub:{token}:{page + 1}"
                ),
            )
        )
    if navigation:
        markup.row(*navigation)
    markup.add(
        types.InlineKeyboardButton(
            "⬅️ Ко всем клубам",
            callback_data=f"{CALLBACK_PREFIX}:missingreport:0",
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


def show_promo_settings(
    message,
    bot,
    *,
    source_message=None,
    notice: str | None = None,
):
    user = require_role(message, bot, ROLE_OWNER)
    if not user:
        return
    settings, storage = _runtime()
    values = storage.tracker_settings()
    bot_enabled = setting_enabled(
        values.get("weekly_promo_enabled") or ""
    )
    fully_enabled = settings.weekly_promo_enabled and bot_enabled
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
                if bot_enabled
                else "▶️ Включить автогенерацию"
            ),
            callback_data=f"{CALLBACK_PREFIX}:toggleweekly:0",
        ),
        types.InlineKeyboardButton(
            "📅 Сменить день",
            callback_data=f"{CALLBACK_PREFIX}:cycleday:0",
        ),
        types.InlineKeyboardButton(
            "🕥 Изменить время",
            callback_data=f"{CALLBACK_PREFIX}:edittime:0",
        ),
        types.InlineKeyboardButton(
            "🌍 Изменить часовой пояс",
            callback_data=f"{CALLBACK_PREFIX}:edittimezone:0",
        ),
        types.InlineKeyboardButton(
            "📦 Техническая очередь",
            callback_data=f"{CALLBACK_PREFIX}:outbox:0",
        ),
        types.InlineKeyboardButton(
            "⬅️ Steam Tracker",
            callback_data=f"{CALLBACK_PREFIX}:tracker:0",
        ),
    )
    body = (
        "⚙️ <b>Настройки Steam Tracker</b>\n\n"
        f"Скидка: {escape(values.get('weekly_discount', 'не задана'))}\n"
        f"День: {escape(values.get('generation_day', 'не задан'))}\n"
        f"Время: {escape(values.get('generation_time', 'не задано'))}\n"
        f"Часовой пояс: {escape(values.get('timezone', 'не задан'))}\n\n"
        f"Главный выключатель сервера: "
        f"{'да' if settings.weekly_promo_enabled else 'нет'}\n"
        f"Включено в боте: {'да' if bot_enabled else 'нет'}\n"
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
    user = require_role(message, bot, ROLE_OWNER)
    if not user:
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
        settings, storage = _runtime()
        storage.update_tracker_setting(
            "weekly_discount",
            value,
            actor_id=_actor_id(message),
            actor_name=_actor_name(message, user),
        )
        warning = _sync_tracker_data_best_effort(settings, storage)
        notice = "✅ Скидка обновлена."
        if warning:
            notice += f"\n⚠️ {warning}"
    except Exception as error:
        notice = f"❌ {error}"
    show_promo_settings(message, bot, notice=notice)


def request_tracker_setting(call, bot, key: str):
    prompts = {
        "generation_time": "Введите время запуска в формате ЧЧ:ММ.",
        "timezone": (
            "Введите часовой пояс IANA, например Europe/Moscow."
        ),
    }
    prompt = _send_context_message(
        call.message.chat.id,
        bot,
        prompts[key],
        source_message=call.message,
    )
    bot.register_next_step_handler(
        prompt,
        save_tracker_setting,
        bot,
        key,
    )


def save_tracker_setting(message, bot, key: str):
    user = require_role(message, bot, ROLE_OWNER)
    if not user:
        return
    value = str(message.text or "").strip()
    try:
        if key == "generation_time":
            datetime.strptime(value, "%H:%M")
        elif key == "timezone":
            pytz.timezone(value)
        settings, storage = _runtime()
        storage.update_tracker_setting(
            key,
            value,
            actor_id=_actor_id(message),
            actor_name=_actor_name(message, user),
        )
        warning = _sync_tracker_data_best_effort(settings, storage)
        notice = "✅ Настройка обновлена."
        if warning:
            notice += f"\n⚠️ {warning}"
    except Exception as error:
        notice = f"❌ {error}"
    show_promo_settings(message, bot, notice=notice)


def show_accounts(
    message,
    bot,
    *,
    page: int = 0,
    source_message=None,
    notice: str | None = None,
):
    if not require_role(message, bot, ROLE_OWNER):
        return
    _, storage = _runtime()
    all_rows = storage.managed_accounts()
    start = max(0, page) * PAGE_SIZE
    rows = all_rows[start : start + PAGE_SIZE]
    types = _telegram_types()
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton(
            "➕ Добавить Steam-аккаунт",
            callback_data=f"{CALLBACK_PREFIX}:accountadd:0",
        )
    )
    for row in rows:
        icon = "🟢" if row["active"] else "⚫"
        title = row["vanity_url"] or row["steam_id"]
        markup.add(
            types.InlineKeyboardButton(
                (
                    f"{icon} {row['club_name'] or 'Без клуба'} / "
                    f"{title}"
                )[:64],
                callback_data=(
                    f"{CALLBACK_PREFIX}:account:{row['steam_id']}"
                ),
            )
        )
    navigation = []
    if page > 0:
        navigation.append(
            types.InlineKeyboardButton(
                "⬅️",
                callback_data=f"{CALLBACK_PREFIX}:accounts:{page - 1}",
            )
        )
    if start + PAGE_SIZE < len(all_rows):
        navigation.append(
            types.InlineKeyboardButton(
                "➡️",
                callback_data=f"{CALLBACK_PREFIX}:accounts:{page + 1}",
            )
        )
    if navigation:
        markup.row(*navigation)
    markup.add(
        types.InlineKeyboardButton(
            "⬅️ Steam Tracker",
            callback_data=f"{CALLBACK_PREFIX}:tracker:0",
        )
    )
    body = (
        "💻 <b>ПК и Steam-аккаунты</b>\n\n"
        f"Всего: {len(all_rows)}\n"
        "Аккаунты не удаляются: ненужный аккаунт можно только отключить."
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


def show_account_card(
    message,
    steam_id: str,
    bot,
    *,
    source_message=None,
    notice: str | None = None,
):
    if not require_role(message, bot, ROLE_OWNER):
        return
    _, storage = _runtime()
    row = dict(storage.managed_account(steam_id))
    types = _telegram_types()
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton(
            "✏️ Изменить зону и клуб",
            callback_data=f"{CALLBACK_PREFIX}:accountedit:{steam_id}",
        ),
        types.InlineKeyboardButton(
            "🔄 Проверить лицензии сейчас",
            callback_data=f"{CALLBACK_PREFIX}:accountsync:{steam_id}",
        ),
        types.InlineKeyboardButton(
            "⚫ Отключить" if row["active"] else "🟢 Включить",
            callback_data=f"{CALLBACK_PREFIX}:accounttoggle:{steam_id}",
        ),
        types.InlineKeyboardButton(
            "⬅️ К аккаунтам",
            callback_data=f"{CALLBACK_PREFIX}:accounts:0",
        ),
    )
    body = (
        f"💻 <b>{escape(row['vanity_url'] or row['steam_id'])}</b>\n\n"
        f"SteamID: <code>{row['steam_id']}</code>\n"
        f"Клуб: {escape(row['club_name'] or 'не указан')}\n"
        f"Статус: {'🟢 активен' if row['active'] else '⚫ отключён'}\n"
        f"Найдено лицензий: {row['owned_games'] or 0}\n"
        f"Последняя успешная запись: {escape(row['updated_at'])}"
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


def request_account_add(message, bot, *, source_message=None):
    if not require_role(message, bot, ROLE_OWNER):
        return
    from club_config import get_clublist

    clubs = ", ".join(get_clublist())
    prompt = _send_context_message(
        message.chat.id,
        bot,
        (
            "Отправьте одной строкой:\n"
            "<code>SteamID | название зоны | клуб</code>\n\n"
            f"Допустимые клубы: {escape(clubs)}"
        ),
        parse_mode="HTML",
        source_message=source_message,
    )
    bot.register_next_step_handler(prompt, save_account_add, bot)


def save_account_add(message, bot):
    user = require_role(message, bot, ROLE_OWNER)
    if not user:
        return
    try:
        parts = [part.strip() for part in str(message.text or "").split("|")]
        if len(parts) != 3:
            raise ValueError(
                "Нужны три значения через символ |: SteamID, зона, клуб"
            )
        steam_id, zone, club = parts
        from club_config import get_clublist

        if club not in get_clublist():
            raise ValueError("Клуб не найден в текущем списке клубов")
        settings, storage = _runtime()
        storage.upsert_managed_account(
            steam_id=steam_id,
            vanity_url=zone,
            club_name=club,
            actor_id=_actor_id(message),
            actor_name=_actor_name(message, user),
        )
        warning = _sync_tracker_data_best_effort(settings, storage)
        notice = "✅ Steam-аккаунт добавлен."
        if warning:
            notice += f"\n⚠️ {warning}"
        show_account_card(message, steam_id, bot, notice=notice)
    except Exception as error:
        show_accounts(message, bot, notice=f"❌ {error}")


def request_account_edit(call, bot, steam_id: str):
    from club_config import get_clublist

    prompt = _send_context_message(
        call.message.chat.id,
        bot,
        (
            "Отправьте одной строкой:\n"
            "<code>новое название зоны | клуб</code>\n\n"
            f"Допустимые клубы: {escape(', '.join(get_clublist()))}"
        ),
        parse_mode="HTML",
        source_message=call.message,
    )
    bot.register_next_step_handler(
        prompt,
        save_account_edit,
        bot,
        steam_id,
    )


def save_account_edit(message, bot, steam_id: str):
    user = require_role(message, bot, ROLE_OWNER)
    if not user:
        return
    try:
        parts = [part.strip() for part in str(message.text or "").split("|")]
        if len(parts) != 2:
            raise ValueError("Нужны зона и клуб через символ |")
        zone, club = parts
        from club_config import get_clublist

        if club not in get_clublist():
            raise ValueError("Клуб не найден в текущем списке клубов")
        settings, storage = _runtime()
        storage.upsert_managed_account(
            steam_id=steam_id,
            vanity_url=zone,
            club_name=club,
            actor_id=_actor_id(message),
            actor_name=_actor_name(message, user),
        )
        warning = _sync_tracker_data_best_effort(settings, storage)
        notice = "✅ Аккаунт обновлён."
        if warning:
            notice += f"\n⚠️ {warning}"
    except Exception as error:
        notice = f"❌ {error}"
    show_account_card(message, steam_id, bot, notice=notice)


def show_sync_menu(
    message,
    bot,
    *,
    source_message=None,
    notice: str | None = None,
):
    if not require_role(message, bot, ROLE_OWNER):
        return
    types = _telegram_types()
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton(
            "🎫 Проверить все лицензии",
            callback_data=f"{CALLBACK_PREFIX}:synclicenses:0",
        ),
        types.InlineKeyboardButton(
            "🖼 Обновить данные Steam Store",
            callback_data=f"{CALLBACK_PREFIX}:syncstore:0",
        ),
        types.InlineKeyboardButton(
            "📊 Выгрузить отчёты в Google",
            callback_data=f"{CALLBACK_PREFIX}:syncgoogle:0",
        ),
        types.InlineKeyboardButton(
            "⬅️ Steam Tracker",
            callback_data=f"{CALLBACK_PREFIX}:tracker:0",
        ),
    )
    body = (
        "🔄 <b>Синхронизация</b>\n\n"
        "Запуски лицензий и Steam Store выполняются в фоне. "
        "Google Sheets только получает отчёты и ничего не меняет в боте."
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


def show_audit(
    message,
    bot,
    *,
    source_message=None,
):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    _, storage = _runtime()
    rows = storage.recent_audit(limit=20)
    action_labels = {
        "game_added": "добавил игру",
        "game_updated": "изменил игру",
        "game_status_changed": "изменил статус игры",
        "game_auto_paused_no_license": (
            "поставил игру на паузу из-за отсутствия лицензии"
        ),
        "account_added": "добавил аккаунт",
        "account_updated": "изменил аккаунт",
        "account_activated": "включил аккаунт",
        "account_deactivated": "отключил аккаунт",
        "setting_updated": "изменил настройку",
    }
    lines = ["📜 <b>Последние изменения</b>", ""]
    if not rows:
        lines.append("Изменений пока нет.")
    for row in rows:
        timestamp = str(row["created_at"]).replace("T", " ")[:16]
        actor = row["actor_name"] or row["actor_id"] or "система"
        action = action_labels.get(row["action"], row["action"])
        lines.append(
            f"{escape(timestamp)} — <b>{escape(actor)}</b> "
            f"{escape(action)} {escape(row['entity_id'] or '')}"
        )
    types = _telegram_types()
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton(
            "⬅️ Steam Tracker",
            callback_data=f"{CALLBACK_PREFIX}:tracker:0",
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
    action = parts[1] if len(parts) > 1 else ""
    promotion_id = (
        int(parts[2])
        if (
            action in {
                "open",
                "claim",
                "takeover",
                "release",
                "generate",
                "approve",
                "texts",
                "edit",
                "more",
                "replace",
                "postpone",
                "cancel",
                "marktest",
                "delete",
            }
            and len(parts) > 2
            and parts[2].isdigit()
        )
        else None
    )
    types = _telegram_types()
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton(
            "⬅️ К карточке" if promotion_id else "⬅️ Steam Tracker",
            callback_data=(
                f"{CALLBACK_PREFIX}:open:{promotion_id}"
                if promotion_id
                else f"{CALLBACK_PREFIX}:tracker:0"
            ),
        )
    )
    _send_context_message(
        call.message.chat.id,
        bot,
        f"❌ Ошибка Steam Tracker: {error}",
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
        user = require_role(call, bot, ROLE_EMPLOYEE)
        if not user:
            return
        parts = call.data.split(":")
        action = parts[1]
        chat_id = call.message.chat.id
        action_lock = None
        try:
            bot.answer_callback_query(call.id)
            if not _is_manager(user) and action not in {
                "home",
                "tracker",
                "catalog",
                "game",
                "glicenses",
            }:
                raise PermissionError(
                    "Сотрудникам доступны только каталог, карточки игр "
                    "и проверка наличия"
                )
            if action in {
                "admin",
                "home",
                "tracker",
                "menu",
                "plane",
                "history",
                "create",
                "settings",
                "catalog",
                "game",
                "geditmenu",
                "gstatusmenu",
                "missingreport",
                "missingclub",
                "accounts",
                "account",
                "syncmenu",
                "audit",
                "list",
                "open",
            }:
                _clear_next_step_handler(chat_id, bot)

            if action == "home":
                _clear_context(
                    chat_id,
                    bot,
                    source_message=call.message,
                )
                from menu import hello

                hello(chat_id, bot)
                return
            if action == "admin":
                _clear_context(
                    chat_id,
                    bot,
                    source_message=call.message,
                )
                from menu import admin_menu

                admin_menu(call.message, bot)
                return
            if action == "tracker":
                show_tracker_dashboard(
                    call.message,
                    bot,
                    source_message=call.message,
                )
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
                    status=parts[2] if len(parts) > 2 else "all",
                    page=int(parts[3]) if len(parts) > 3 else 0,
                    source_message=call.message,
                )
                return
            if action == "game":
                show_game_card(
                    call.message,
                    int(parts[2]),
                    bot,
                    source_message=call.message,
                )
                return
            if action == "gadd":
                request_game_add(
                    call.message,
                    bot,
                    source_message=call.message,
                )
                return
            if action == "gsearch":
                request_game_search(
                    call.message,
                    bot,
                    source_message=call.message,
                )
                return
            if action == "geditmenu":
                show_game_edit_menu(
                    call.message,
                    int(parts[2]),
                    bot,
                    source_message=call.message,
                )
                return
            if action == "gstatusmenu":
                show_game_status_menu(
                    call.message,
                    int(parts[2]),
                    bot,
                    source_message=call.message,
                )
                return
            if action in {
                "geditplayers",
                "geditdesc",
                "geditcomment",
            }:
                field = {
                    "geditplayers": "players",
                    "geditdesc": "description",
                    "geditcomment": "comment",
                }[action]
                request_game_field(call, bot, field, int(parts[2]))
                return
            if action == "glicenses":
                show_game_licenses(
                    call.message,
                    int(parts[2]),
                    bot,
                    source_message=call.message,
                )
                return
            if action == "missingreport":
                show_missing_license_report(
                    call.message,
                    bot,
                    source_message=call.message,
                )
                return
            if action == "missingclub":
                show_missing_license_club(
                    call.message,
                    parts[2],
                    int(parts[3]),
                    bot,
                    source_message=call.message,
                )
                return
            if action == "gstatus":
                settings, storage = _runtime()
                storage.set_game_catalog_status(
                    int(parts[3]),
                    parts[2],
                    actor_id=_actor_id(call),
                    actor_name=_actor_name(call, user),
                )
                warning = _sync_tracker_data_best_effort(
                    settings,
                    storage,
                )
                notice = "✅ Статус игры обновлён."
                if warning:
                    notice += f"\n⚠️ {warning}"
                show_game_card(
                    call.message,
                    int(parts[3]),
                    bot,
                    source_message=call.message,
                    notice=notice,
                )
                return
            if action == "grefresh":
                app_id = int(parts[2])
                settings, storage = _runtime()
                wait = _send_context_message(
                    chat_id,
                    bot,
                    "⏳ Обновляю данные Steam Store…",
                    source_message=call.message,
                )
                metadata = SteamStoreClient().get_metadata(app_id)
                storage.save_game_metadata(
                    app_id,
                    steam_name=metadata.name,
                    store_description=metadata.description,
                    genres=metadata.genres,
                    categories=metadata.categories,
                    header_image=metadata.header_image,
                    screenshots=metadata.screenshots,
                    is_free=metadata.is_free,
                    source_language=metadata.source_language,
                )
                warning = _sync_tracker_data_best_effort(
                    settings,
                    storage,
                )
                notice = "✅ Данные Steam Store обновлены."
                if warning:
                    notice += f"\n⚠️ {warning}"
                show_game_card(
                    call.message,
                    app_id,
                    bot,
                    source_message=wait,
                    notice=notice,
                )
                return
            if action == "accounts":
                show_accounts(
                    call.message,
                    bot,
                    page=int(parts[2]),
                    source_message=call.message,
                )
                return
            if action == "account":
                show_account_card(
                    call.message,
                    parts[2],
                    bot,
                    source_message=call.message,
                )
                return
            if action == "accountadd":
                request_account_add(
                    call.message,
                    bot,
                    source_message=call.message,
                )
                return
            if action == "accountedit":
                if not _is_owner(user):
                    raise PermissionError(
                        "Это действие доступно только владельцу"
                    )
                request_account_edit(call, bot, parts[2])
                return
            if action == "accounttoggle":
                if not _is_owner(user):
                    raise PermissionError(
                        "Это действие доступно только владельцу"
                    )
                settings, storage = _runtime()
                row = storage.managed_account(parts[2])
                storage.set_account_active(
                    parts[2],
                    not bool(row["active"]),
                    actor_id=_actor_id(call),
                    actor_name=_actor_name(call, user),
                )
                warning = _sync_tracker_data_best_effort(
                    settings,
                    storage,
                )
                notice = "✅ Статус аккаунта обновлён."
                if warning:
                    notice += f"\n⚠️ {warning}"
                show_account_card(
                    call.message,
                    parts[2],
                    bot,
                    source_message=call.message,
                    notice=notice,
                )
                return
            if action == "accountsync":
                if not _is_owner(user):
                    raise PermissionError(
                        "Это действие доступно только владельцу"
                    )
                settings, storage = _runtime()
                if not settings.steam_api_key:
                    raise ValueError("STEAM_API_KEY не задан")
                wait = _send_context_message(
                    chat_id,
                    bot,
                    "⏳ Проверяю библиотеку Steam…",
                    source_message=call.message,
                )
                result = LicenseSyncService(
                    storage,
                    SteamClient(settings.steam_api_key),
                    removal_threshold=settings.removal_threshold,
                ).sync_account(parts[2])
                warning = _sync_tracker_data_best_effort(
                    settings,
                    storage,
                )
                notice = (
                    f"✅ Найдено игр: {result.seen_games}; "
                    f"добавлено: {result.added}; снято: {result.removed}."
                )
                if warning:
                    notice += f"\n⚠️ {warning}"
                show_account_card(
                    call.message,
                    parts[2],
                    bot,
                    source_message=wait,
                    notice=notice,
                )
                return
            if action == "syncmenu":
                show_sync_menu(
                    call.message,
                    bot,
                    source_message=call.message,
                )
                return
            if action in {"synclicenses", "syncstore"}:
                if not _is_owner(user):
                    raise PermissionError(
                        "Это действие доступно только владельцу"
                    )
                from .jobs import start_license_sync, start_store_enrichment

                started = (
                    start_license_sync()
                    if action == "synclicenses"
                    else start_store_enrichment()
                )
                show_sync_menu(
                    call.message,
                    bot,
                    source_message=call.message,
                    notice=(
                        "✅ Фоновая синхронизация запущена."
                        if started
                        else "⚠️ Запуск отключён настройками сервера "
                        "или уже выполняется."
                    ),
                )
                return
            if action == "syncgoogle":
                if not _is_owner(user):
                    raise PermissionError(
                        "Это действие доступно только владельцу"
                    )
                settings, storage = _runtime()
                wait = _send_context_message(
                    chat_id,
                    bot,
                    "⏳ Выгружаю отчёты в Google Sheets…",
                    source_message=call.message,
                )
                GoogleSheetsManager(settings).sync_tracker_data(
                    storage,
                    apply=True,
                )
                show_sync_menu(
                    call.message,
                    bot,
                    source_message=wait,
                    notice="✅ Отчёты Google Sheets обновлены.",
                )
                return
            if action == "audit":
                show_audit(
                    call.message,
                    bot,
                    source_message=call.message,
                )
                return
            if action == "cycleday":
                if not _is_owner(user):
                    raise PermissionError(
                        "Это действие доступно только владельцу"
                    )
                settings, storage = _runtime()
                days = [
                    "monday",
                    "tuesday",
                    "wednesday",
                    "thursday",
                    "friday",
                    "saturday",
                    "sunday",
                ]
                current = storage.tracker_settings().get(
                    "generation_day",
                    "monday",
                )
                if current not in days:
                    current = "monday"
                next_day = days[(days.index(current) + 1) % len(days)]
                storage.update_tracker_setting(
                    "generation_day",
                    next_day,
                    actor_id=_actor_id(call),
                    actor_name=_actor_name(call, user),
                )
                _sync_tracker_data_best_effort(settings, storage)
                show_promo_settings(
                    call.message,
                    bot,
                    source_message=call.message,
                    notice=f"✅ День запуска: {next_day}.",
                )
                return
            if action in {"edittime", "edittimezone"}:
                if not _is_owner(user):
                    raise PermissionError(
                        "Это действие доступно только владельцу"
                    )
                request_tracker_setting(
                    call,
                    bot,
                    (
                        "generation_time"
                        if action == "edittime"
                        else "timezone"
                    ),
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
                raise ValueError(
                    "Импорт каталога из Google отключён. "
                    "Используйте раздел «Каталог игр» в боте."
                )
            elif action == "editdiscount":
                if not _is_owner(user):
                    raise PermissionError(
                        "Это действие доступно только владельцу"
                    )
                _request_discount(call, bot)
            elif action == "toggleweekly":
                if not _is_owner(user):
                    raise PermissionError(
                        "Это действие доступно только руководству"
                    )
                settings, storage = _runtime()
                values = storage.tracker_settings()
                enabled = setting_enabled(
                    values.get("weekly_promo_enabled")
                )
                if not enabled and not settings.weekly_promo_enabled:
                    raise ValueError(
                        "Автоматический режим запрещён на сервере. "
                        "Сначала задайте "
                        "STEAMTRACKER_WEEKLY_PROMO_ENABLED=true "
                        "и перезапустите bot"
                    )
                storage.update_tracker_setting(
                    "weekly_promo_enabled",
                    "false" if enabled else "true",
                    actor_id=_actor_id(call),
                    actor_name=_actor_name(call, user),
                )
                _sync_tracker_data_best_effort(settings, storage)
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
