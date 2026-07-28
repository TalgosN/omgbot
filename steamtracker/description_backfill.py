"""Разовая генерация и безопасное применение описаний каталога."""

import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from html import unescape
from pathlib import Path
from typing import Protocol

import requests

from .config import Settings
from .db import TrackerStorage


ARTIFACT_VERSION = 1
PROMPT_VERSION = "steamtracker-manager-description-v1"
DEFAULT_ARTIFACT_PATH = Path(
    "Reports/steamtracker_manager_descriptions.json"
)
APPLY_CONFIRMATION = "APPLY_MANAGER_DESCRIPTIONS"


@dataclass(frozen=True)
class ManagerDescription:
    description: str
    audience: str

    @property
    def text(self) -> str:
        return (
            f"{self.description}\n\n"
            f"Кому рекомендовать: {self.audience}"
        )


class ManagerDescriptionGenerator(Protocol):
    model_name: str | None

    def generate(self, facts: dict) -> ManagerDescription:
        ...


class OpenRouterManagerDescriptionGenerator:
    """Отдельный строгий промпт поверх текущей модели OpenRouter."""

    ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"

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

    def generate(self, facts: dict) -> ManagerDescription:
        body = {
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Ты редактор внутреннего каталога VR-клуба OMG VR. "
                        "Используй только факты из входного JSON. Не "
                        "придумывай игровые режимы, механику, возрастные "
                        "ограничения, число игроков или другие факты. "
                        "Ответ должен быть на русском языке, без HTML и "
                        "Markdown. Верни только JSON указанной структуры."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Подготовь понятное и живое описание игры для "
                        "сотрудника VR-клуба.\n\n"
                        "Правила:\n"
                        "1. Не изменяй и не повторяй название игры.\n"
                        "2. В description кратко объясни суть, атмосферу и "
                        "главный игровой опыт. Допустимы 1-2 коротких "
                        "абзаца.\n"
                        "3. В audience объясни, каким гостям её предлагать. "
                        "Не добавляй заголовок «Кому рекомендовать».\n"
                        "4. Не упоминай скидки, акции, цены, публикации и "
                        "социальные сети.\n"
                        "5. Не пиши про проверку оборудования, пространства "
                        "или другие очевидные обязанности администратора.\n"
                        "6. Не делай вывод, что несколько гостей могут играть "
                        "одновременно в одном клубе, зале или на одном ПК. "
                        "Steam-категории описывают режимы игры, а не схему "
                        "подключения в клубе.\n"
                        "7. Не используй списки и длинное тире (—).\n\n"
                        "Формат ответа строго JSON:\n"
                        "{\n"
                        '  "description": "строка",\n'
                        '  "audience": "строка"\n'
                        "}\n\n"
                        "Факты:\n"
                        + json.dumps(facts, ensure_ascii=False)
                    ),
                },
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.4,
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

        description = _validated_part(
            result,
            "description",
            minimum=80,
            maximum=1000,
        )
        audience = _validated_part(
            result,
            "audience",
            minimum=30,
            maximum=500,
        )
        prefix = "кому рекомендовать:"
        if audience.casefold().startswith(prefix):
            audience = audience[len(prefix):].strip()
        proposal = ManagerDescription(description, audience)
        validate_manager_description(proposal.text)
        return proposal


def build_manager_description_generator(
    settings: Settings,
) -> OpenRouterManagerDescriptionGenerator:
    if settings.generator_provider != "openrouter":
        raise RuntimeError(
            "Для генерации менеджерских описаний установите "
            "STEAMTRACKER_GENERATOR=openrouter"
        )
    if not settings.openrouter_api_key:
        raise RuntimeError(
            "Для генерации менеджерских описаний нужен OPENROUTER_API_KEY"
        )
    return OpenRouterManagerDescriptionGenerator(
        settings.openrouter_api_key,
        model=settings.openrouter_model,
    )


def generate_description_artifact(
    storage: TrackerStorage,
    generator: ManagerDescriptionGenerator,
    output_path: Path,
    *,
    limit: int | None = None,
    resume: bool = False,
    app_ids: list[int] | None = None,
    workers: int = 1,
) -> dict:
    if limit is not None and limit < 1:
        raise ValueError("--limit должен быть не меньше 1")
    if not 1 <= workers <= 5:
        raise ValueError("--workers должен быть от 1 до 5")
    output_path = Path(output_path)
    artifact = (
        _load_artifact(output_path)
        if resume
        else _new_artifact(generator.model_name)
    )
    if not resume and output_path.exists():
        raise FileExistsError(
            f"Файл уже существует: {output_path}. "
            "Используйте --resume или укажите другой --output."
        )
    if artifact["prompt_version"] != PROMPT_VERSION:
        raise ValueError(
            "Версия промпта в существующем файле отличается. "
            "Создайте новый файл."
        )

    selected = set(app_ids or [])
    existing = {
        int(item["app_id"]): item
        for item in artifact.get("items", [])
    }
    candidates: list[dict] = []
    generated = 0
    failed = 0
    skipped_existing = 0
    rows = storage.managed_games(status="active", limit=500)
    for catalog_row in rows:
        app_id = int(catalog_row["app_id"])
        if selected and app_id not in selected:
            continue
        row = dict(storage.managed_game(app_id))
        if str(row.get("manager_description") or "").strip():
            skipped_existing += 1
            continue
        previous = existing.get(app_id)
        if previous and previous.get("status") == "generated":
            skipped_existing += 1
            continue
        if limit is not None and len(candidates) >= limit:
            break
        candidates.append(row)

    if workers == 1:
        items = (
            _generate_artifact_item(row, generator)
            for row in candidates
        )
        executor = None
    else:
        executor = ThreadPoolExecutor(
            max_workers=min(workers, max(1, len(candidates)))
        )
        futures = [
            executor.submit(_generate_artifact_item, row, generator)
            for row in candidates
        ]
        items = (future.result() for future in as_completed(futures))
    try:
        for item in items:
            if item["status"] == "generated":
                generated += 1
            else:
                failed += 1
            existing[int(item["app_id"])] = item
            artifact["items"] = sorted(
                existing.values(),
                key=lambda value: (
                    str(value.get("steam_name") or "").casefold(),
                    int(value["app_id"]),
                ),
            )
            artifact["model"] = generator.model_name
            artifact["updated_at"] = _now()
            _write_artifact(output_path, artifact)
    finally:
        if executor is not None:
            executor.shutdown(wait=True)

    if not output_path.exists():
        artifact["items"] = list(existing.values())
        _write_artifact(output_path, artifact)
    return {
        "output": str(output_path),
        "attempted": len(candidates),
        "generated": generated,
        "failed": failed,
        "skipped_existing": skipped_existing,
        "total_in_artifact": len(artifact["items"]),
        "model": generator.model_name,
    }


def _generate_artifact_item(
    row: dict,
    generator: ManagerDescriptionGenerator,
) -> dict:
    item = _artifact_item(row)
    try:
        if not item["source_description"]:
            raise ValueError("У игры нет исходного описания Steam")
        proposal = generator.generate(_generation_facts(row))
        item.update(
            {
                "status": "generated",
                "proposed_description": proposal.text,
                "model": generator.model_name,
                "error": None,
            }
        )
    except Exception as error:
        item.update(
            {
                "status": "error",
                "proposed_description": None,
                "model": generator.model_name,
                "error": str(error),
            }
        )
    return item


def apply_description_artifact(
    storage: TrackerStorage,
    input_path: Path,
    *,
    confirmation: str,
) -> dict:
    if confirmation != APPLY_CONFIRMATION:
        raise ValueError(
            f"Для применения передайте --confirm {APPLY_CONFIRMATION}"
        )
    input_path = Path(input_path)
    artifact = _load_artifact(input_path)
    applied = 0
    skipped = 0
    failed = 0
    errors: list[str] = []
    for item in artifact.get("items", []):
        if item.get("status") != "generated":
            continue
        app_id = int(item["app_id"])
        try:
            row = dict(storage.managed_game(app_id))
            if row["catalog_status"] != "active":
                raise ValueError("игра больше не активна")
            if str(row.get("manager_description") or "").strip():
                item["apply_status"] = "skipped_existing"
                skipped += 1
                continue
            if str(row["steam_name"]) != str(item["steam_name"]):
                raise ValueError("название игры изменилось")
            if _source_fingerprint(row) != item.get("source_fingerprint"):
                raise ValueError("исходные данные Steam изменились")
            proposal = str(item.get("proposed_description") or "").strip()
            validate_manager_description(proposal)
            storage.update_managed_game(
                app_id,
                player_count=row["player_count"],
                manager_description=proposal,
                manager_comment=row["manager_comment"],
                actor_id="description-backfill",
                actor_name="OpenRouter description backfill",
            )
            item["apply_status"] = "applied"
            item["applied_at"] = _now()
            item.pop("apply_error", None)
            applied += 1
        except Exception as error:
            item["apply_status"] = "error"
            item["apply_error"] = str(error)
            failed += 1
            errors.append(f"AppID {app_id}: {error}")
        artifact["updated_at"] = _now()
        _write_artifact(input_path, artifact)
    artifact["updated_at"] = _now()
    _write_artifact(input_path, artifact)
    return {
        "input": str(input_path),
        "applied": applied,
        "skipped": skipped,
        "failed": failed,
        "errors": errors,
    }


def validate_manager_description(value: str) -> None:
    text = str(value or "").strip()
    if not 120 <= len(text) <= 1600:
        raise ValueError(
            "Менеджерское описание должно содержать от 120 до 1600 символов"
        )
    if not re.search(r"[А-Яа-яЁё]", text):
        raise ValueError("Менеджерское описание должно быть на русском языке")
    if re.search(r"<[^>]+>", text):
        raise ValueError("HTML в менеджерском описании запрещён")
    if "—" in text:
        raise ValueError("Длинное тире в менеджерском описании запрещено")
    if re.search(r"\b(?:скидк\w*|акци(?:я|и|ю|ей|ям|ями|ях)|рубл\w*)\b", text, re.I):
        raise ValueError("Менеджерское описание содержит промо-термины")
    if "Кому рекомендовать:" not in text:
        raise ValueError("Не найден блок «Кому рекомендовать»")


def _validated_part(
    result: dict,
    key: str,
    *,
    minimum: int,
    maximum: int,
) -> str:
    value = result.get(key)
    if not isinstance(value, str):
        raise ValueError(f"OpenRouter не вернул поле {key}")
    value = unescape(value).replace("—", "-").strip()
    if not minimum <= len(value) <= maximum:
        raise ValueError(
            f"Поле {key} должно содержать от {minimum} до "
            f"{maximum} символов"
        )
    return value


def _decode_list(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    try:
        decoded = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    return [str(item) for item in decoded] if isinstance(decoded, list) else []


def _source_description(row: dict) -> str:
    return str(
        row.get("store_description")
        or row.get("base_description")
        or ""
    ).strip()


def _generation_facts(row: dict) -> dict:
    return {
        "steam_app_id": int(row["app_id"]),
        "game_name": str(row["steam_name"]),
        "source_description": _source_description(row),
        "source_language": row.get("source_language"),
        "player_count": row.get("player_count"),
        "genres": _decode_list(row.get("genres_json")),
        "categories": _decode_list(row.get("categories_json")),
    }


def _source_fingerprint(row: dict) -> str:
    payload = json.dumps(
        _generation_facts(row),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _artifact_item(row: dict) -> dict:
    return {
        "app_id": int(row["app_id"]),
        "steam_name": str(row["steam_name"]),
        "source_description": _source_description(row),
        "source_language": row.get("source_language"),
        "source_fingerprint": _source_fingerprint(row),
        "status": "pending",
        "proposed_description": None,
        "model": None,
        "error": None,
    }


def _new_artifact(model_name: str | None) -> dict:
    timestamp = _now()
    return {
        "format_version": ARTIFACT_VERSION,
        "prompt_version": PROMPT_VERSION,
        "model": model_name,
        "created_at": timestamp,
        "updated_at": timestamp,
        "items": [],
    }


def _load_artifact(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Файл не найден: {path}")
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"Некорректный JSON: {path}") from error
    if artifact.get("format_version") != ARTIFACT_VERSION:
        raise ValueError("Неподдерживаемая версия файла описаний")
    if not isinstance(artifact.get("items"), list):
        raise ValueError("В файле описаний отсутствует список items")
    return artifact


def _write_artifact(path: Path, artifact: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
