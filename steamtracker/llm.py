"""Настоящий OpenRouter-генератор с проверяемым JSON и fake fallback."""

import json
from html import unescape
from typing import Any

import requests

from .config import Settings
from .promo import (
    ContentDraft,
    ContentGenerator,
    FakeGenerator,
    GeneratedTexts,
    render_content,
)


PROMPT_VERSION = "steamtracker-promo-v3"


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
                        "Ты SMM-редактор VR-клуба OMG VR. Используй только "
                        "факты из входного JSON. Не придумывай игровые "
                        "режимы, механики или другие факты. Не пиши название "
                        "игры, скидку, даты и количество игроков: система "
                        "добавит их сама без искажений. Не используй HTML. "
                        "Верни только JSON указанной структуры."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Подготовь русскоязычные смысловые блоки.\n\n"
                        "Правила стиля:\n"
                        "1. Пиши живо, естественно и вовлекающе.\n"
                        "2. Не используй длинное тире (—), только дефис (-) "
                        "или запятую.\n"
                        "3. Не изменяй и не повторяй название игры: в ответе "
                        "называй её «эта игра» или перестрой предложение.\n"
                        "4. Для сотрудника дай только понятное описание и "
                        "кому игру рекомендовать. Не пиши про оборудование, "
                        "пространство или очевидные обязанности.\n"
                        "5. Для соцсетей дай 2-4 коротких абзаца. Допускается "
                        "один список из 3-4 преимуществ, только если он "
                        "действительно помогает тексту.\n"
                        "6. Заверши естественным вопросом или призывом к "
                        "бронированию.\n\n"
                        "Формат ответа строго JSON:\n"
                        "{\n"
                        '  "employee_description": "строка",\n'
                        '  "employee_audience": "строка",\n'
                        '  "social_headline": "строка без названия игры",\n'
                        '  "social_paragraphs": ["абзац 1", "абзац 2"],\n'
                        '  "social_benefits": [],\n'
                        '  "social_closing": "строка"\n'
                        "}\n"
                        "`social_benefits` должен быть пустым либо содержать "
                        "3-4 коротких пункта без маркеров.\n\nФакты:\n"
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

        draft = ContentDraft(
            employee_description=self._validate_text(
                result,
                "employee_description",
                1000,
            ),
            employee_audience=self._validate_text(
                result,
                "employee_audience",
                700,
            ),
            social_headline=self._validate_text(
                result,
                "social_headline",
                180,
            ),
            social_paragraphs=tuple(
                self._validate_list(
                    result,
                    "social_paragraphs",
                    minimum=2,
                    maximum=4,
                    item_limit=1000,
                )
            ),
            social_benefits=tuple(
                self._validate_list(
                    result,
                    "social_benefits",
                    minimum=0,
                    maximum=4,
                    item_limit=250,
                    allowed_empty=True,
                )
            ),
            social_closing=self._validate_text(
                result,
                "social_closing",
                400,
            ),
        )
        if draft.social_benefits and len(draft.social_benefits) < 3:
            raise ValueError(
                "social_benefits должен быть пустым либо содержать 3-4 пункта"
            )
        texts = render_content(context, draft)
        for key, text, limit in (
            ("employee", texts.employee, 3500),
            ("telegram", texts.telegram, 3500),
            ("vk", texts.vk, 6000),
        ):
            if len(text) > limit:
                raise ValueError(f"Поле {key} длиннее {limit} символов")
            if facts["discount"] not in text:
                raise ValueError(
                    "Сгенерированный текст потерял точное значение скидки"
                )
            if facts["game_name"] not in unescape(text):
                raise ValueError(
                    "Сгенерированный текст потерял точное название игры"
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

    @staticmethod
    def _validate_list(
        result: dict,
        key: str,
        *,
        minimum: int,
        maximum: int,
        item_limit: int,
        allowed_empty: bool = False,
    ) -> list[str]:
        value = result.get(key)
        if not isinstance(value, list):
            raise ValueError(f"OpenRouter не вернул список {key}")
        if not value and allowed_empty:
            return []
        if not minimum <= len(value) <= maximum:
            raise ValueError(
                f"Поле {key} должно содержать от {minimum} до "
                f"{maximum} элементов"
            )
        items: list[str] = []
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise ValueError(f"Поле {key} содержит пустой элемент")
            item = item.strip()
            if len(item) > item_limit:
                raise ValueError(
                    f"Элемент {key} длиннее {item_limit} символов"
                )
            items.append(item)
        return items


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
