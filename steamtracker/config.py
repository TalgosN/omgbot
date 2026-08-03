import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CATALOG_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1VYcdmS5B6-cGpawVZZpc8qpiwDnjlSNaNyLM43eBJKI/"
    "export?format=csv&gid=300268818"
)
DEFAULT_SPREADSHEET_ID = "1VYcdmS5B6-cGpawVZZpc8qpiwDnjlSNaNyLM43eBJKI"


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}


def _env_ids(name: str) -> frozenset[int]:
    value = os.getenv(name, "")
    result: set[int] = set()
    for item in value.split(","):
        item = item.strip()
        if item:
            result.add(int(item))
    return frozenset(result)


def _env_optional_int(*names: str) -> int | None:
    for name in names:
        value = os.getenv(name)
        if value and value.strip():
            try:
                return int(value.strip())
            except ValueError as error:
                raise ValueError(
                    f"{name} должен содержать числовой Telegram chat ID"
                ) from error
    return None


@dataclass(frozen=True)
class Settings:
    db_path: Path
    legacy_db_path: Path
    catalog_url: str
    steam_api_key: str | None
    publish_mode: str
    removal_threshold: int
    generator_provider: str
    openrouter_api_key: str | None
    openrouter_model: str | None
    telegram_approval_enabled: bool
    telegram_approver_ids: frozenset[int]
    steam_sync_enabled: bool
    store_enrichment_enabled: bool
    spreadsheet_id: str
    google_service_account_file: Path
    catalog_sync_enabled: bool
    google_export_enabled: bool
    weekly_promo_enabled: bool
    employee_delivery_enabled: bool
    employee_chat_id: int | None

    @classmethod
    def from_env(cls) -> "Settings":
        employee_delivery_enabled = _env_bool(
            "EMPLOYEE_DELIVERY_ENABLED"
        )
        employee_chat_id = (
            _env_optional_int(
                "STEAMTRACKER_EMPLOYEE_CHAT_ID",
                "CHAT_MAIN_GROUP",
            )
            if employee_delivery_enabled
            else None
        )
        return cls(
            db_path=Path(
                os.getenv(
                    "STEAMTRACKER_DB_PATH",
                    PROJECT_DIR / "db" / "steamtracker_v2.db",
                )
            ),
            legacy_db_path=Path(
                os.getenv(
                    "STEAMTRACKER_LEGACY_DB_PATH",
                    PROJECT_DIR
                    / "dump"
                    / "steamtracker"
                    / "steam_stats.db",
                )
            ),
            catalog_url=os.getenv(
                "STEAMTRACKER_CATALOG_URL",
                DEFAULT_CATALOG_URL,
            ),
            steam_api_key=os.getenv("STEAM_API_KEY") or None,
            publish_mode=os.getenv("PUBLISH_MODE", "dry_run"),
            removal_threshold=int(os.getenv("LICENSE_REMOVAL_THRESHOLD", "3")),
            generator_provider=os.getenv(
                "STEAMTRACKER_GENERATOR",
                "fake",
            ).strip().casefold(),
            openrouter_api_key=os.getenv("OPENROUTER_API_KEY") or None,
            openrouter_model=os.getenv("OPENROUTER_MODEL") or None,
            telegram_approval_enabled=_env_bool(
                "STEAMTRACKER_TELEGRAM_APPROVAL_ENABLED"
            ),
            telegram_approver_ids=_env_ids(
                "STEAMTRACKER_APPROVER_IDS"
            ),
            steam_sync_enabled=_env_bool("STEAMTRACKER_SYNC_ENABLED"),
            store_enrichment_enabled=_env_bool(
                "STEAMTRACKER_STORE_ENRICHMENT_ENABLED"
            ),
            spreadsheet_id=os.getenv(
                "STEAMTRACKER_SPREADSHEET_ID",
                DEFAULT_SPREADSHEET_ID,
            ),
            google_service_account_file=Path(
                os.getenv(
                    "STEAMTRACKER_GOOGLE_SERVICE_ACCOUNT_FILE",
                    PROJECT_DIR
                    / "key"
                    / "omgbot-430116-e9a4d9c69b7f.json",
                )
            ),
            catalog_sync_enabled=_env_bool(
                "STEAMTRACKER_CATALOG_SYNC_ENABLED"
            ),
            google_export_enabled=_env_bool(
                "STEAMTRACKER_GOOGLE_EXPORT_ENABLED"
            ),
            weekly_promo_enabled=_env_bool(
                "STEAMTRACKER_WEEKLY_PROMO_ENABLED"
            ),
            employee_delivery_enabled=employee_delivery_enabled,
            employee_chat_id=employee_chat_id,
        )
