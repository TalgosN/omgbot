"""Опциональное Telegram-согласование внутри Виарыча.

Модуль не регистрирует обработчики, пока feature flag выключен. После
согласования используются только DryRunPublisher и локальный outbox.
"""

from .config import Settings
from .db import TrackerStorage
from .llm import build_generator
from .promo import DryRunPublisher, PromotionWorkflow


CALLBACK_PREFIX = "stp"


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
    workflow = PromotionWorkflow(
        storage,
        build_generator(settings),
        DryRunPublisher(),
    )

    def is_allowed(user_id: int) -> bool:
        return user_id in settings.telegram_approver_ids

    def controls(promotion_id: int):
        from telebot import types

        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton(
                "✅ Согласовать",
                callback_data=f"{CALLBACK_PREFIX}:approve:{promotion_id}",
            ),
            types.InlineKeyboardButton(
                "🔄 Переделать всё",
                callback_data=f"{CALLBACK_PREFIX}:all:{promotion_id}",
            ),
        )
        markup.add(
            types.InlineKeyboardButton(
                "👥 Текст сотрудникам",
                callback_data=f"{CALLBACK_PREFIX}:employee:{promotion_id}",
            ),
            types.InlineKeyboardButton(
                "📣 Анонсы",
                callback_data=f"{CALLBACK_PREFIX}:social:{promotion_id}",
            ),
        )
        markup.add(
            types.InlineKeyboardButton(
                "⏸ Отложить",
                callback_data=f"{CALLBACK_PREFIX}:postpone:{promotion_id}",
            )
        )
        return markup

    def send_preview(chat_id: int, promotion_id: int) -> None:
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

        def send_section(title: str, text: str) -> None:
            value = f"{title}\n\n{text}"
            for start in range(0, len(value), 3900):
                bot.send_message(chat_id, value[start : start + 3900])

        send_section("👥 СОТРУДНИКАМ", promo["employee_text"])
        send_section("📣 TELEGRAM", promo["telegram_text"])
        send_section("🔵 VK", promo["vk_text"])
        bot.send_message(
            chat_id,
            (
                f"Промо #{promotion_id}: {promo['game_name']}\n"
                "Публикация выключена: после согласования задания останутся "
                "в dry-run outbox."
            ),
            reply_markup=controls(promotion_id),
        )

    @bot.message_handler(commands=["steam_promo"])
    def steam_promo_command(message):
        if not is_allowed(message.from_user.id):
            bot.reply_to(message, "Нет доступа к согласованию.")
            return
        parts = (message.text or "").split()
        if len(parts) != 2 or not parts[1].isdigit():
            bot.reply_to(message, "Использование: /steam_promo <id>")
            return
        try:
            send_preview(message.chat.id, int(parts[1]))
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
                bot.answer_callback_query(call.id, "Новый вариант готов.")
                send_preview(call.message.chat.id, promotion_id)
            elif action == "postpone":
                storage.set_promotion_status(promotion_id, "postponed")
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
