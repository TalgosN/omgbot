"""Telegram-согласование промо внутри Виарыча.

Модуль не регистрирует обработчики, пока feature flag выключен. После
согласования используются только DryRunPublisher и локальный outbox.
"""

from .config import Settings
from .db import TrackerStorage
from .llm import build_generator
from .promo import DryRunPublisher, PromotionWorkflow
from .sheets import GoogleSheetsManager
from .weekly import WeeklyPromotionService


CALLBACK_PREFIX = "stp"


def _workflow(
    settings: Settings,
    storage: TrackerStorage,
) -> PromotionWorkflow:
    return PromotionWorkflow(
        storage,
        build_generator(settings),
        DryRunPublisher(),
    )


def _controls(
    promotion_id: int,
    *,
    status: str,
    is_test: bool,
):
    from telebot import types

    markup = types.InlineKeyboardMarkup(row_width=2)
    if status == "review":
        if not is_test:
            markup.add(
                types.InlineKeyboardButton(
                    "✅ Согласовать",
                    callback_data=(
                        f"{CALLBACK_PREFIX}:approve:{promotion_id}"
                    ),
                ),
                types.InlineKeyboardButton(
                    "🎲 Другая игра",
                    callback_data=(
                        f"{CALLBACK_PREFIX}:replace:{promotion_id}"
                    ),
                ),
            )
        markup.add(
            types.InlineKeyboardButton(
                "🔄 Переделать всё",
                callback_data=f"{CALLBACK_PREFIX}:all:{promotion_id}",
            ),
            types.InlineKeyboardButton(
                "👥 Текст сотрудникам",
                callback_data=f"{CALLBACK_PREFIX}:employee:{promotion_id}",
            ),
        )
        markup.add(
            types.InlineKeyboardButton(
                "📣 Анонсы",
                callback_data=f"{CALLBACK_PREFIX}:social:{promotion_id}",
            ),
            types.InlineKeyboardButton(
                "⏸ Отложить",
                callback_data=f"{CALLBACK_PREFIX}:postpone:{promotion_id}",
            ),
        )
    return markup if status == "review" else None


def _sync_promotion_best_effort(
    settings: Settings,
    storage: TrackerStorage,
    promotion_id: int,
) -> None:
    try:
        GoogleSheetsManager(settings).sync_promotion(
            storage,
            promotion_id,
            apply=True,
        )
    except Exception as error:
        print(
            "Steam Tracker Telegram: не удалось обновить Промо-план: "
            f"{error}"
        )


def send_promotion_preview(
    bot,
    storage: TrackerStorage,
    workflow: PromotionWorkflow,
    chat_id: int,
    promotion_id: int,
) -> None:
    promo = dict(storage.promotion_context(promotion_id))
    if not all(
        [
            promo.get("employee_text"),
            promo.get("telegram_text"),
            promo.get("vk_text"),
        ]
    ):
        workflow.generate(promotion_id)
        promo = dict(storage.promotion_context(promotion_id))

    bot.send_message(
        chat_id,
        f"<b>👥 СОТРУДНИКАМ</b>\n\n{promo['employee_text']}",
        parse_mode="HTML",
    )
    bot.send_message(
        chat_id,
        f"<b>📣 TELEGRAM</b>\n\n{promo['telegram_text']}",
        parse_mode="HTML",
    )
    bot.send_message(
        chat_id,
        f"🔵 VK\n\n{promo['vk_text']}",
    )
    bot.send_message(
        chat_id,
        (
            f"Промо #{promotion_id}: {promo['game_name']}\n"
            "Публикация выключена: после согласования задания останутся "
            "в dry-run outbox."
        ),
        reply_markup=_controls(
            promotion_id,
            status=promo["status"],
            is_test=bool(promo["is_test"]),
        ),
    )


def send_promotion_to_approvers(
    bot,
    settings: Settings,
    promotion_id: int,
) -> None:
    storage = TrackerStorage(settings.db_path)
    storage.initialize()
    workflow = _workflow(settings, storage)
    for user_id in sorted(settings.telegram_approver_ids):
        try:
            send_promotion_preview(
                bot,
                storage,
                workflow,
                user_id,
                promotion_id,
            )
        except Exception as error:
            print(
                "Steam Tracker Telegram: не удалось отправить промо "
                f"пользователю {user_id}: {error}"
            )


def register_steamtracker_handlers(bot) -> bool:
    settings = Settings.from_env()
    if not settings.telegram_approval_enabled:
        return False
    if not settings.telegram_approver_ids:
        raise RuntimeError(
            "Для Telegram-согласования задайте STEAMTRACKER_APPROVER_IDS"
        )

    storage = TrackerStorage(settings.db_path)
    storage.initialize()
    workflow = _workflow(settings, storage)

    def is_allowed(user_id: int) -> bool:
        return user_id in settings.telegram_approver_ids

    @bot.message_handler(commands=["steam_promo"])
    def steam_promo_command(message):
        if not is_allowed(message.from_user.id):
            bot.reply_to(message, "Нет доступа к согласованию.")
            return
        parts = (message.text or "").split()
        if len(parts) != 2 or not parts[1].isdigit():
            bot.reply_to(message, "Использование: /steam_promo <id>")
            return
        promotion_id = int(parts[1])
        try:
            send_promotion_preview(
                bot,
                storage,
                workflow,
                message.chat.id,
                promotion_id,
            )
            _sync_promotion_best_effort(
                settings,
                storage,
                promotion_id,
            )
        except Exception as error:
            bot.reply_to(message, f"Ошибка промо: {error}")

    @bot.callback_query_handler(
        func=lambda call: bool(
            call.data and call.data.startswith(f"{CALLBACK_PREFIX}:")
        )
    )
    def steam_promo_callback(call):
        if not is_allowed(call.from_user.id):
            bot.answer_callback_query(call.id, "Нет доступа.")
            return
        try:
            _, action, promotion_id_text = call.data.split(":", 2)
            promotion_id = int(promotion_id_text)
            if action == "approve":
                workflow.approve_and_dispatch(
                    promotion_id,
                    approved_by=str(call.from_user.id),
                )
                _sync_promotion_best_effort(
                    settings,
                    storage,
                    promotion_id,
                )
                bot.edit_message_reply_markup(
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=None,
                )
                bot.answer_callback_query(
                    call.id,
                    "Согласовано. Создан только dry-run outbox.",
                )
            elif action in {"all", "employee", "social"}:
                workflow.regenerate(promotion_id, section=action)
                _sync_promotion_best_effort(
                    settings,
                    storage,
                    promotion_id,
                )
                bot.answer_callback_query(call.id, "Новый вариант готов.")
                bot.edit_message_reply_markup(
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=None,
                )
                send_promotion_preview(
                    bot,
                    storage,
                    workflow,
                    call.message.chat.id,
                    promotion_id,
                )
            elif action == "replace":
                service = WeeklyPromotionService(
                    storage,
                    workflow,
                    GoogleSheetsManager(settings),
                )
                replacement = service.replace(promotion_id)
                bot.edit_message_reply_markup(
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=None,
                )
                bot.answer_callback_query(call.id, "Выбрана другая игра.")
                send_promotion_preview(
                    bot,
                    storage,
                    workflow,
                    call.message.chat.id,
                    replacement.promotion_id,
                )
            elif action == "postpone":
                storage.set_promotion_status(promotion_id, "postponed")
                _sync_promotion_best_effort(
                    settings,
                    storage,
                    promotion_id,
                )
                bot.edit_message_reply_markup(
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=None,
                )
                bot.answer_callback_query(call.id, "Промо отложено.")
            else:
                bot.answer_callback_query(call.id, "Неизвестное действие.")
        except Exception as error:
            bot.answer_callback_query(call.id, f"Ошибка: {error}"[:180])

    return True
