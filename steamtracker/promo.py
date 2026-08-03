"""Генерация, согласование и dry-run доставка промо-материалов."""

import json
from datetime import date
from dataclasses import dataclass
from html import escape
from typing import Protocol

from .db import TrackerStorage


@dataclass(frozen=True)
class GeneratedTexts:
    employee: str
    telegram: str
    vk: str


@dataclass(frozen=True)
class ContentDraft:
    employee_description: str
    employee_audience: str
    social_headline: str
    social_paragraphs: tuple[str, ...]
    social_benefits: tuple[str, ...]
    social_closing: str


class ContentGenerator(Protocol):
    def generate(self, context: dict) -> GeneratedTexts:
        ...


MONTHS = (
    "",
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)


def _period(context: dict) -> str:
    valid_from = context.get("valid_from")
    valid_to = context.get("valid_to")
    start = date.fromisoformat(valid_from) if valid_from else None
    end = date.fromisoformat(valid_to) if valid_to else None
    if start and end:
        if start.year == end.year and start.month == end.month:
            return f"{start.day}-{end.day} {MONTHS[end.month]}"
        return (
            f"{start.day} {MONTHS[start.month]} - "
            f"{end.day} {MONTHS[end.month]}"
        )
    if start:
        return f"с {start.day} {MONTHS[start.month]}"
    if end:
        return f"до {end.day} {MONTHS[end.month]}"
    return "в период акции"


def _social_period(context: dict) -> str:
    valid_from = context.get("valid_from")
    valid_to = context.get("valid_to")
    start = date.fromisoformat(valid_from) if valid_from else None
    end = date.fromisoformat(valid_to) if valid_to else None
    if start and end:
        if start.year == end.year and start.month == end.month:
            return f"с {start.day} по {end.day} {MONTHS[end.month]}"
        return (
            f"с {start.day} {MONTHS[start.month]} "
            f"по {end.day} {MONTHS[end.month]}"
        )
    if start:
        return f"с {start.day} {MONTHS[start.month]}"
    if end:
        return f"до {end.day} {MONTHS[end.month]}"
    return "в период акции"


def _discount_phrase(value: str) -> str:
    value = " ".join(value.split())
    return value if "скид" in value.casefold() else f"скидка {value}"


def _clean_generated(value: str) -> str:
    return " ".join(value.replace("—", "-").split())


def render_content(context: dict, draft: ContentDraft) -> GeneratedTexts:
    name = str(context["game_name"]).strip()
    discount = str(context["discount_text"]).strip()
    players = int(context.get("player_count") or 1)
    player_text = str(players) if players == 1 else f"до {players}"
    period = _period(context)
    social_period = _social_period(context)
    discount_phrase = _discount_phrase(discount)

    description = _clean_generated(draft.employee_description)
    audience = _clean_generated(draft.employee_audience)
    headline = _clean_generated(draft.social_headline)
    paragraphs = tuple(
        _clean_generated(value) for value in draft.social_paragraphs
    )
    benefits = tuple(
        _clean_generated(value) for value in draft.social_benefits
    )
    closing = _clean_generated(draft.social_closing)

    employee_actions = [
        "• самостоятельно запустить игру и сыграть;",
        "• проверить и выучить управление;",
    ]
    if players > 1:
        employee_actions.append(
            "• проверить подключение нескольких игроков;"
        )
    employee_actions.append(
        "• понять, каким клиентам рекомендовать игру."
    )
    employee = (
        f"<b>🎮 Игра недели: {escape(name)}</b>\n\n"
        f"<b>Что это:</b>\n{escape(description)}\n\n"
        f"<b>Кому рекомендовать:</b>\n{escape(audience)}\n\n"
        f"<b>Количество игроков:</b> {player_text}\n"
        f"<b>Акция:</b> {escape(discount_phrase)}\n"
        f"<b>Период:</b> {period}\n\n"
        "<b>Что должен сделать администратор:</b>\n"
        + "\n".join(employee_actions)
    )

    telegram_blocks = [
        f"<b>🎮 {escape(name)}: {escape(headline)}</b>",
        *[escape(value) for value in paragraphs],
    ]
    vk_blocks = [
        f"🎮 {name}: {headline}",
        *paragraphs,
    ]
    if benefits:
        telegram_blocks.append(
            "<b>⚔️ Почему стоит попробовать?</b>\n"
            + "\n".join(f"• {escape(value)};" for value in benefits)
        )
        vk_blocks.append(
            "⚔️ Почему стоит попробовать?\n"
            + "\n".join(f"• {value};" for value in benefits)
        )
    telegram_blocks.extend(
        [
            (
                f"На игру действует <b>{escape(discount_phrase)}</b>. "
                f"Предложение актуально {social_period}."
            ),
            escape(closing),
        ]
    )
    vk_blocks.extend(
        [
            (
                f"На игру действует {discount_phrase}. "
                f"Предложение актуально {social_period}."
            ),
            closing,
        ]
    )
    return GeneratedTexts(
        employee=employee,
        telegram="\n\n".join(telegram_blocks),
        vk="\n\n".join(vk_blocks),
    )


class FakeGenerator:
    """Детерминированный генератор для проверки процесса без LLM-ключа."""

    provider_name = "fake"
    model_name = "deterministic-template"
    prompt_version = "steamtracker-fake-v3"

    def generate(self, context: dict) -> GeneratedTexts:
        description = context.get("base_description") or (
            "VR-игра из согласованного каталога клуба."
        )
        return render_content(
            context,
            ContentDraft(
                employee_description=str(description),
                employee_audience=(
                    "Гостям, которым подходит жанр и формат этой VR-игры."
                ),
                social_headline="пора попробовать что-то новое!",
                social_paragraphs=(
                    str(description),
                    (
                        "Приходите познакомиться с игрой недели и получить "
                        "новые впечатления в виртуальной реальности!"
                    ),
                ),
                social_benefits=(),
                social_closing=(
                    "Бронируйте удобное время и приходите играть! 👇"
                ),
            ),
        )


@dataclass(frozen=True)
class PublishResult:
    status: str
    external_id: str | None = None


class Publisher(Protocol):
    def publish(self, channel: str, payload: dict) -> PublishResult:
        ...


class DryRunPublisher:
    def publish(self, channel: str, payload: dict) -> PublishResult:
        if channel not in {"employees", "telegram", "vk"}:
            raise ValueError(f"Неизвестный канал: {channel}")
        if not payload.get("text"):
            raise ValueError("Пустой текст публикации")
        return PublishResult(status="ready_dry_run")


class EmployeeTelegramPublisher:
    """Отправляет сотруднический текст, сохраняя соцсети в dry-run."""

    def __init__(self, bot, chat_id: int):
        self.bot = bot
        self.chat_id = int(chat_id)
        self.dry_run = DryRunPublisher()

    def publish(self, channel: str, payload: dict) -> PublishResult:
        if channel != "employees":
            return self.dry_run.publish(channel, payload)
        text = str(payload.get("text") or "").strip()
        if not text:
            raise ValueError("Пустой текст для сотрудников")
        message = self.bot.send_message(
            self.chat_id,
            text,
            parse_mode=payload.get("parse_mode") or "HTML",
        )
        return PublishResult(
            status="sent",
            external_id=str(message.message_id),
        )


class PromotionWorkflow:
    def __init__(
        self,
        storage: TrackerStorage,
        generator: ContentGenerator,
        publisher: Publisher,
    ):
        self.storage = storage
        self.generator = generator
        self.publisher = publisher

    def generate(self, promotion_id: int) -> GeneratedTexts:
        row = self.storage.promotion_context(promotion_id)
        context = dict(row)
        texts = self.generator.generate(context)
        self.storage.save_generated_texts(
            promotion_id,
            employee_text=texts.employee,
            telegram_text=texts.telegram,
            vk_text=texts.vk,
        )
        self._record_generation(promotion_id, context, texts)
        return texts

    def regenerate(
        self,
        promotion_id: int,
        *,
        section: str,
    ) -> GeneratedTexts:
        if section not in {"all", "employee", "social"}:
            raise ValueError(f"Неизвестная секция генерации: {section}")
        row = self.storage.promotion_context(promotion_id)
        context = dict(row)
        texts = self.generator.generate(context)
        if section == "all":
            self.storage.save_generated_texts(
                promotion_id,
                employee_text=texts.employee,
                telegram_text=texts.telegram,
                vk_text=texts.vk,
            )
        elif section == "employee":
            self.storage.save_partial_generated_texts(
                promotion_id,
                employee_text=texts.employee,
            )
        else:
            self.storage.save_partial_generated_texts(
                promotion_id,
                telegram_text=texts.telegram,
                vk_text=texts.vk,
            )
        self._record_generation(promotion_id, context, texts)
        return texts

    def _record_generation(
        self,
        promotion_id: int,
        context: dict,
        texts: GeneratedTexts,
    ) -> None:
        self.storage.record_generation(
            promotion_id,
            provider=getattr(
                self.generator,
                "provider_name",
                self.generator.__class__.__name__,
            ),
            model=getattr(self.generator, "model_name", None),
            prompt_version=getattr(
                self.generator,
                "prompt_version",
                "unknown",
            ),
            input_data=context,
            output_data={
                "employee": texts.employee,
                "telegram": texts.telegram,
                "vk": texts.vk,
            },
        )

    def approve_and_dispatch(
        self,
        promotion_id: int,
        *,
        approved_by: str,
    ) -> None:
        self.storage.approve_promotion(promotion_id, approved_by)
        for row in self.storage.pending_outbox():
            if row["promotion_id"] != promotion_id:
                continue
            try:
                result = self.publisher.publish(
                    row["channel"],
                    json.loads(row["payload_json"]),
                )
                self.storage.mark_outbox(
                    row["id"],
                    status=result.status,
                    external_id=result.external_id,
                )
            except Exception as error:
                self.storage.mark_outbox(
                    row["id"],
                    status="error",
                    error=str(error),
                )
