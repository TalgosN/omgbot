"""Еженедельный выбор игры, генерация промо и запись в Google Sheets."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from .db import TrackerStorage, WeeklyPromotionSelection
from .promo import PromotionWorkflow
from .sheets import GoogleSheetsManager


MOSCOW = timezone(timedelta(hours=3), name="Europe/Moscow")


def setting_enabled(value: object) -> bool:
    return str(value or "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
        "да",
    }


def week_period(reference_date: date | None = None) -> tuple[date, date]:
    current = reference_date or datetime.now(MOSCOW).date()
    monday = current - timedelta(days=current.weekday())
    return monday, monday + timedelta(days=6)


@dataclass(frozen=True)
class WeeklyPromotionResult:
    promotion_id: int
    app_id: int
    cycle_number: int
    valid_from: str
    valid_to: str
    created: bool
    generated: bool


class WeeklyPromotionService:
    def __init__(
        self,
        storage: TrackerStorage,
        workflow: PromotionWorkflow,
        sheets: GoogleSheetsManager,
        *,
        rng=None,
    ):
        self.storage = storage
        self.workflow = workflow
        self.sheets = sheets
        self.rng = rng

    def preview(self) -> dict:
        settings = self.storage.tracker_settings()
        start, end = week_period()
        return {
            **self.storage.rotation_summary(),
            "weekly_discount": settings.get("weekly_discount", ""),
            "weekly_promo_enabled": setting_enabled(
                settings.get("weekly_promo_enabled")
            ),
            "valid_from": start.isoformat(),
            "valid_to": end.isoformat(),
        }

    def run(
        self,
        *,
        reference_date: date | None = None,
        force: bool = False,
    ) -> WeeklyPromotionResult:
        settings = self.storage.tracker_settings()
        if not force and not setting_enabled(
            settings.get("weekly_promo_enabled")
        ):
            raise RuntimeError(
                "Автоматическая игра недели выключена в настройках бота"
            )
        discount = str(settings.get("weekly_discount") or "").strip()
        if not discount:
            raise RuntimeError(
                "Размер скидки не заполнен в настройках бота"
            )

        start, end = week_period(reference_date)
        selection = self.storage.create_random_weekly_promotion(
            discount_text=discount,
            valid_from=start.isoformat(),
            valid_to=end.isoformat(),
            rng=self.rng,
        )
        generated = self._generate_if_needed(selection)
        self.sheets.sync_promotion(
            self.storage,
            selection.promotion_id,
            apply=True,
        )
        return WeeklyPromotionResult(
            promotion_id=selection.promotion_id,
            app_id=selection.app_id,
            cycle_number=selection.cycle_number,
            valid_from=start.isoformat(),
            valid_to=end.isoformat(),
            created=selection.created,
            generated=generated,
        )

    def replace(self, promotion_id: int) -> WeeklyPromotionResult:
        previous = dict(self.storage.promotion_sheet_row(promotion_id))
        if previous["status"] == "approved":
            raise ValueError("Согласованное промо нельзя заменить")
        self.storage.set_promotion_status(promotion_id, "cancelled")
        self.sheets.sync_promotion(
            self.storage,
            promotion_id,
            apply=True,
        )
        selection = self.storage.create_random_weekly_promotion(
            discount_text=previous["discount_text"],
            valid_from=previous["valid_from"],
            valid_to=previous["valid_to"],
            exclude_app_ids={int(previous["app_id"])},
            rng=self.rng,
        )
        generated = self._generate_if_needed(selection)
        self.sheets.sync_promotion(
            self.storage,
            selection.promotion_id,
            apply=True,
        )
        return WeeklyPromotionResult(
            promotion_id=selection.promotion_id,
            app_id=selection.app_id,
            cycle_number=selection.cycle_number,
            valid_from=previous["valid_from"],
            valid_to=previous["valid_to"],
            created=selection.created,
            generated=generated,
        )

    def _generate_if_needed(
        self,
        selection: WeeklyPromotionSelection,
    ) -> bool:
        promotion = dict(
            self.storage.promotion_context(selection.promotion_id)
        )
        if all(
            (
                promotion.get("employee_text"),
                promotion.get("telegram_text"),
                promotion.get("vk_text"),
            )
        ):
            return False
        self.workflow.generate(selection.promotion_id)
        return True
