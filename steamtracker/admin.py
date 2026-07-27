"""Административное управление промо внутри Виарыча."""

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


def _admin_reply_keyboard():
    types = _telegram_types()
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add(
        "⭐ Текущее промо",
        "➕ Создать промо",
        "📋 Все промо",
        "🧪 Тестовые варианты",
        "📦 Очередь отправки",
        "🎮 Каталог игр",
        "⚙️ Настройки промо",
        "⬅️ Назад в админку",
    )
    return markup


def promotion_admin_menu(message, bot):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    msg = bot.send_message(
        message.chat.id,
        "📣 <b>Управление промо</b>\n\nВыберите раздел.",
        parse_mode="HTML",
        reply_markup=_admin_reply_keyboard(),
    )
    bot.register_next_step_handler(msg, promotion_admin_handler, bot)


def promotion_admin_handler(message, bot):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    action = message.text
    if action == "⭐ Текущее промо":
        show_current_promotion(message, bot)
    elif action == "➕ Создать промо":
        show_create_menu(message, bot)
    elif action == "📋 Все промо":
        show_promotion_list(message, bot, scope="all", page=0)
    elif action == "🧪 Тестовые варианты":
        show_promotion_list(message, bot, scope="test", page=0)
    elif action == "📦 Очередь отправки":
        show_outbox(message, bot)
    elif action == "🎮 Каталог игр":
        show_catalog(message, bot)
    elif action == "⚙️ Настройки промо":
        show_promo_settings(message, bot)
    elif action == "⬅️ Назад в админку":
        from menu import admin_menu

        admin_menu(message, bot)
    else:
        promotion_admin_menu(message, bot)


def _card_markup(row: dict):
    types = _telegram_types()
    promotion_id = int(row["id"])
    status = row["status"]
    is_test = bool(row["is_test"])
    prefix = CALLBACK_PREFIX
    markup = types.InlineKeyboardMarkup(row_width=2)

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

    if status == "draft":
        markup.add(
            types.InlineKeyboardButton(
                "✨ Сгенерировать",
                callback_data=f"{prefix}:generate:{promotion_id}",
            )
        )
    elif status == "review":
        if not is_test:
            markup.add(
                types.InlineKeyboardButton(
                    "✅ Согласовать",
                    callback_data=f"{prefix}:approve:{promotion_id}",
                )
            )
        markup.add(
            types.InlineKeyboardButton(
                "✏️ Редактировать",
                callback_data=f"{prefix}:edit:{promotion_id}",
            ),
            types.InlineKeyboardButton(
                "🔄 Переделать всё",
                callback_data=f"{prefix}:regenall:{promotion_id}",
            ),
        )
        markup.add(
            types.InlineKeyboardButton(
                "👥 Переделать сотрудникам",
                callback_data=f"{prefix}:regenemployee:{promotion_id}",
            ),
            types.InlineKeyboardButton(
                "📣 Переделать анонсы",
                callback_data=f"{prefix}:regensocial:{promotion_id}",
            ),
        )
        if not is_test and row.get("cycle_number"):
            markup.add(
                types.InlineKeyboardButton(
                    "🎲 Другая игра",
                    callback_data=f"{prefix}:replace:{promotion_id}",
                )
            )

    if status in {"draft", "review"} and not is_test:
        markup.add(
            types.InlineKeyboardButton(
                "⏸ Отложить",
                callback_data=f"{prefix}:confirmpostpone:{promotion_id}",
            ),
            types.InlineKeyboardButton(
                "🗑 Отменить",
                callback_data=f"{prefix}:confirmcancel:{promotion_id}",
            ),
        )
    if (
        not is_test
        and status in {"draft", "review", "postponed", "cancelled"}
        and not row.get("outbox_total")
    ):
        markup.add(
            types.InlineKeyboardButton(
                "🧪 Пометить тестовым",
                callback_data=f"{prefix}:confirmmarktest:{promotion_id}",
            )
        )
    if is_test:
        markup.add(
            types.InlineKeyboardButton(
                "🗑 Удалить тест",
                callback_data=f"{prefix}:confirmdelete:{promotion_id}",
            )
        )

    if row.get("outbox_total"):
        markup.add(
            types.InlineKeyboardButton(
                "📦 Очередь отправки",
                callback_data=f"{prefix}:outbox:{promotion_id}",
            )
        )
    markup.add(
        types.InlineKeyboardButton(
            "⬅️ К списку",
            callback_data=f"{prefix}:list:all:0",
        ),
        types.InlineKeyboardButton(
            "🏠 Меню промо",
            callback_data=f"{prefix}:menu:0",
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
    return (
        f"📣 <b>Промо #{row['id']}</b>{test_label}\n\n"
        f"🎮 <b>{escape(str(row['steam_name']))}</b>\n"
        f"AppID: <code>{row['app_id']}</code>\n"
        f"Статус: {STATUS_LABELS.get(row['status'], row['status'])}\n"
        f"Период: {escape(period)}\n"
        f"Акция: {escape(str(row['discount_text']))}\n"
        f"Изображение: {image_source}\n"
        f"Отправка: {delivery}"
    )


def send_promotion_card(chat_id, promotion_id: int, bot):
    _, storage = _runtime()
    row = dict(storage.promotion_admin_row(promotion_id))
    caption = _promotion_caption(row)
    markup = _card_markup(row)
    image_url = row.get("publish_image_url")
    if image_url:
        try:
            bot.send_photo(
                chat_id,
                photo=image_url,
                caption=caption,
                parse_mode="HTML",
                reply_markup=markup,
            )
            return
        except Exception:
            pass
    bot.send_message(
        chat_id,
        caption,
        parse_mode="HTML",
        reply_markup=markup,
    )


def show_current_promotion(message, bot):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    _, storage = _runtime()
    today = datetime.now(MOSCOW).date().isoformat()
    row = storage.current_promotion(today)
    if row is None:
        types = _telegram_types()
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton(
                "🎲 Сформировать игру недели",
                callback_data=f"{CALLBACK_PREFIX}:createweekly:0",
            )
        )
        bot.send_message(
            message.chat.id,
            "На текущую неделю активного промо нет.",
            reply_markup=markup,
        )
        return
    send_promotion_card(message.chat.id, int(row["id"]), bot)


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


def show_promotion_list(message, bot, *, scope: str, page: int):
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
    markup.row(
        filters("🟡 В работе", callback_data=f"{CALLBACK_PREFIX}:list:review:0"),
        filters("🟢 Готовые", callback_data=f"{CALLBACK_PREFIX}:list:approved:0"),
    )
    markup.row(
        filters("🗄 Архив", callback_data=f"{CALLBACK_PREFIX}:list:archive:0"),
        filters("🧪 Тесты", callback_data=f"{CALLBACK_PREFIX}:list:test:0"),
    )
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
    markup.add(
        filters(
            "🏠 Меню промо",
            callback_data=f"{CALLBACK_PREFIX}:menu:0",
        )
    )
    text = (
        f"📋 <b>{escape(title)}</b>\n"
        f"Страница {page + 1}."
    )
    if not rows:
        text += "\n\nЗаписей нет."
    bot.send_message(
        message.chat.id,
        text,
        parse_mode="HTML",
        reply_markup=markup,
    )


def show_create_menu(message, bot):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    types = _telegram_types()
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add(
        "🎲 Случайная игра недели",
        "🎮 Выбрать по AppID",
        "🧪 Тестовый вариант",
        "⬅️ Назад в промо",
    )
    msg = bot.send_message(
        message.chat.id,
        "Какое промо создать?",
        reply_markup=markup,
    )
    bot.register_next_step_handler(msg, create_menu_handler, bot)


def create_menu_handler(message, bot):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    if message.text == "🎲 Случайная игра недели":
        create_weekly_promotion(message, bot)
    elif message.text == "🎮 Выбрать по AppID":
        types = _telegram_types()
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
        markup.add("Отмена")
        msg = bot.send_message(
            message.chat.id,
            "Введите Steam AppID игры:",
            reply_markup=markup,
        )
        bot.register_next_step_handler(msg, create_manual_promotion, bot)
    elif message.text == "🧪 Тестовый вариант":
        create_test_promotion(message, bot)
    elif message.text == "⬅️ Назад в промо":
        promotion_admin_menu(message, bot)
    else:
        show_create_menu(message, bot)


def create_weekly_promotion(message, bot):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    wait = bot.send_message(message.chat.id, "⏳ Формирую игру недели...")
    try:
        settings, storage = _runtime()
        result = _weekly_service(settings, storage).run(force=True)
        bot.edit_message_text(
            f"✅ Промо #{result.promotion_id} готово.",
            message.chat.id,
            wait.message_id,
        )
        send_promotion_card(message.chat.id, result.promotion_id, bot)
    except Exception as error:
        bot.edit_message_text(
            f"❌ Не удалось создать промо: {error}",
            message.chat.id,
            wait.message_id,
        )


def _promotion_defaults(settings: Settings) -> tuple[str, str, str]:
    values = GoogleSheetsManager(settings).read_tracker_settings()
    discount = str(values.get("weekly_discount") or "").strip()
    if not discount:
        raise ValueError("В настройках не заполнена скидка")
    start, end = week_period()
    return discount, start.isoformat(), end.isoformat()


def create_manual_promotion(message, bot):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    if message.text == "Отмена":
        show_create_menu(message, bot)
        return
    try:
        app_id = int(str(message.text).strip())
    except (TypeError, ValueError):
        bot.send_message(message.chat.id, "AppID должен состоять из цифр.")
        show_create_menu(message, bot)
        return
    wait = bot.send_message(message.chat.id, "⏳ Создаю промо...")
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
        bot.edit_message_text(
            text,
            message.chat.id,
            wait.message_id,
        )
        send_promotion_card(message.chat.id, promotion_id, bot)
    except Exception as error:
        bot.edit_message_text(
            f"❌ Не удалось создать промо: {error}",
            message.chat.id,
            wait.message_id,
        )


def create_test_promotion(message, bot):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    wait = bot.send_message(message.chat.id, "⏳ Создаю тестовый вариант...")
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
        bot.edit_message_text(
            text,
            message.chat.id,
            wait.message_id,
        )
        send_promotion_card(message.chat.id, promotion_id, bot)
    except Exception as error:
        bot.edit_message_text(
            f"❌ Не удалось создать тест: {error}",
            message.chat.id,
            wait.message_id,
        )


def _send_promotion_texts(chat_id: int, promotion_id: int, bot) -> None:
    _, storage = _runtime()
    row = dict(storage.promotion_admin_row(promotion_id))
    messages = (
        ("👥 <b>СОТРУДНИКАМ</b>", row.get("employee_text"), "HTML"),
        ("✈️ <b>TELEGRAM</b>", row.get("telegram_text"), "HTML"),
        ("🔵 VK", row.get("vk_text"), None),
    )
    for title, text, parse_mode in messages:
        if not text:
            continue
        bot.send_message(
            chat_id,
            f"{title}\n\n{text}",
            parse_mode=parse_mode,
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
            "⬅️ К карточке",
            callback_data=f"{CALLBACK_PREFIX}:open:{promotion_id}",
        ),
    )
    return markup


def _request_manual_text(call, channel: str, bot) -> None:
    labels = {
        "employee": "текст для сотрудников",
        "telegram": "текст Telegram",
        "vk": "текст VK",
    }
    types = _telegram_types()
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("Отмена")
    msg = bot.send_message(
        call.message.chat.id,
        f"Отправьте новый {labels[channel]}. Название игры и скидка "
        "должны остаться без изменений.",
        reply_markup=markup,
    )
    bot.register_next_step_handler(
        msg,
        save_manual_text,
        int(call.data.rsplit(":", 1)[1]),
        channel,
        bot,
    )


def save_manual_text(message, promotion_id: int, channel: str, bot):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    if message.text == "Отмена":
        send_promotion_card(message.chat.id, promotion_id, bot)
        return
    text = str(message.text or "").strip()
    limits = {"employee": 3500, "telegram": 3500, "vk": 6000}
    if not text or len(text) > limits[channel]:
        bot.send_message(
            message.chat.id,
            f"Текст должен содержать от 1 до {limits[channel]} символов.",
        )
        send_promotion_card(message.chat.id, promotion_id, bot)
        return
    settings, storage = _runtime()
    promotion = dict(storage.promotion_admin_row(promotion_id))
    plain_text = unescape(text)
    if promotion["steam_name"] not in plain_text:
        bot.send_message(
            message.chat.id,
            "Название игры должно присутствовать без изменений.",
        )
        send_promotion_card(message.chat.id, promotion_id, bot)
        return
    if promotion["discount_text"] not in plain_text:
        bot.send_message(
            message.chat.id,
            "Точное значение скидки должно присутствовать в тексте.",
        )
        send_promotion_card(message.chat.id, promotion_id, bot)
        return
    values = {
        "employee_text": text if channel == "employee" else None,
        "telegram_text": text if channel == "telegram" else None,
        "vk_text": text if channel == "vk" else None,
    }
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
        bot.send_message(message.chat.id, response)
    except Exception as error:
        bot.send_message(message.chat.id, f"❌ {error}")
    send_promotion_card(message.chat.id, promotion_id, bot)


def show_outbox(message, bot, *, promotion_id: int | None = None):
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
            "🏠 Меню промо",
            callback_data=f"{CALLBACK_PREFIX}:menu:0",
        ),
    )
    bot.send_message(
        message.chat.id,
        text,
        parse_mode="HTML",
        reply_markup=markup,
    )


def show_catalog(message, bot):
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
            "🏠 Меню промо",
            callback_data=f"{CALLBACK_PREFIX}:menu:0",
        )
    )
    bot.send_message(
        message.chat.id,
        (
            "🎮 <b>Каталог игр</b>\n\n"
            f"Согласовано: {summary['approved_games']}\n"
            f"Обогащено Steam: {summary['enriched_games']}\n"
            f"Цикл ротации: {rotation['cycle_number']}\n"
            f"Уже использовано: {rotation['used_games']}\n"
            f"Доступно: {rotation['available_games']}"
        ),
        parse_mode="HTML",
        reply_markup=markup,
    )


def show_promo_settings(message, bot):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    settings, _ = _runtime()
    try:
        values = GoogleSheetsManager(settings).read_tracker_settings()
    except Exception as error:
        bot.send_message(
            message.chat.id,
            f"❌ Не удалось прочитать настройки: {error}",
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
            "🏠 Меню промо",
            callback_data=f"{CALLBACK_PREFIX}:menu:0",
        ),
    )
    bot.send_message(
        message.chat.id,
        (
            "⚙️ <b>Настройки промо</b>\n\n"
            f"Скидка: {escape(values.get('weekly_discount', 'не задана'))}\n"
            "Расписание сервера: понедельник, 10:30 МСК\n"
            f"Разрешено на сервере: "
            f"{'да' if settings.weekly_promo_enabled else 'нет'}\n"
            f"Включено менеджером: {'да' if sheet_enabled else 'нет'}\n"
            f"Итог: {'🟢 включено' if fully_enabled else '⏸ выключено'}\n"
            f"Генератор: {escape(settings.generator_provider)}\n"
            "Публикация: dry-run"
        ),
        parse_mode="HTML",
        reply_markup=markup,
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
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("Отмена")
    msg = bot.send_message(
        call.message.chat.id,
        "Введите точный текст скидки, например: 100 рублей",
        reply_markup=markup,
    )
    bot.register_next_step_handler(msg, save_discount, bot)


def save_discount(message, bot):
    if not require_role(message, bot, ROLE_MANAGER):
        return
    if message.text == "Отмена":
        show_promo_settings(message, bot)
        return
    value = str(message.text or "").strip()
    if not value or len(value) > 80:
        bot.send_message(
            message.chat.id,
            "Скидка должна содержать от 1 до 80 символов.",
        )
        show_promo_settings(message, bot)
        return
    try:
        settings, _ = _runtime()
        GoogleSheetsManager(settings).update_tracker_setting(
            "weekly_discount",
            value,
        )
        bot.send_message(message.chat.id, "✅ Скидка обновлена.")
    except Exception as error:
        bot.send_message(message.chat.id, f"❌ {error}")
    show_promo_settings(message, bot)


def _regenerate(
    call,
    bot,
    promotion_id: int,
    section: str,
) -> None:
    settings, storage = _runtime()
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
    bot.send_message(call.message.chat.id, text)
    send_promotion_card(call.message.chat.id, promotion_id, bot)


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
        try:
            bot.answer_callback_query(call.id)
            if action == "menu":
                promotion_admin_menu(call.message, bot)
                return
            if action == "list":
                show_promotion_list(
                    call.message,
                    bot,
                    scope=parts[2],
                    page=int(parts[3]),
                )
                return
            promotion_id = int(parts[2])
            if action == "open":
                send_promotion_card(
                    call.message.chat.id,
                    promotion_id,
                    bot,
                )
            elif action == "texts":
                _send_promotion_texts(
                    call.message.chat.id,
                    promotion_id,
                    bot,
                )
            elif action == "generate":
                settings, storage = _runtime()
                _workflow(settings, storage).generate(promotion_id)
                warning = _sync_promotion_best_effort(
                    settings,
                    storage,
                    promotion_id,
                )
                text = "✅ Тексты готовы."
                if warning:
                    text += f"\n⚠️ {warning}"
                bot.send_message(call.message.chat.id, text)
                send_promotion_card(
                    call.message.chat.id,
                    promotion_id,
                    bot,
                )
            elif action == "approve":
                settings, storage = _runtime()
                _workflow(settings, storage).approve_and_dispatch(
                    promotion_id,
                    approved_by=str(call.from_user.id),
                )
                warning = _sync_promotion_best_effort(
                    settings,
                    storage,
                    promotion_id,
                )
                text = (
                    "✅ Промо согласовано. Создана только "
                    "dry-run очередь."
                )
                if warning:
                    text += f"\n⚠️ {warning}"
                bot.send_message(call.message.chat.id, text)
                send_promotion_card(
                    call.message.chat.id,
                    promotion_id,
                    bot,
                )
            elif action == "edit":
                bot.send_message(
                    call.message.chat.id,
                    "Какой текст изменить?",
                    reply_markup=_edit_markup(promotion_id),
                )
            elif action == "edemployee":
                _request_manual_text(call, "employee", bot)
            elif action == "edtelegram":
                _request_manual_text(call, "telegram", bot)
            elif action == "edvk":
                _request_manual_text(call, "vk", bot)
            elif action == "regenall":
                _regenerate(call, bot, promotion_id, "all")
            elif action == "regenemployee":
                _regenerate(call, bot, promotion_id, "employee")
            elif action == "regensocial":
                _regenerate(call, bot, promotion_id, "social")
            elif action == "replace":
                settings, storage = _runtime()
                replacement = _weekly_service(
                    settings,
                    storage,
                ).replace(promotion_id)
                bot.send_message(
                    call.message.chat.id,
                    f"✅ Выбрано новое промо #{replacement.promotion_id}.",
                )
                send_promotion_card(
                    call.message.chat.id,
                    replacement.promotion_id,
                    bot,
                )
            elif action == "confirmpostpone":
                bot.send_message(
                    call.message.chat.id,
                    f"Отложить промо #{promotion_id}?",
                    reply_markup=_confirmation_markup(
                        promotion_id,
                        "postpone",
                        "Да, отложить",
                    ),
                )
            elif action == "postpone":
                settings, storage = _runtime()
                storage.set_promotion_status(
                    promotion_id,
                    "postponed",
                )
                warning = _sync_promotion_best_effort(
                    settings,
                    storage,
                    promotion_id,
                )
                text = "⏸ Промо отложено."
                if warning:
                    text += f"\n⚠️ {warning}"
                bot.send_message(call.message.chat.id, text)
                send_promotion_card(
                    call.message.chat.id,
                    promotion_id,
                    bot,
                )
            elif action == "confirmcancel":
                bot.send_message(
                    call.message.chat.id,
                    f"Отменить промо #{promotion_id}?",
                    reply_markup=_confirmation_markup(
                        promotion_id,
                        "cancel",
                        "Да, отменить",
                    ),
                )
            elif action == "cancel":
                settings, storage = _runtime()
                storage.set_promotion_status(
                    promotion_id,
                    "cancelled",
                )
                warning = _sync_promotion_best_effort(
                    settings,
                    storage,
                    promotion_id,
                )
                text = "🗄 Промо отменено и сохранено в истории."
                if warning:
                    text += f"\n⚠️ {warning}"
                bot.send_message(call.message.chat.id, text)
                send_promotion_card(
                    call.message.chat.id,
                    promotion_id,
                    bot,
                )
            elif action == "confirmmarktest":
                if int(user["status"]) < ROLE_OWNER:
                    bot.send_message(
                        call.message.chat.id,
                        "Это действие доступно только руководству.",
                    )
                    return
                bot.send_message(
                    call.message.chat.id,
                    f"Пометить промо #{promotion_id} тестовым?",
                    reply_markup=_confirmation_markup(
                        promotion_id,
                        "marktest",
                        "Да, это тест",
                    ),
                )
            elif action == "marktest":
                if int(user["status"]) < ROLE_OWNER:
                    bot.send_message(
                        call.message.chat.id,
                        "Это действие доступно только руководству.",
                    )
                    return
                settings, storage = _runtime()
                storage.mark_promotion_as_test(promotion_id)
                warning = _sync_promotion_best_effort(
                    settings,
                    storage,
                    promotion_id,
                )
                text = "🧪 Промо помечено тестовым."
                if warning:
                    text += f"\n⚠️ {warning}"
                bot.send_message(call.message.chat.id, text)
                send_promotion_card(
                    call.message.chat.id,
                    promotion_id,
                    bot,
                )
            elif action == "confirmdelete":
                if int(user["status"]) < ROLE_OWNER:
                    bot.send_message(
                        call.message.chat.id,
                        "Это действие доступно только руководству.",
                    )
                    return
                bot.send_message(
                    call.message.chat.id,
                    f"Полностью удалить тестовое промо #{promotion_id}?",
                    reply_markup=_confirmation_markup(
                        promotion_id,
                        "delete",
                        "Удалить навсегда",
                    ),
                )
            elif action == "delete":
                if int(user["status"]) < ROLE_OWNER:
                    bot.send_message(
                        call.message.chat.id,
                        "Это действие доступно только руководству.",
                    )
                    return
                settings, storage = _runtime()
                storage.delete_test_promotion(promotion_id)
                warning = None
                try:
                    GoogleSheetsManager(settings).remove_promotion(
                        promotion_id
                    )
                except Exception as error:
                    warning = f"строка Google не удалена: {error}"
                text = f"🗑 Тестовое промо #{promotion_id} удалено."
                if warning:
                    text += f"\n⚠️ {warning}"
                bot.send_message(call.message.chat.id, text)
                show_promotion_list(
                    call.message,
                    bot,
                    scope="test",
                    page=0,
                )
            elif action == "outbox":
                show_outbox(
                    call.message,
                    bot,
                    promotion_id=promotion_id or None,
                )
            elif action == "createweekly":
                create_weekly_promotion(call.message, bot)
            elif action == "synccatalog":
                settings, storage = _runtime()
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
                bot.send_message(
                    call.message.chat.id,
                    (
                        "✅ Каталог применён: "
                        f"{result.active_games} активных, "
                        f"{result.excluded_games} исключено."
                    ),
                )
                show_catalog(call.message, bot)
            elif action == "editdiscount":
                _request_discount(call, bot)
            elif action == "toggleweekly":
                if int(user["status"]) < ROLE_OWNER:
                    bot.send_message(
                        call.message.chat.id,
                        "Это действие доступно только руководству.",
                    )
                    return
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
                manager.update_tracker_setting(
                    "weekly_promo_enabled",
                    "false" if enabled else "true",
                )
                bot.send_message(
                    call.message.chat.id,
                    "✅ Настройка автогенерации обновлена.",
                )
                show_promo_settings(call.message, bot)
            else:
                bot.send_message(
                    call.message.chat.id,
                    "Неизвестное действие промо.",
                )
        except Exception as error:
            bot.send_message(
                call.message.chat.id,
                f"❌ Ошибка промо: {error}",
            )
