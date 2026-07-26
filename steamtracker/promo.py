"""Генерация, согласование и dry-run доставка промо-материалов."""

import json
from dataclasses import dataclass
from typing import Protocol

from .db import TrackerStorage


@dataclass(frozen=True)
class GeneratedTexts:
    employee: str
    telegram: str
    vk: str


class ContentGenerator(Protocol):
    def generate(self, context: dict) -> GeneratedTexts:
        ...


def _period(context: dict) -> str:
    if context.get("valid_from") and context.get("valid_to"):
        return f"с {context['valid_from']} по {context['valid_to']}"
    if context.get("valid_from"):
        return f"с {context['valid_from']}"
    if context.get("valid_to"):
        return f"до {context['valid_to']}"
    return "в период акции"


def _short_description(value: str | None, limit: int = 320) -> str:
    if not value:
        return "Базовое описание пока не заполнено менеджером."
    value = " ".join(value.split())
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


class FakeGenerator:
    """Детерминированный генератор для проверки процесса без LLM-ключа."""

    provider_name = "fake"
    model_name = "deterministic-template"
    prompt_version = "steamtracker-fake-v1"

    def generate(self, context: dict) -> GeneratedTexts:
        name = context["game_name"]
        players = context.get("player_count") or "не указано"
        discount = context["discount_text"]
        period = _period(context)
        description = _short_description(context.get("base_description"))
        manager_comment = context.get("manager_comment")
        comment_line = (
            f"\nКомментарий менеджера: {manager_comment}"
            if manager_comment
            else ""
        )

        employee = (
            f"[DRY RUN] Игра недели для сотрудников: {name}\n"
            f"Количество игроков: {players}\n"
            f"Скидка: {discount}, {period}.\n\n"
            f"Что это за игра: {description}\n"
            "Задача администратора: самостоятельно изучить запуск и "
            "предлагать игру подходящим клиентам."
            f"{comment_line}"
        )
        telegram = (
            f"[DRY RUN] 🎮 {name}\n\n"
            f"{description}\n\n"
            f"👥 Игроков: {players}\n"
            f"🔥 Скидка: {discount}, {period}.\n"
            "Уточняйте свободное время и приходите играть!"
        )
        vk = (
            f"[DRY RUN] Игра недели — {name}\n\n"
            f"{description}\n\n"
            f"Можно играть: {players}. "
            f"На игру действует скидка {discount} {period}.\n\n"
            "Собирайте команду и бронируйте удобное время в OMG VR."
        )
        return GeneratedTexts(
            employee=employee,
            telegram=telegram,
            vk=vk,
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
