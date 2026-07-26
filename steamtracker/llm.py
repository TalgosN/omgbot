"""Настоящий OpenRouter-генератор с проверяемым JSON и fake fallback."""

import json
from typing import Any

import requests

from .config import Settings
from .promo import ContentGenerator, FakeGenerator, GeneratedTexts


PROMPT_VERSION = "steamtracker-promo-v1"


def _json_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def generation_facts(context: dict) -> dict:
    return {
        "game_name": context["game_name"],
        "steam_app_id": context["app_id"],
        "player_count": context.get("player_count"),
        "description": context.get("base_description"),
        "genres": _json_list(context.get("genres_json")),
        "categories": _json_list(context.get("categories_json")),
        "discount": context["discount_text"],
        "valid_from": context.get("valid_from"),
        "valid_to": context.get("valid_to"),
        "manager_comment": context.get("manager_comment"),
    }


class OpenRouterGenerator:
    ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
    provider_name = "openrouter"
    prompt_version = PROMPT_VERSION

    def __init__(
        self,
        api_key: str,
        *,
        model: str | None = None,
        timeout: int = 60,
        session=requests,
    ):
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY не задан")
        self.api_key = api_key
        self.model_name = model
        self.timeout = timeout
        self.session = session

    def generate(self, context: dict) -> GeneratedTexts:
        facts = generation_facts(context)
        body = {
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Ты редактор VR-клуба OMG VR. Используй только факты "
                        "из входного JSON. Не придумывай скидки, количество "
                        "игроков, игровые режимы или условия акции. Верни "
                        "только JSON с ключами employee, telegram и vk."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Создай три разных русскоязычных текста.\n"
                        "employee: обучающая карточка сотрудникам — что за "
                        "игра, кому рекомендовать, что проверить самому, "
                        "количество игроков и точная скидка.\n"
                        "telegram: короткий клиентский анонс с CTA.\n"
                        "vk: более подробный клиентский анонс с CTA.\n"
                        "Точная скидка должна присутствовать во всех трёх "
                        "текстах.\n\nФакты:\n"
                        + json.dumps(facts, ensure_ascii=False)
                    ),
                },
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.6,
        }
        if self.model_name:
            body["model"] = self.model_name

        response = self.session.post(
            self.ENDPOINT,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://omgvr.ru",
                "X-Title": "OMG VR Steam Tracker",
            },
            json=body,
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        self.model_name = payload.get("model") or self.model_name
        try:
            content = payload["choices"][0]["message"]["content"]
            result = json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
            raise ValueError("OpenRouter вернул некорректный JSON") from error

        texts = GeneratedTexts(
            employee=self._validate_text(result, "employee", 3500),
            telegram=self._validate_text(result, "telegram", 3500),
            vk=self._validate_text(result, "vk", 6000),
        )
        for text in (texts.employee, texts.telegram, texts.vk):
            if facts["discount"] not in text:
                raise ValueError(
                    "Сгенерированный текст потерял точное значение скидки"
                )
        return texts

    @staticmethod
    def _validate_text(result: dict, key: str, limit: int) -> str:
        value = result.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"OpenRouter не вернул поле {key}")
        value = value.strip()
        if len(value) > limit:
            raise ValueError(f"Поле {key} длиннее {limit} символов")
        return value


def build_generator(settings: Settings) -> ContentGenerator:
    if settings.generator_provider == "fake":
        return FakeGenerator()
    if settings.generator_provider == "openrouter":
        if not settings.openrouter_api_key:
            raise RuntimeError(
                "Для STEAMTRACKER_GENERATOR=openrouter нужен "
                "OPENROUTER_API_KEY"
            )
        return OpenRouterGenerator(
            settings.openrouter_api_key,
            model=settings.openrouter_model,
        )
    raise RuntimeError(
        f"Неизвестный STEAMTRACKER_GENERATOR: "
        f"{settings.generator_provider}"
    )
