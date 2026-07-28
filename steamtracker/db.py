"""Хранилище лицензий, дневной динамики и промо-процесса."""

import json
import random
import sqlite3
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Iterable, Iterator, Sequence

import pytz


PROMOTION_STATUSES = {
    "draft",
    "review",
    "approved",
    "postponed",
    "cancelled",
}
PROMOTION_STATUS_TRANSITIONS = {
    "draft": {"postponed", "cancelled"},
    "review": {"postponed", "cancelled"},
    "approved": set(),
    "postponed": {"cancelled"},
    "cancelled": set(),
}
GAME_CATALOG_STATUSES = {
    "active",
    "draft",
    "paused",
    "excluded",
}
TRACKER_SETTING_DEFAULTS = {
    "weekly_discount": (
        "100 рублей",
        "Точное значение скидки для игры недели",
    ),
    "generation_day": (
        "monday",
        "День автоматического выбора игры",
    ),
    "generation_time": (
        "10:30",
        "Время по часовому поясу Steam Tracker",
    ),
    "timezone": (
        "Europe/Moscow",
        "Часовой пояс автоматического задания",
    ),
    "weekly_promo_enabled": (
        "false",
        "Автоматически запускать согласование игры недели",
    ),
}


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class OwnedGame:
    app_id: int
    name: str
    playtime_minutes: int


@dataclass(frozen=True)
class ScanResult:
    steam_id: str
    seen_games: int
    added: int
    removed: int


@dataclass(frozen=True)
class WeeklyPromotionSelection:
    promotion_id: int
    app_id: int
    cycle_number: int
    created: bool


class TrackerStorage:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode = WAL;

                CREATE TABLE IF NOT EXISTS accounts (
                    steam_id TEXT PRIMARY KEY,
                    vanity_url TEXT,
                    club_name TEXT,
                    active INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS games (
                    app_id INTEGER PRIMARY KEY,
                    steam_name TEXT NOT NULL,
                    official_name TEXT,
                    is_approved INTEGER NOT NULL DEFAULT 0,
                    managed INTEGER NOT NULL DEFAULT 0,
                    catalog_status TEXT NOT NULL DEFAULT 'draft',
                    player_count INTEGER,
                    base_description TEXT,
                    manager_description TEXT,
                    manager_comment TEXT,
                    description_source TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS account_games (
                    steam_id TEXT NOT NULL,
                    app_id INTEGER NOT NULL,
                    owned INTEGER NOT NULL DEFAULT 1,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    missing_checks INTEGER NOT NULL DEFAULT 0,
                    last_playtime_minutes INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (steam_id, app_id),
                    FOREIGN KEY (steam_id) REFERENCES accounts(steam_id),
                    FOREIGN KEY (app_id) REFERENCES games(app_id)
                );

                CREATE TABLE IF NOT EXISTS license_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    steam_id TEXT NOT NULL,
                    app_id INTEGER NOT NULL,
                    event_type TEXT NOT NULL CHECK(event_type IN ('added', 'removed')),
                    recorded_at TEXT NOT NULL,
                    FOREIGN KEY (steam_id) REFERENCES accounts(steam_id),
                    FOREIGN KEY (app_id) REFERENCES games(app_id)
                );

                CREATE TABLE IF NOT EXISTS playtime_daily (
                    steam_id TEXT NOT NULL,
                    app_id INTEGER NOT NULL,
                    snapshot_date TEXT NOT NULL,
                    playtime_minutes INTEGER NOT NULL,
                    recorded_at TEXT NOT NULL,
                    PRIMARY KEY (steam_id, app_id, snapshot_date),
                    FOREIGN KEY (steam_id) REFERENCES accounts(steam_id),
                    FOREIGN KEY (app_id) REFERENCES games(app_id)
                );

                CREATE TABLE IF NOT EXISTS game_metadata (
                    app_id INTEGER PRIMARY KEY,
                    store_description TEXT,
                    genres_json TEXT NOT NULL DEFAULT '[]',
                    categories_json TEXT NOT NULL DEFAULT '[]',
                    header_image TEXT,
                    screenshots_json TEXT NOT NULL DEFAULT '[]',
                    is_free INTEGER,
                    source_language TEXT,
                    updated_at TEXT NOT NULL,
                    last_error TEXT,
                    FOREIGN KEY (app_id) REFERENCES games(app_id)
                );

                CREATE TABLE IF NOT EXISTS promotions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    app_id INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'draft',
                    discount_text TEXT NOT NULL,
                    valid_from TEXT,
                    valid_to TEXT,
                    manager_comment TEXT,
                    employee_text TEXT,
                    telegram_text TEXT,
                    vk_text TEXT,
                    image_url TEXT,
                    approved_by TEXT,
                    approved_at TEXT,
                    claimed_by TEXT,
                    claimed_name TEXT,
                    claimed_at TEXT,
                    is_test INTEGER NOT NULL DEFAULT 0 CHECK(
                        is_test IN (0, 1)
                    ),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (app_id) REFERENCES games(app_id)
                );

                CREATE TABLE IF NOT EXISTS content_generations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    promotion_id INTEGER NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT,
                    prompt_version TEXT NOT NULL,
                    input_json TEXT NOT NULL,
                    output_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (promotion_id) REFERENCES promotions(id)
                );

                CREATE TABLE IF NOT EXISTS outbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    promotion_id INTEGER NOT NULL,
                    channel TEXT NOT NULL CHECK(channel IN ('employees', 'telegram', 'vk')),
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    external_id TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE (promotion_id, channel),
                    FOREIGN KEY (promotion_id) REFERENCES promotions(id)
                );

                CREATE TABLE IF NOT EXISTS game_rotation (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cycle_number INTEGER NOT NULL,
                    app_id INTEGER NOT NULL,
                    promotion_id INTEGER NOT NULL UNIQUE,
                    status TEXT NOT NULL CHECK(
                        status IN ('reserved', 'used', 'released')
                    ),
                    selected_at TEXT NOT NULL,
                    used_at TEXT,
                    FOREIGN KEY (app_id) REFERENCES games(app_id),
                    FOREIGN KEY (promotion_id) REFERENCES promotions(id)
                );

                CREATE TABLE IF NOT EXISTS tracker_settings (
                    setting_key TEXT PRIMARY KEY,
                    setting_value TEXT NOT NULL,
                    comment TEXT,
                    updated_at TEXT NOT NULL,
                    updated_by TEXT
                );

                CREATE TABLE IF NOT EXISTS tracker_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    actor_id TEXT,
                    actor_name TEXT,
                    action TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT,
                    before_json TEXT,
                    after_json TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_account_games_owned_app
                    ON account_games(owned, app_id);
                CREATE INDEX IF NOT EXISTS idx_license_events_recorded_at
                    ON license_events(recorded_at);
                CREATE INDEX IF NOT EXISTS idx_playtime_daily_date
                    ON playtime_daily(snapshot_date);
                CREATE INDEX IF NOT EXISTS idx_outbox_status
                    ON outbox(status);
                CREATE INDEX IF NOT EXISTS idx_content_generations_promotion
                    ON content_generations(promotion_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_game_rotation_cycle_status
                    ON game_rotation(cycle_number, status, app_id);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_game_rotation_active_game
                    ON game_rotation(cycle_number, app_id)
                    WHERE status IN ('reserved', 'used');
                CREATE INDEX IF NOT EXISTS idx_tracker_audit_created_at
                    ON tracker_audit(created_at, id);

                PRAGMA user_version = 6;
                """
            )
            game_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(games)")
            }
            if "manager_description" not in game_columns:
                conn.execute(
                    "ALTER TABLE games ADD COLUMN manager_description TEXT"
                )
            if "managed" not in game_columns:
                conn.execute(
                    "ALTER TABLE games ADD COLUMN managed INTEGER NOT NULL DEFAULT 0"
                )
            if "catalog_status" not in game_columns:
                conn.execute(
                    "ALTER TABLE games ADD COLUMN catalog_status TEXT NOT NULL DEFAULT 'draft'"
                )
            if "manager_comment" not in game_columns:
                conn.execute(
                    "ALTER TABLE games ADD COLUMN manager_comment TEXT"
                )
            conn.execute(
                """
                UPDATE games
                SET managed = 1,
                    catalog_status = 'active'
                WHERE is_approved = 1
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_games_managed_status
                ON games(managed, catalog_status, steam_name)
                """
            )
            promotion_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(promotions)")
            }
            if "is_test" not in promotion_columns:
                conn.execute(
                    """
                    ALTER TABLE promotions
                    ADD COLUMN is_test INTEGER NOT NULL DEFAULT 0
                    CHECK(is_test IN (0, 1))
                    """
                )
            for column_name in ("claimed_by", "claimed_name", "claimed_at"):
                if column_name not in promotion_columns:
                    conn.execute(
                        f"ALTER TABLE promotions ADD COLUMN {column_name} TEXT"
                    )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_promotions_period_status
                ON promotions(is_test, status, valid_from, valid_to)
                """
            )
            timestamp = utc_now()
            for key, (value, comment) in TRACKER_SETTING_DEFAULTS.items():
                conn.execute(
                    """
                    INSERT INTO tracker_settings (
                        setting_key, setting_value, comment, updated_at
                    ) VALUES (?, ?, ?, ?)
                    ON CONFLICT(setting_key) DO NOTHING
                    """,
                    (key, value, comment, timestamp),
                )
            conn.execute("PRAGMA user_version = 6")

    def import_legacy_accounts(self, legacy_db_path: str | Path) -> int:
        legacy_path = Path(legacy_db_path)
        if not legacy_path.exists():
            raise FileNotFoundError(f"Legacy DB не найдена: {legacy_path}")

        with closing(
            sqlite3.connect(
                f"file:{legacy_path}?mode=ro",
                uri=True,
            )
        ) as legacy:
            rows = legacy.execute(
                """
                SELECT steam_id, vanity_url, club_name, status
                FROM accounts
                WHERE steam_id IS NOT NULL
                """
            ).fetchall()

        timestamp = utc_now()
        with self.connect() as conn:
            for steam_id, vanity_url, club_name, status in rows:
                conn.execute(
                    """
                    INSERT INTO accounts (
                        steam_id, vanity_url, club_name, active, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(steam_id) DO UPDATE SET
                        vanity_url = excluded.vanity_url,
                        club_name = excluded.club_name,
                        active = excluded.active,
                        updated_at = excluded.updated_at
                    """,
                    (
                        steam_id,
                        vanity_url,
                        club_name,
                        int(status == "ACTIVE"),
                        timestamp,
                    ),
                )
        return len(rows)

    def upsert_catalog_games(self, games: Iterable[dict]) -> int:
        timestamp = utc_now()
        count = 0
        with self.connect() as conn:
            for game in games:
                conn.execute(
                    """
                    INSERT INTO games (
                        app_id, steam_name, official_name, is_approved,
                        managed, catalog_status,
                        player_count, base_description, manager_description,
                        description_source, updated_at
                    ) VALUES (?, ?, ?, 1, 1, 'active', ?, ?, ?, ?, ?)
                    ON CONFLICT(app_id) DO UPDATE SET
                        steam_name = excluded.steam_name,
                        official_name = excluded.official_name,
                        is_approved = 1,
                        managed = 1,
                        catalog_status = 'active',
                        player_count = excluded.player_count,
                        base_description = excluded.base_description,
                        manager_description = COALESCE(
                            excluded.manager_description,
                            manager_description
                        ),
                        description_source = excluded.description_source,
                        updated_at = excluded.updated_at
                    """,
                    (
                        game["app_id"],
                        game["steam_name"],
                        game["official_name"],
                        game.get("player_count"),
                        game.get("base_description"),
                        game.get("manager_description"),
                        game.get("description_source", "google_sheet"),
                        timestamp,
                    ),
                )
                count += 1
        return count

    def replace_approved_catalog(self, games: Sequence[dict]) -> int:
        timestamp = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE games
                SET is_approved = 0,
                    catalog_status = 'excluded',
                    updated_at = ?
                WHERE managed = 1
                """,
                (timestamp,),
            )
            for game in games:
                conn.execute(
                    """
                    INSERT INTO games (
                        app_id, steam_name, official_name, is_approved,
                        managed, catalog_status,
                        player_count, base_description, manager_description,
                        description_source, updated_at
                    ) VALUES (?, ?, ?, 1, 1, 'active', ?, ?, ?, ?, ?)
                    ON CONFLICT(app_id) DO UPDATE SET
                        steam_name = excluded.steam_name,
                        official_name = excluded.official_name,
                        is_approved = 1,
                        managed = 1,
                        catalog_status = 'active',
                        player_count = excluded.player_count,
                        base_description = excluded.base_description,
                        manager_description = excluded.manager_description,
                        description_source = excluded.description_source,
                        updated_at = excluded.updated_at
                    """,
                    (
                        game["app_id"],
                        game["steam_name"],
                        game["official_name"],
                        game.get("player_count"),
                        game.get("base_description"),
                        game.get("manager_description"),
                        game.get("description_source", "google_sheet"),
                        timestamp,
                    ),
                )
        return len(games)

    def catalog_name_index(self) -> dict[str, tuple[int, str]]:
        from .catalog import normalize_name

        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT app_id, COALESCE(official_name, steam_name) AS game_name
                FROM games
                """
            ).fetchall()
        return {
            normalize_name(row["game_name"]): (
                row["app_id"],
                row["game_name"],
            )
            for row in rows
        }

    def catalog_app_index(self) -> dict[int, dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    g.app_id,
                    g.steam_name,
                    g.official_name,
                    g.player_count,
                    g.base_description,
                    g.manager_description,
                    gm.store_description,
                    gm.source_language
                FROM games g
                LEFT JOIN game_metadata gm ON gm.app_id = g.app_id
                """
            ).fetchall()
        return {row["app_id"]: dict(row) for row in rows}

    @staticmethod
    def _write_audit(
        conn: sqlite3.Connection,
        *,
        actor_id: str | None,
        actor_name: str | None,
        action: str,
        entity_type: str,
        entity_id: str | int | None,
        before: dict | None,
        after: dict | None,
    ) -> None:
        conn.execute(
            """
            INSERT INTO tracker_audit (
                actor_id, actor_name, action, entity_type, entity_id,
                before_json, after_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                actor_id,
                actor_name,
                action,
                entity_type,
                None if entity_id is None else str(entity_id),
                json.dumps(before, ensure_ascii=False, default=str)
                if before is not None
                else None,
                json.dumps(after, ensure_ascii=False, default=str)
                if after is not None
                else None,
                utc_now(),
            ),
        )

    @classmethod
    def _pause_unlicensed_games(
        cls,
        conn: sqlite3.Connection,
        *,
        actor_id: str | None,
        actor_name: str | None,
    ) -> int:
        rows = conn.execute(
            """
            SELECT *
            FROM games g
            WHERE g.managed = 1
                AND g.catalog_status = 'active'
                AND NOT EXISTS (
                    SELECT 1
                    FROM account_games ag
                    JOIN accounts a ON a.steam_id = ag.steam_id
                    WHERE ag.app_id = g.app_id
                        AND ag.owned = 1
                        AND a.active = 1
                )
            """
        ).fetchall()
        for row in rows:
            before = dict(row)
            conn.execute(
                """
                UPDATE games
                SET catalog_status = 'paused',
                    is_approved = 0,
                    updated_at = ?
                WHERE app_id = ?
                """,
                (utc_now(), row["app_id"]),
            )
            after = dict(
                conn.execute(
                    "SELECT * FROM games WHERE app_id = ?",
                    (row["app_id"],),
                ).fetchone()
            )
            cls._write_audit(
                conn,
                actor_id=actor_id,
                actor_name=actor_name,
                action="game_auto_paused_no_license",
                entity_type="game",
                entity_id=row["app_id"],
                before=before,
                after=after,
            )
        return len(rows)

    def tracker_settings(self) -> dict[str, str]:
        with self.connect() as conn:
            return {
                row["setting_key"]: row["setting_value"]
                for row in conn.execute(
                    """
                    SELECT setting_key, setting_value
                    FROM tracker_settings
                    ORDER BY setting_key
                    """
                )
            }

    def tracker_setting_rows(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(
                conn.execute(
                    """
                    SELECT
                        setting_key,
                        setting_value,
                        comment,
                        updated_at,
                        updated_by
                    FROM tracker_settings
                    ORDER BY setting_key
                    """
                )
            )

    def update_tracker_setting(
        self,
        key: str,
        value: str,
        *,
        actor_id: str | None,
        actor_name: str | None,
    ) -> None:
        if key not in TRACKER_SETTING_DEFAULTS:
            raise ValueError(f"Неизвестная настройка Steam Tracker: {key}")
        value = str(value).strip()
        if not value:
            raise ValueError("Значение настройки не может быть пустым")
        if key == "weekly_discount" and len(value) > 80:
            raise ValueError("Скидка не должна превышать 80 символов")
        if key == "weekly_promo_enabled":
            normalized = value.casefold()
            if normalized not in {
                "1",
                "0",
                "true",
                "false",
                "yes",
                "no",
                "on",
                "off",
                "да",
                "нет",
            }:
                raise ValueError("Включение должно быть true или false")
            value = (
                "true"
                if normalized in {"1", "true", "yes", "on", "да"}
                else "false"
            )
        if key == "generation_day":
            value = value.casefold()
            if value not in {
                "monday",
                "tuesday",
                "wednesday",
                "thursday",
                "friday",
                "saturday",
                "sunday",
            }:
                raise ValueError("Неизвестный день недели")
        if key == "generation_time":
            datetime.strptime(value, "%H:%M")
        if key == "timezone":
            pytz.timezone(value)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT setting_key, setting_value, comment, updated_at,
                       updated_by
                FROM tracker_settings
                WHERE setting_key = ?
                """,
                (key,),
            ).fetchone()
            before = dict(row) if row else None
            comment = TRACKER_SETTING_DEFAULTS[key][1]
            conn.execute(
                """
                INSERT INTO tracker_settings (
                    setting_key, setting_value, comment, updated_at,
                    updated_by
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(setting_key) DO UPDATE SET
                    setting_value = excluded.setting_value,
                    comment = excluded.comment,
                    updated_at = excluded.updated_at,
                    updated_by = excluded.updated_by
                """,
                (key, value, comment, utc_now(), actor_name or actor_id),
            )
            after = dict(
                conn.execute(
                    "SELECT * FROM tracker_settings WHERE setting_key = ?",
                    (key,),
                ).fetchone()
            )
            self._write_audit(
                conn,
                actor_id=actor_id,
                actor_name=actor_name,
                action="setting_updated",
                entity_type="setting",
                entity_id=key,
                before=before,
                after=after,
            )
    def recent_audit(self, *, limit: int = 30) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(
                conn.execute(
                    """
                    SELECT *
                    FROM tracker_audit
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (max(1, min(int(limit), 100)),),
                )
            )

    def add_managed_game(
        self,
        *,
        app_id: int,
        steam_name: str,
        actor_id: str | None,
        actor_name: str | None,
    ) -> None:
        app_id = int(app_id)
        steam_name = str(steam_name).strip()
        if app_id <= 0:
            raise ValueError("AppID должен быть положительным числом")
        if not steam_name:
            raise ValueError("Название игры обязательно")
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM games WHERE app_id = ?",
                (app_id,),
            ).fetchone()
            before = dict(row) if row else None
            if row is not None and row["managed"]:
                raise ValueError(f"Игра AppID {app_id} уже есть в каталоге")
            conn.execute(
                """
                INSERT INTO games (
                    app_id, steam_name, official_name, is_approved,
                    managed, catalog_status, description_source, updated_at
                ) VALUES (?, ?, ?, 0, 1, 'draft', 'steam_store', ?)
                ON CONFLICT(app_id) DO UPDATE SET
                    steam_name = excluded.steam_name,
                    official_name = COALESCE(
                        games.official_name,
                        excluded.official_name
                    ),
                    is_approved = 0,
                    managed = 1,
                    catalog_status = 'draft',
                    updated_at = excluded.updated_at
                """,
                (app_id, steam_name, steam_name, utc_now()),
            )
            after = dict(
                conn.execute(
                    "SELECT * FROM games WHERE app_id = ?",
                    (app_id,),
                ).fetchone()
            )
            self._write_audit(
                conn,
                actor_id=actor_id,
                actor_name=actor_name,
                action="game_added",
                entity_type="game",
                entity_id=app_id,
                before=before,
                after=after,
            )

    def update_managed_game(
        self,
        app_id: int,
        *,
        player_count: int | None = None,
        manager_description: str | None = None,
        manager_comment: str | None = None,
        actor_id: str | None,
        actor_name: str | None,
    ) -> None:
        if player_count is not None and player_count < 1:
            raise ValueError("Количество игроков должно быть не меньше 1")
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM games WHERE app_id = ? AND managed = 1",
                (app_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"Игра AppID {app_id} не найдена в каталоге")
            before = dict(row)
            conn.execute(
                """
                UPDATE games
                SET player_count = ?,
                    manager_description = ?,
                    manager_comment = ?,
                    updated_at = ?
                WHERE app_id = ?
                """,
                (
                    player_count,
                    manager_description,
                    manager_comment,
                    utc_now(),
                    app_id,
                ),
            )
            after = dict(
                conn.execute(
                    "SELECT * FROM games WHERE app_id = ?",
                    (app_id,),
                ).fetchone()
            )
            self._write_audit(
                conn,
                actor_id=actor_id,
                actor_name=actor_name,
                action="game_updated",
                entity_type="game",
                entity_id=app_id,
                before=before,
                after=after,
            )

    def set_game_catalog_status(
        self,
        app_id: int,
        status: str,
        *,
        actor_id: str | None,
        actor_name: str | None,
    ) -> None:
        if status not in GAME_CATALOG_STATUSES:
            raise ValueError(f"Неизвестный статус игры: {status}")
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM games WHERE app_id = ? AND managed = 1",
                (app_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"Игра AppID {app_id} не найдена в каталоге")
            if status == "active":
                license_row = conn.execute(
                    """
                    SELECT 1
                    FROM account_games ag
                    JOIN accounts a ON a.steam_id = ag.steam_id
                    WHERE ag.app_id = ?
                        AND ag.owned = 1
                        AND a.active = 1
                    LIMIT 1
                    """,
                    (app_id,),
                ).fetchone()
                if license_row is None:
                    raise ValueError(
                        "Игру нельзя активировать: лицензия не найдена "
                        "ни на одном активном аккаунте"
                    )
            before = dict(row)
            conn.execute(
                """
                UPDATE games
                SET catalog_status = ?,
                    is_approved = ?,
                    updated_at = ?
                WHERE app_id = ?
                """,
                (status, int(status == "active"), utc_now(), app_id),
            )
            after = dict(
                conn.execute(
                    "SELECT * FROM games WHERE app_id = ?",
                    (app_id,),
                ).fetchone()
            )
            self._write_audit(
                conn,
                actor_id=actor_id,
                actor_name=actor_name,
                action="game_status_changed",
                entity_type="game",
                entity_id=app_id,
                before=before,
                after=after,
            )

    def managed_games(
        self,
        *,
        status: str | None = None,
        search: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[sqlite3.Row]:
        conditions = ["g.managed = 1"]
        params: list[object] = []
        if status:
            if status == "no_license":
                conditions.append(
                    """
                    NOT EXISTS (
                        SELECT 1
                        FROM account_games ag
                        JOIN accounts a ON a.steam_id = ag.steam_id
                        WHERE ag.app_id = g.app_id
                            AND ag.owned = 1
                            AND a.active = 1
                    )
                    """
                )
            elif status in GAME_CATALOG_STATUSES:
                conditions.append("g.catalog_status = ?")
                params.append(status)
            else:
                raise ValueError(f"Неизвестный фильтр каталога: {status}")
        if search:
            conditions.append(
                """
                (
                    CAST(g.app_id AS TEXT) LIKE ?
                    OR g.steam_name LIKE ?
                    OR COALESCE(g.official_name, '') LIKE ?
                )
                """
            )
            pattern = f"%{search.strip()}%"
            params.extend((pattern, pattern, pattern))
        params.extend((max(1, min(int(limit), 500)), max(0, int(offset))))
        with self.connect() as conn:
            return list(
                conn.execute(
                    f"""
                    SELECT
                        g.app_id,
                        g.steam_name,
                        g.official_name,
                        g.catalog_status,
                        g.player_count,
                        g.manager_description,
                        g.manager_comment,
                        (
                            SELECT COUNT(*)
                            FROM account_games ag
                            JOIN accounts a ON a.steam_id = ag.steam_id
                            WHERE ag.app_id = g.app_id
                                AND ag.owned = 1
                                AND a.active = 1
                        ) AS owned_count
                    FROM games g
                    WHERE {' AND '.join(conditions)}
                    ORDER BY g.steam_name, g.app_id
                    LIMIT ? OFFSET ?
                    """,
                    params,
                )
            )

    def managed_game(self, app_id: int) -> sqlite3.Row:
        with self.connect() as conn:
            row = conn.execute(
                """
                WITH active_game_playtime AS (
                    SELECT
                        catalog_game.app_id,
                        COALESCE(
                            SUM(
                                CASE
                                    WHEN a.active = 1
                                    THEN ag.last_playtime_minutes
                                    ELSE 0
                                END
                            ),
                            0
                        ) AS total_playtime_minutes
                    FROM games catalog_game
                    LEFT JOIN account_games ag
                        ON ag.app_id = catalog_game.app_id
                    LEFT JOIN accounts a
                        ON a.steam_id = ag.steam_id
                    WHERE catalog_game.managed = 1
                        AND catalog_game.catalog_status = 'active'
                    GROUP BY catalog_game.app_id
                ),
                ranked_game_playtime AS (
                    SELECT
                        app_id,
                        total_playtime_minutes,
                        RANK() OVER (
                            ORDER BY total_playtime_minutes DESC
                        ) AS popularity_rank
                    FROM active_game_playtime
                )
                SELECT
                    g.*,
                    gm.store_description,
                    gm.source_language,
                    gm.genres_json,
                    gm.categories_json,
                    gm.header_image,
                    gm.updated_at AS metadata_updated_at,
                    gm.last_error,
                    (
                        SELECT COUNT(*)
                        FROM account_games ag
                        JOIN accounts a ON a.steam_id = ag.steam_id
                        WHERE ag.app_id = g.app_id
                            AND ag.owned = 1
                            AND a.active = 1
                    ) AS owned_count,
                    (
                        SELECT COUNT(*)
                        FROM accounts
                        WHERE active = 1
                    ) AS account_count,
                    COALESCE(
                        ranked_game_playtime.total_playtime_minutes,
                        0
                    ) AS total_playtime_minutes,
                    ranked_game_playtime.popularity_rank,
                    (
                        SELECT MAX(valid_to)
                        FROM promotions p
                        WHERE p.app_id = g.app_id
                            AND p.is_test = 0
                            AND p.status = 'approved'
                    ) AS last_promotion
                FROM games g
                LEFT JOIN game_metadata gm ON gm.app_id = g.app_id
                LEFT JOIN ranked_game_playtime
                    ON ranked_game_playtime.app_id = g.app_id
                WHERE g.app_id = ? AND g.managed = 1
                """,
                (app_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"Игра AppID {app_id} не найдена в каталоге")
            return row

    def game_license_rows(self, app_id: int) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(
                conn.execute(
                    """
                    SELECT
                        a.steam_id,
                        a.vanity_url,
                        a.club_name,
                        a.active,
                        COALESCE(ag.owned, 0) AS owned,
                        COALESCE(ag.last_playtime_minutes, 0)
                            AS playtime_minutes,
                        ag.last_seen_at
                    FROM accounts a
                    LEFT JOIN account_games ag
                        ON ag.steam_id = a.steam_id
                        AND ag.app_id = ?
                    ORDER BY a.active DESC, a.club_name, a.vanity_url
                    """,
                    (app_id,),
                )
            )

    def missing_game_license_rows(
        self,
        app_id: int,
    ) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(
                conn.execute(
                    """
                    SELECT
                        a.steam_id,
                        a.vanity_url,
                        a.club_name
                    FROM accounts a
                    LEFT JOIN account_games ag
                        ON ag.steam_id = a.steam_id
                        AND ag.app_id = ?
                    WHERE a.active = 1
                        AND COALESCE(ag.owned, 0) = 0
                    ORDER BY a.club_name, a.vanity_url, a.steam_id
                    """,
                    (app_id,),
                )
            )

    def missing_license_club_summary(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(
                conn.execute(
                    """
                    SELECT
                        COALESCE(NULLIF(a.club_name, ''), 'Без клуба')
                            AS club_name,
                        COUNT(DISTINCT a.steam_id) AS account_count,
                        COUNT(DISTINCT g.app_id) AS active_game_count,
                        SUM(
                            CASE
                                WHEN COALESCE(ag.owned, 0) = 0 THEN 1
                                ELSE 0
                            END
                        ) AS missing_license_count,
                        COUNT(
                            DISTINCT CASE
                                WHEN COALESCE(ag.owned, 0) = 0
                                THEN g.app_id
                            END
                        ) AS games_with_gaps
                    FROM accounts a
                    CROSS JOIN games g
                    LEFT JOIN account_games ag
                        ON ag.steam_id = a.steam_id
                        AND ag.app_id = g.app_id
                    WHERE a.active = 1
                        AND g.managed = 1
                        AND g.catalog_status = 'active'
                    GROUP BY
                        COALESCE(NULLIF(a.club_name, ''), 'Без клуба')
                    ORDER BY club_name
                    """
                )
            )

    def missing_license_rows_for_club(
        self,
        club_name: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(
                conn.execute(
                    """
                    SELECT
                        g.app_id,
                        g.steam_name,
                        COUNT(*) AS missing_count,
                        GROUP_CONCAT(
                            COALESCE(
                                NULLIF(a.vanity_url, ''),
                                a.steam_id
                            ),
                            ', '
                        ) AS missing_zones
                    FROM games g
                    CROSS JOIN accounts a
                    LEFT JOIN account_games ag
                        ON ag.steam_id = a.steam_id
                        AND ag.app_id = g.app_id
                    WHERE g.managed = 1
                        AND g.catalog_status = 'active'
                        AND a.active = 1
                        AND COALESCE(
                            NULLIF(a.club_name, ''),
                            'Без клуба'
                        ) = ?
                        AND COALESCE(ag.owned, 0) = 0
                    GROUP BY g.app_id, g.steam_name
                    ORDER BY g.steam_name, g.app_id
                    LIMIT ? OFFSET ?
                    """,
                    (
                        club_name,
                        max(1, min(int(limit), 500)),
                        max(0, int(offset)),
                    ),
                )
            )

    def managed_accounts(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(
                conn.execute(
                    """
                    SELECT
                        a.*,
                        SUM(CASE WHEN ag.owned = 1 THEN 1 ELSE 0 END)
                            AS owned_games
                    FROM accounts a
                    LEFT JOIN account_games ag ON ag.steam_id = a.steam_id
                    GROUP BY a.steam_id
                    ORDER BY a.active DESC, a.club_name, a.vanity_url,
                             a.steam_id
                    """
                )
            )

    def managed_account(self, steam_id: str) -> sqlite3.Row:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT
                    a.*,
                    SUM(CASE WHEN ag.owned = 1 THEN 1 ELSE 0 END)
                        AS owned_games
                FROM accounts a
                LEFT JOIN account_games ag ON ag.steam_id = a.steam_id
                WHERE a.steam_id = ?
                GROUP BY a.steam_id
                """,
                (steam_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"Steam-аккаунт {steam_id} не найден")
            return row

    def upsert_managed_account(
        self,
        *,
        steam_id: str,
        vanity_url: str | None,
        club_name: str | None,
        actor_id: str | None,
        actor_name: str | None,
    ) -> None:
        steam_id = str(steam_id).strip()
        if not steam_id.isdigit():
            raise ValueError("SteamID должен содержать только цифры")
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM accounts WHERE steam_id = ?",
                (steam_id,),
            ).fetchone()
            before = dict(row) if row else None
            conn.execute(
                """
                INSERT INTO accounts (
                    steam_id, vanity_url, club_name, active, updated_at
                ) VALUES (?, ?, ?, 1, ?)
                ON CONFLICT(steam_id) DO UPDATE SET
                    vanity_url = excluded.vanity_url,
                    club_name = excluded.club_name,
                    updated_at = excluded.updated_at
                """,
                (
                    steam_id,
                    str(vanity_url or "").strip() or None,
                    str(club_name or "").strip() or None,
                    utc_now(),
                ),
            )
            after = dict(
                conn.execute(
                    "SELECT * FROM accounts WHERE steam_id = ?",
                    (steam_id,),
                ).fetchone()
            )
            self._write_audit(
                conn,
                actor_id=actor_id,
                actor_name=actor_name,
                action="account_added" if before is None else "account_updated",
                entity_type="account",
                entity_id=steam_id,
                before=before,
                after=after,
            )

    def set_account_active(
        self,
        steam_id: str,
        active: bool,
        *,
        actor_id: str | None,
        actor_name: str | None,
    ) -> None:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM accounts WHERE steam_id = ?",
                (steam_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"Steam-аккаунт {steam_id} не найден")
            before = dict(row)
            conn.execute(
                """
                UPDATE accounts
                SET active = ?, updated_at = ?
                WHERE steam_id = ?
                """,
                (int(bool(active)), utc_now(), steam_id),
            )
            after = dict(
                conn.execute(
                    "SELECT * FROM accounts WHERE steam_id = ?",
                    (steam_id,),
                ).fetchone()
            )
            self._write_audit(
                conn,
                actor_id=actor_id,
                actor_name=actor_name,
                action="account_activated" if active else "account_deactivated",
                entity_type="account",
                entity_id=steam_id,
                before=before,
                after=after,
            )
            if not active:
                self._pause_unlicensed_games(
                    conn,
                    actor_id=actor_id,
                    actor_name=actor_name,
                )

    def active_steam_ids(self) -> list[str]:
        with self.connect() as conn:
            return [
                row["steam_id"]
                for row in conn.execute(
                    "SELECT steam_id FROM accounts WHERE active = 1 ORDER BY steam_id"
                )
            ]

    def record_account_scan(
        self,
        steam_id: str,
        games: Sequence[OwnedGame],
        *,
        scanned_at: str | None = None,
        snapshot_date: str | None = None,
        removal_threshold: int = 3,
    ) -> ScanResult:
        if removal_threshold < 1:
            raise ValueError("removal_threshold должен быть не меньше 1")

        scanned_at = scanned_at or utc_now()
        snapshot_date = snapshot_date or scanned_at[:10]
        seen_ids = {game.app_id for game in games}
        added = 0
        removed = 0

        with self.connect() as conn:
            account_exists = conn.execute(
                "SELECT 1 FROM accounts WHERE steam_id = ?",
                (steam_id,),
            ).fetchone()
            if account_exists is None:
                raise ValueError(f"Неизвестный SteamID: {steam_id}")

            owned_rows = conn.execute(
                """
                SELECT app_id, owned, missing_checks
                FROM account_games
                WHERE steam_id = ?
                """,
                (steam_id,),
            ).fetchall()
            existing = {row["app_id"]: row for row in owned_rows}

            for game in games:
                conn.execute(
                    """
                    INSERT INTO games (app_id, steam_name, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(app_id) DO UPDATE SET
                        steam_name = excluded.steam_name,
                        updated_at = excluded.updated_at
                    """,
                    (game.app_id, game.name, scanned_at),
                )

                previous = existing.get(game.app_id)
                was_owned = previous is not None and bool(previous["owned"])
                if not was_owned:
                    conn.execute(
                        """
                        INSERT INTO license_events (
                            steam_id, app_id, event_type, recorded_at
                        ) VALUES (?, ?, 'added', ?)
                        """,
                        (steam_id, game.app_id, scanned_at),
                    )
                    added += 1

                conn.execute(
                    """
                    INSERT INTO account_games (
                        steam_id, app_id, owned, first_seen_at, last_seen_at,
                        missing_checks, last_playtime_minutes
                    ) VALUES (?, ?, 1, ?, ?, 0, ?)
                    ON CONFLICT(steam_id, app_id) DO UPDATE SET
                        owned = 1,
                        last_seen_at = excluded.last_seen_at,
                        missing_checks = 0,
                        last_playtime_minutes = excluded.last_playtime_minutes
                    """,
                    (
                        steam_id,
                        game.app_id,
                        scanned_at,
                        scanned_at,
                        game.playtime_minutes,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO playtime_daily (
                        steam_id, app_id, snapshot_date, playtime_minutes,
                        recorded_at
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(steam_id, app_id, snapshot_date) DO UPDATE SET
                        playtime_minutes = excluded.playtime_minutes,
                        recorded_at = excluded.recorded_at
                    """,
                    (
                        steam_id,
                        game.app_id,
                        snapshot_date,
                        game.playtime_minutes,
                        scanned_at,
                    ),
                )

            for app_id, previous in existing.items():
                if app_id in seen_ids or not previous["owned"]:
                    continue
                missing_checks = previous["missing_checks"] + 1
                if missing_checks >= removal_threshold:
                    conn.execute(
                        """
                        UPDATE account_games
                        SET owned = 0, missing_checks = ?
                        WHERE steam_id = ? AND app_id = ?
                        """,
                        (missing_checks, steam_id, app_id),
                    )
                    conn.execute(
                        """
                        INSERT INTO license_events (
                            steam_id, app_id, event_type, recorded_at
                        ) VALUES (?, ?, 'removed', ?)
                        """,
                        (steam_id, app_id, scanned_at),
                    )
                    removed += 1
                else:
                    conn.execute(
                        """
                        UPDATE account_games
                        SET missing_checks = ?
                        WHERE steam_id = ? AND app_id = ?
                        """,
                        (missing_checks, steam_id, app_id),
                    )

            conn.execute(
                """
                UPDATE accounts
                SET updated_at = ?
                WHERE steam_id = ?
                """,
                (scanned_at, steam_id),
            )
            self._pause_unlicensed_games(
                conn,
                actor_id="system",
                actor_name="Steam license sync",
            )

        return ScanResult(
            steam_id=steam_id,
            seen_games=len(games),
            added=added,
            removed=removed,
        )

    def create_promotion(
        self,
        *,
        app_id: int,
        discount_text: str,
        valid_from: str | None,
        valid_to: str | None,
        manager_comment: str | None,
        image_url: str | None,
        is_test: bool = False,
    ) -> int:
        discount_text = discount_text.strip()
        if not discount_text:
            raise ValueError("Скидка обязательна")
        parsed_from = date.fromisoformat(valid_from) if valid_from else None
        parsed_to = date.fromisoformat(valid_to) if valid_to else None
        if parsed_from and parsed_to and parsed_from > parsed_to:
            raise ValueError("Дата начала акции позже даты окончания")

        timestamp = utc_now()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            game = conn.execute(
                """
                SELECT
                    g.is_approved,
                    EXISTS (
                        SELECT 1
                        FROM account_games ag
                        JOIN accounts a ON a.steam_id = ag.steam_id
                        WHERE ag.app_id = g.app_id
                            AND ag.owned = 1
                            AND a.active = 1
                    ) AS has_license
                FROM games g
                WHERE g.app_id = ?
                """,
                (app_id,),
            ).fetchone()
            if game is None or not game["is_approved"]:
                raise ValueError("Промо можно создать только для согласованной игры")
            if not game["has_license"]:
                raise ValueError(
                    "Промо нельзя создать: лицензия игры не найдена "
                    "ни на одном активном аккаунте"
                )
            if not is_test and parsed_from and parsed_to:
                overlap = conn.execute(
                    """
                    SELECT id
                    FROM promotions
                    WHERE is_test = 0
                        AND status NOT IN ('postponed', 'cancelled')
                        AND valid_from IS NOT NULL
                        AND valid_to IS NOT NULL
                        AND valid_from <= ?
                        AND valid_to >= ?
                    LIMIT 1
                    """,
                    (valid_to, valid_from),
                ).fetchone()
                if overlap is not None:
                    raise ValueError(
                        f"Период пересекается с промо #{overlap['id']}"
                    )
            cursor = conn.execute(
                """
                INSERT INTO promotions (
                    app_id, discount_text, valid_from, valid_to,
                    manager_comment, image_url, is_test,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    app_id,
                    discount_text,
                    valid_from,
                    valid_to,
                    manager_comment,
                    image_url,
                    int(is_test),
                    timestamp,
                    timestamp,
                ),
            )
            return int(cursor.lastrowid)

    def create_random_weekly_promotion(
        self,
        *,
        discount_text: str,
        valid_from: str,
        valid_to: str,
        exclude_app_ids: set[int] | None = None,
        rng=None,
    ) -> WeeklyPromotionSelection:
        discount_text = discount_text.strip()
        if not discount_text:
            raise ValueError("Скидка обязательна")
        parsed_from = date.fromisoformat(valid_from)
        parsed_to = date.fromisoformat(valid_to)
        if parsed_from > parsed_to:
            raise ValueError("Дата начала акции позже даты окончания")

        excluded = set(exclude_app_ids or ())
        chooser = rng or random.SystemRandom()
        timestamp = utc_now()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                """
                SELECT p.id, p.app_id, gr.cycle_number
                FROM promotions p
                LEFT JOIN game_rotation gr ON gr.promotion_id = p.id
                WHERE p.is_test = 0
                    AND p.status NOT IN ('postponed', 'cancelled')
                    AND p.valid_from = ?
                    AND p.valid_to = ?
                ORDER BY p.id
                LIMIT 1
                """,
                (valid_from, valid_to),
            ).fetchone()
            if existing is not None:
                return WeeklyPromotionSelection(
                    promotion_id=existing["id"],
                    app_id=existing["app_id"],
                    cycle_number=existing["cycle_number"] or 0,
                    created=False,
                )

            overlap = conn.execute(
                """
                SELECT id
                FROM promotions
                WHERE is_test = 0
                    AND status NOT IN ('postponed', 'cancelled')
                    AND valid_from IS NOT NULL
                    AND valid_to IS NOT NULL
                    AND valid_from <= ?
                    AND valid_to >= ?
                LIMIT 1
                """,
                (valid_to, valid_from),
            ).fetchone()
            if overlap is not None:
                raise ValueError(
                    f"Период пересекается с промо #{overlap['id']}"
                )

            cycle_row = conn.execute(
                "SELECT MAX(cycle_number) AS value FROM game_rotation"
            ).fetchone()
            cycle_number = int(cycle_row["value"] or 1)
            blocked = {
                row["app_id"]
                for row in conn.execute(
                    """
                    SELECT app_id
                    FROM game_rotation
                    WHERE cycle_number = ?
                        AND status IN ('reserved', 'used')
                    """,
                    (cycle_number,),
                )
            }
            approved = [
                row["app_id"]
                for row in conn.execute(
                    """
                    SELECT app_id
                    FROM games g
                    WHERE g.is_approved = 1
                        AND EXISTS (
                            SELECT 1
                            FROM account_games ag
                            JOIN accounts a ON a.steam_id = ag.steam_id
                            WHERE ag.app_id = g.app_id
                                AND ag.owned = 1
                                AND a.active = 1
                        )
                    ORDER BY app_id
                    """
                )
            ]
            if not approved:
                raise ValueError("Нет согласованных игр для ротации")

            eligible = [
                app_id
                for app_id in approved
                if app_id not in blocked and app_id not in excluded
            ]
            if not eligible:
                cycle_number += 1
                previous = conn.execute(
                    """
                    SELECT app_id
                    FROM game_rotation
                    WHERE status = 'used'
                    ORDER BY used_at DESC, id DESC
                    LIMIT 1
                    """
                ).fetchone()
                avoid = set(excluded)
                if previous is not None and len(approved) > 1:
                    avoid.add(previous["app_id"])
                eligible = [
                    app_id for app_id in approved if app_id not in avoid
                ]
            if not eligible:
                raise ValueError("Нет доступных игр для ротации")

            app_id = int(chooser.choice(eligible))
            cursor = conn.execute(
                """
                INSERT INTO promotions (
                    app_id, discount_text, valid_from, valid_to,
                    manager_comment, image_url, created_at, updated_at
                ) VALUES (?, ?, ?, ?, NULL, NULL, ?, ?)
                """,
                (
                    app_id,
                    discount_text,
                    valid_from,
                    valid_to,
                    timestamp,
                    timestamp,
                ),
            )
            promotion_id = int(cursor.lastrowid)
            conn.execute(
                """
                INSERT INTO game_rotation (
                    cycle_number, app_id, promotion_id, status, selected_at
                ) VALUES (?, ?, ?, 'reserved', ?)
                """,
                (cycle_number, app_id, promotion_id, timestamp),
            )
            return WeeklyPromotionSelection(
                promotion_id=promotion_id,
                app_id=app_id,
                cycle_number=cycle_number,
                created=True,
            )

    def promotion_context(self, promotion_id: int) -> sqlite3.Row:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT
                    p.*,
                    g.steam_name AS game_name,
                    g.player_count,
                    COALESCE(
                        NULLIF(g.manager_description, ''),
                        NULLIF(gm.store_description, ''),
                        NULLIF(g.base_description, '')
                    ) AS base_description,
                    gm.genres_json,
                    gm.categories_json,
                    gm.header_image AS store_header_image,
                    gm.source_language
                FROM promotions p
                JOIN games g ON g.app_id = p.app_id
                LEFT JOIN game_metadata gm ON gm.app_id = g.app_id
                WHERE p.id = ?
                """,
                (promotion_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"Промо #{promotion_id} не найдено")
            return row

    def save_generated_texts(
        self,
        promotion_id: int,
        *,
        employee_text: str,
        telegram_text: str,
        vk_text: str,
    ) -> None:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            promotion = conn.execute(
                "SELECT status FROM promotions WHERE id = ?",
                (promotion_id,),
            ).fetchone()
            if promotion is None:
                raise ValueError(f"Промо #{promotion_id} не найдено")
            if promotion["status"] not in {"draft", "review"}:
                raise ValueError(
                    "Тексты можно менять только у черновика "
                    "или промо на согласовании"
                )
            conn.execute(
                """
                UPDATE promotions
                SET status = 'review',
                    employee_text = ?,
                    telegram_text = ?,
                    vk_text = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    employee_text,
                    telegram_text,
                    vk_text,
                    utc_now(),
                    promotion_id,
                ),
            )

    def save_partial_generated_texts(
        self,
        promotion_id: int,
        *,
        employee_text: str | None = None,
        telegram_text: str | None = None,
        vk_text: str | None = None,
    ) -> None:
        if all(value is None for value in (employee_text, telegram_text, vk_text)):
            raise ValueError("Не передан ни один текст для обновления")
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            promotion = conn.execute(
                "SELECT status FROM promotions WHERE id = ?",
                (promotion_id,),
            ).fetchone()
            if promotion is None:
                raise ValueError(f"Промо #{promotion_id} не найдено")
            if promotion["status"] not in {"draft", "review"}:
                raise ValueError(
                    "Тексты можно менять только у черновика "
                    "или промо на согласовании"
                )
            conn.execute(
                """
                UPDATE promotions
                SET status = 'review',
                    employee_text = COALESCE(?, employee_text),
                    telegram_text = COALESCE(?, telegram_text),
                    vk_text = COALESCE(?, vk_text),
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    employee_text,
                    telegram_text,
                    vk_text,
                    utc_now(),
                    promotion_id,
                ),
            )

    def record_generation(
        self,
        promotion_id: int,
        *,
        provider: str,
        model: str | None,
        prompt_version: str,
        input_data: dict,
        output_data: dict,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO content_generations (
                    promotion_id, provider, model, prompt_version,
                    input_json, output_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    promotion_id,
                    provider,
                    model,
                    prompt_version,
                    json.dumps(input_data, ensure_ascii=False),
                    json.dumps(output_data, ensure_ascii=False),
                    utc_now(),
                ),
            )

    def approve_promotion(self, promotion_id: int, approved_by: str) -> None:
        timestamp = utc_now()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            promo = conn.execute(
                """
                SELECT
                    p.*,
                    COALESCE(p.image_url, gm.header_image) AS publish_image_url,
                    g.is_approved,
                    EXISTS (
                        SELECT 1
                        FROM account_games ag
                        JOIN accounts a ON a.steam_id = ag.steam_id
                        WHERE ag.app_id = p.app_id
                            AND ag.owned = 1
                            AND a.active = 1
                    ) AS has_license
                FROM promotions p
                JOIN games g ON g.app_id = p.app_id
                LEFT JOIN game_metadata gm ON gm.app_id = p.app_id
                WHERE p.id = ?
                """,
                (promotion_id,),
            ).fetchone()
            if promo is None:
                raise ValueError(f"Промо #{promotion_id} не найдено")
            if promo["status"] == "approved":
                return
            if promo["is_test"]:
                raise ValueError("Тестовое промо нельзя согласовать")
            if promo["status"] != "review":
                raise ValueError(
                    "Согласовать можно только промо со статусом "
                    "«На согласовании»"
                )
            if (
                promo["claimed_by"]
                and str(promo["claimed_by"]) != str(approved_by)
            ):
                raise ValueError(
                    "Промо уже взято в работу другим сотрудником"
                )
            if not promo["is_approved"]:
                raise ValueError("Нельзя согласовать промо исключённой игры")
            if not promo["has_license"]:
                raise ValueError(
                    "Нельзя согласовать промо: лицензия игры не найдена "
                    "ни на одном активном аккаунте"
                )
            if not all(
                [
                    promo["employee_text"],
                    promo["telegram_text"],
                    promo["vk_text"],
                ]
            ):
                raise ValueError("Сначала нужно сгенерировать все три текста")

            conn.execute(
                """
                UPDATE promotions
                SET status = 'approved',
                    approved_by = ?,
                    approved_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (approved_by, timestamp, timestamp, promotion_id),
            )
            conn.execute(
                """
                UPDATE game_rotation
                SET status = 'used', used_at = ?
                WHERE promotion_id = ?
                    AND status = 'reserved'
                """,
                (timestamp, promotion_id),
            )

            payloads = {
                "employees": promo["employee_text"],
                "telegram": promo["telegram_text"],
                "vk": promo["vk_text"],
            }
            for channel, text in payloads.items():
                payload = json.dumps(
                    {
                        "text": text,
                        "image_url": promo["publish_image_url"],
                        "parse_mode": (
                            "HTML"
                            if channel in {"employees", "telegram"}
                            else None
                        ),
                    },
                    ensure_ascii=False,
                )
                conn.execute(
                    """
                    INSERT INTO outbox (
                        promotion_id, channel, payload_json, status,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, 'pending', ?, ?)
                    ON CONFLICT(promotion_id, channel) DO NOTHING
                    """,
                    (promotion_id, channel, payload, timestamp, timestamp),
                )

    def claim_promotion(
        self,
        promotion_id: int,
        *,
        claimed_by: str,
        claimed_name: str,
        force: bool = False,
    ) -> None:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            promotion = conn.execute(
                """
                SELECT status, claimed_by, claimed_name
                FROM promotions
                WHERE id = ?
                """,
                (promotion_id,),
            ).fetchone()
            if promotion is None:
                raise ValueError(f"Промо #{promotion_id} не найдено")
            if promotion["status"] not in {"draft", "review"}:
                raise ValueError(
                    "Взять в работу можно только черновик "
                    "или промо на согласовании"
                )
            current_claimant = promotion["claimed_by"]
            if current_claimant and str(current_claimant) != str(claimed_by):
                if not force:
                    current_name = (
                        promotion["claimed_name"] or current_claimant
                    )
                    raise ValueError(
                        f"Промо уже в работе у {current_name}"
                    )
            conn.execute(
                """
                UPDATE promotions
                SET claimed_by = ?,
                    claimed_name = ?,
                    claimed_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    str(claimed_by),
                    claimed_name,
                    utc_now(),
                    utc_now(),
                    promotion_id,
                ),
            )

    def release_promotion_claim(
        self,
        promotion_id: int,
        *,
        actor_id: str,
        force: bool = False,
    ) -> None:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            promotion = conn.execute(
                """
                SELECT status, claimed_by, claimed_name
                FROM promotions
                WHERE id = ?
                """,
                (promotion_id,),
            ).fetchone()
            if promotion is None:
                raise ValueError(f"Промо #{promotion_id} не найдено")
            if not promotion["claimed_by"]:
                return
            if str(promotion["claimed_by"]) != str(actor_id) and not force:
                current_name = (
                    promotion["claimed_name"] or promotion["claimed_by"]
                )
                raise ValueError(
                    f"Освободить промо может только {current_name}"
                )
            conn.execute(
                """
                UPDATE promotions
                SET claimed_by = NULL,
                    claimed_name = NULL,
                    claimed_at = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (utc_now(), promotion_id),
            )

    def pending_outbox(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(
                conn.execute(
                    """
                    SELECT * FROM outbox
                    WHERE status = 'pending'
                    ORDER BY id
                    """
                )
            )

    def mark_outbox(
        self,
        outbox_id: int,
        *,
        status: str,
        external_id: str | None = None,
        error: str | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE outbox
                SET status = ?, external_id = ?, error = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, external_id, error, utc_now(), outbox_id),
            )

    def set_promotion_status(self, promotion_id: int, status: str) -> None:
        if status not in PROMOTION_STATUSES:
            raise ValueError(f"Неизвестный статус промо: {status}")
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            promotion = conn.execute(
                "SELECT status FROM promotions WHERE id = ?",
                (promotion_id,),
            ).fetchone()
            if promotion is None:
                raise ValueError(f"Промо #{promotion_id} не найдено")
            current_status = promotion["status"]
            if current_status == status:
                return
            allowed = PROMOTION_STATUS_TRANSITIONS.get(current_status)
            if allowed is None:
                raise ValueError(
                    f"У промо #{promotion_id} неизвестный текущий статус "
                    f"«{current_status}»"
                )
            if status not in allowed:
                raise ValueError(
                    f"Нельзя изменить статус промо с "
                    f"«{current_status}» на «{status}»"
                )
            conn.execute(
                """
                UPDATE promotions
                SET status = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, utc_now(), promotion_id),
            )
            if status in {"postponed", "cancelled"}:
                conn.execute(
                    """
                    UPDATE game_rotation
                    SET status = 'released'
                    WHERE promotion_id = ?
                        AND status = 'reserved'
                    """,
                    (promotion_id,),
                )

    def mark_promotion_as_test(self, promotion_id: int) -> None:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            promotion = conn.execute(
                """
                SELECT p.status, p.is_test,
                    EXISTS(
                        SELECT 1 FROM outbox o
                        WHERE o.promotion_id = p.id
                    ) AS has_outbox,
                    EXISTS(
                        SELECT 1 FROM game_rotation gr
                        WHERE gr.promotion_id = p.id
                            AND gr.status = 'used'
                    ) AS rotation_used
                FROM promotions p
                WHERE p.id = ?
                """,
                (promotion_id,),
            ).fetchone()
            if promotion is None:
                raise ValueError(f"Промо #{promotion_id} не найдено")
            if promotion["is_test"]:
                return
            if promotion["status"] == "approved":
                raise ValueError("Согласованное промо нельзя пометить тестовым")
            if promotion["has_outbox"] or promotion["rotation_used"]:
                raise ValueError(
                    "Промо с историей отправки или использованной ротацией "
                    "нельзя пометить тестовым"
                )
            conn.execute(
                """
                UPDATE promotions
                SET is_test = 1, updated_at = ?
                WHERE id = ?
                """,
                (utc_now(), promotion_id),
            )
            conn.execute(
                """
                UPDATE game_rotation
                SET status = 'released'
                WHERE promotion_id = ?
                    AND status = 'reserved'
                """,
                (promotion_id,),
            )

    def delete_test_promotion(self, promotion_id: int) -> None:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            promotion = conn.execute(
                """
                SELECT p.is_test,
                    EXISTS(
                        SELECT 1 FROM outbox o
                        WHERE o.promotion_id = p.id
                    ) AS has_outbox,
                    EXISTS(
                        SELECT 1 FROM game_rotation gr
                        WHERE gr.promotion_id = p.id
                            AND gr.status = 'used'
                    ) AS rotation_used
                FROM promotions p
                WHERE p.id = ?
                """,
                (promotion_id,),
            ).fetchone()
            if promotion is None:
                raise ValueError(f"Промо #{promotion_id} не найдено")
            if not promotion["is_test"]:
                raise ValueError(
                    "Полностью удалить можно только тестовое промо"
                )
            if promotion["has_outbox"] or promotion["rotation_used"]:
                raise ValueError(
                    "Нельзя удалить промо с историей отправки "
                    "или использованной ротацией"
                )
            conn.execute(
                "DELETE FROM content_generations WHERE promotion_id = ?",
                (promotion_id,),
            )
            conn.execute(
                "DELETE FROM game_rotation WHERE promotion_id = ?",
                (promotion_id,),
            )
            conn.execute(
                "DELETE FROM promotions WHERE id = ?",
                (promotion_id,),
            )

    def random_approved_game_id(self, *, rng=None) -> int:
        with self.connect() as conn:
            app_ids = [
                int(row["app_id"])
                for row in conn.execute(
                    """
                    SELECT app_id
                    FROM games g
                    WHERE g.is_approved = 1
                        AND EXISTS (
                            SELECT 1
                            FROM account_games ag
                            JOIN accounts a ON a.steam_id = ag.steam_id
                            WHERE ag.app_id = g.app_id
                                AND ag.owned = 1
                                AND a.active = 1
                        )
                    ORDER BY app_id
                    """
                )
            ]
        if not app_ids:
            raise ValueError("Нет согласованных игр")
        return int((rng or random.SystemRandom()).choice(app_ids))

    def current_promotion(
        self,
        reference_date: str,
    ) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT
                    p.*,
                    g.steam_name,
                    COALESCE(p.image_url, gm.header_image)
                        AS publish_image_url,
                    gr.cycle_number,
                    gr.status AS rotation_status,
                    (
                        SELECT COUNT(*) FROM outbox o
                        WHERE o.promotion_id = p.id
                    ) AS outbox_total,
                    (
                        SELECT COUNT(*) FROM outbox o
                        WHERE o.promotion_id = p.id
                            AND o.status IN ('sent', 'ready_dry_run')
                    ) AS outbox_ready,
                    (
                        SELECT COUNT(*) FROM outbox o
                        WHERE o.promotion_id = p.id
                            AND o.status = 'error'
                    ) AS outbox_errors
                FROM promotions p
                JOIN games g ON g.app_id = p.app_id
                LEFT JOIN game_metadata gm ON gm.app_id = p.app_id
                LEFT JOIN game_rotation gr ON gr.promotion_id = p.id
                WHERE p.is_test = 0
                    AND p.status NOT IN ('postponed', 'cancelled')
                    AND p.valid_from IS NOT NULL
                    AND p.valid_to IS NOT NULL
                    AND p.valid_from <= ?
                    AND p.valid_to >= ?
                ORDER BY p.id DESC
                LIMIT 1
                """,
                (reference_date, reference_date),
            ).fetchone()

    def has_real_promotion_for_period(
        self,
        valid_from: str,
        valid_to: str,
    ) -> bool:
        with self.connect() as conn:
            return (
                conn.execute(
                    """
                    SELECT 1
                    FROM promotions
                    WHERE is_test = 0
                        AND valid_from = ?
                        AND valid_to = ?
                    LIMIT 1
                    """,
                    (valid_from, valid_to),
                ).fetchone()
                is not None
            )

    def promotion_admin_row(self, promotion_id: int) -> sqlite3.Row:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT
                    p.*,
                    g.steam_name,
                    COALESCE(p.image_url, gm.header_image)
                        AS publish_image_url,
                    CASE
                        WHEN p.image_url IS NOT NULL
                            AND TRIM(p.image_url) <> ''
                        THEN 'manager'
                        WHEN gm.header_image IS NOT NULL
                            AND TRIM(gm.header_image) <> ''
                        THEN 'steam'
                        ELSE 'none'
                    END AS image_source,
                    gr.cycle_number,
                    gr.status AS rotation_status,
                    (
                        SELECT COUNT(*) FROM outbox o
                        WHERE o.promotion_id = p.id
                    ) AS outbox_total,
                    (
                        SELECT COUNT(*) FROM outbox o
                        WHERE o.promotion_id = p.id
                            AND o.status IN ('sent', 'ready_dry_run')
                    ) AS outbox_ready,
                    (
                        SELECT COUNT(*) FROM outbox o
                        WHERE o.promotion_id = p.id
                            AND o.status = 'error'
                    ) AS outbox_errors
                FROM promotions p
                JOIN games g ON g.app_id = p.app_id
                LEFT JOIN game_metadata gm ON gm.app_id = p.app_id
                LEFT JOIN game_rotation gr ON gr.promotion_id = p.id
                WHERE p.id = ?
                """,
                (promotion_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"Промо #{promotion_id} не найдено")
            return row

    def list_promotions(
        self,
        *,
        is_test: bool | None = None,
        statuses: Sequence[str] | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[sqlite3.Row]:
        conditions: list[str] = []
        parameters: list[object] = []
        if is_test is not None:
            conditions.append("p.is_test = ?")
            parameters.append(int(is_test))
        if statuses:
            unknown = set(statuses) - PROMOTION_STATUSES
            if unknown:
                raise ValueError(
                    "Неизвестные статусы: " + ", ".join(sorted(unknown))
                )
            placeholders = ", ".join("?" for _ in statuses)
            conditions.append(f"p.status IN ({placeholders})")
            parameters.extend(statuses)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        parameters.extend((limit, offset))
        with self.connect() as conn:
            return list(
                conn.execute(
                    f"""
                    SELECT
                        p.id,
                        p.app_id,
                        p.status,
                        p.discount_text,
                        p.valid_from,
                        p.valid_to,
                        p.is_test,
                        p.approved_by,
                        p.approved_at,
                        p.claimed_by,
                        p.claimed_name,
                        p.claimed_at,
                        p.created_at,
                        g.steam_name,
                        gr.cycle_number,
                        (
                            SELECT COUNT(*) FROM outbox o
                            WHERE o.promotion_id = p.id
                                AND o.status = 'error'
                        ) AS outbox_errors
                    FROM promotions p
                    JOIN games g ON g.app_id = p.app_id
                    LEFT JOIN game_rotation gr ON gr.promotion_id = p.id
                    {where}
                    ORDER BY
                        COALESCE(p.valid_from, p.created_at) DESC,
                        p.id DESC
                    LIMIT ? OFFSET ?
                    """,
                    parameters,
                )
            )

    def outbox_admin_rows(self, *, limit: int = 30) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(
                conn.execute(
                    """
                    SELECT
                        o.id,
                        o.promotion_id,
                        o.channel,
                        o.status,
                        o.external_id,
                        o.error,
                        o.updated_at,
                        g.steam_name
                    FROM outbox o
                    JOIN promotions p ON p.id = o.promotion_id
                    JOIN games g ON g.app_id = p.app_id
                    ORDER BY o.id DESC
                    LIMIT ?
                    """,
                    (limit,),
                )
            )

    def promotion_reset_summary(self) -> dict[str, int]:
        with self.connect() as conn:
            return {
                "promotions": conn.execute(
                    "SELECT COUNT(*) FROM promotions"
                ).fetchone()[0],
                "content_generations": conn.execute(
                    "SELECT COUNT(*) FROM content_generations"
                ).fetchone()[0],
                "outbox": conn.execute(
                    "SELECT COUNT(*) FROM outbox"
                ).fetchone()[0],
                "game_rotation": conn.execute(
                    "SELECT COUNT(*) FROM game_rotation"
                ).fetchone()[0],
            }

    def reset_all_promotions(self) -> dict[str, int]:
        counts = self.promotion_reset_summary()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM outbox")
            conn.execute("DELETE FROM content_generations")
            conn.execute("DELETE FROM game_rotation")
            conn.execute("DELETE FROM promotions")
            conn.execute(
                """
                DELETE FROM sqlite_sequence
                WHERE name IN (
                    'promotions',
                    'content_generations',
                    'outbox',
                    'game_rotation'
                )
                """
            )
        return counts

    def approved_games_for_enrichment(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(
                conn.execute(
                    """
                    SELECT
                        g.app_id,
                        COALESCE(g.official_name, g.steam_name) AS game_name,
                        gm.updated_at AS metadata_updated_at
                    FROM games g
                    LEFT JOIN game_metadata gm ON gm.app_id = g.app_id
                    WHERE g.managed = 1
                    ORDER BY g.app_id
                    """
                )
            )

    def save_game_metadata(
        self,
        app_id: int,
        *,
        steam_name: str | None = None,
        store_description: str | None,
        genres: list[str],
        categories: list[str],
        header_image: str | None,
        screenshots: list[str],
        is_free: bool | None,
        source_language: str,
        error: str | None = None,
    ) -> None:
        with self.connect() as conn:
            if steam_name:
                conn.execute(
                    """
                    UPDATE games
                    SET steam_name = ?, updated_at = ?
                    WHERE app_id = ?
                    """,
                    (steam_name, utc_now(), app_id),
                )
            conn.execute(
                """
                INSERT INTO game_metadata (
                    app_id, store_description, genres_json, categories_json,
                    header_image, screenshots_json, is_free, source_language,
                    updated_at, last_error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(app_id) DO UPDATE SET
                    store_description = excluded.store_description,
                    genres_json = excluded.genres_json,
                    categories_json = excluded.categories_json,
                    header_image = excluded.header_image,
                    screenshots_json = excluded.screenshots_json,
                    is_free = excluded.is_free,
                    source_language = excluded.source_language,
                    updated_at = excluded.updated_at,
                    last_error = excluded.last_error
                """,
                (
                    app_id,
                    store_description,
                    json.dumps(genres, ensure_ascii=False),
                    json.dumps(categories, ensure_ascii=False),
                    header_image,
                    json.dumps(screenshots, ensure_ascii=False),
                    None if is_free is None else int(is_free),
                    source_language,
                    utc_now(),
                    error,
                ),
            )

    def summary(self) -> dict[str, int]:
        with self.connect() as conn:
            return {
                "accounts": conn.execute(
                    "SELECT COUNT(*) FROM accounts WHERE active = 1"
                ).fetchone()[0],
                "approved_games": conn.execute(
                    "SELECT COUNT(*) FROM games WHERE is_approved = 1"
                ).fetchone()[0],
                "owned_licenses": conn.execute(
                    "SELECT COUNT(*) FROM account_games WHERE owned = 1"
                ).fetchone()[0],
                "daily_playtime_rows": conn.execute(
                    "SELECT COUNT(*) FROM playtime_daily"
                ).fetchone()[0],
                "enriched_games": conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM game_metadata
                    WHERE last_error IS NULL
                    """
                ).fetchone()[0],
                "promotions": conn.execute(
                    "SELECT COUNT(*) FROM promotions"
                ).fetchone()[0],
                "outbox": conn.execute(
                    "SELECT COUNT(*) FROM outbox"
                ).fetchone()[0],
            }

    def current_state_matrix(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(
                conn.execute(
                    """
                    SELECT
                        g.app_id,
                        a.steam_id,
                        a.vanity_url AS nickname,
                        a.club_name,
                        COALESCE(ag.owned, 0) AS owned,
                        COALESCE(ag.last_playtime_minutes, 0)
                            AS playtime_minutes,
                        a.updated_at AS recorded_at
                    FROM games g
                    CROSS JOIN accounts a
                    LEFT JOIN account_games ag
                        ON ag.app_id = g.app_id
                        AND ag.steam_id = a.steam_id
                    WHERE g.is_approved = 1
                        AND a.active = 1
                    ORDER BY g.app_id, a.club_name, a.vanity_url
                    """
                )
            )

    def playtime_dynamics(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(
                conn.execute(
                    """
                    WITH snapshots AS (
                        SELECT
                            pt.snapshot_date,
                            pt.app_id,
                            pt.steam_id,
                            pt.playtime_minutes,
                            LAG(pt.playtime_minutes) OVER (
                                PARTITION BY pt.steam_id, pt.app_id
                                ORDER BY pt.snapshot_date
                            ) AS previous_playtime_minutes
                        FROM playtime_daily pt
                    )
                    SELECT
                        snapshots.snapshot_date,
                        snapshots.app_id,
                        a.club_name,
                        snapshots.steam_id,
                        snapshots.playtime_minutes,
                        (
                            snapshots.playtime_minutes
                            - snapshots.previous_playtime_minutes
                        ) AS playtime_delta
                    FROM snapshots
                    JOIN games g ON g.app_id = snapshots.app_id
                    JOIN accounts a
                        ON a.steam_id = snapshots.steam_id
                    WHERE g.is_approved = 1
                        AND a.active = 1
                        AND snapshots.previous_playtime_minutes IS NOT NULL
                        AND (
                            snapshots.playtime_minutes
                            - snapshots.previous_playtime_minutes
                        ) > 0
                    ORDER BY
                        snapshots.snapshot_date,
                        snapshots.app_id,
                        a.club_name,
                        snapshots.steam_id
                    """
                )
            )

    def approved_game_sheet_rows(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(
                conn.execute(
                    """
                    SELECT
                        g.app_id,
                        g.steam_name,
                        g.official_name,
                        g.catalog_status,
                        g.player_count,
                        g.base_description,
                        g.manager_description,
                        g.manager_comment,
                        gm.store_description,
                        gm.source_language,
                        gm.genres_json,
                        gm.categories_json,
                        gm.header_image,
                        gm.updated_at AS metadata_updated_at,
                        gm.last_error,
                        (
                            SELECT COUNT(*)
                            FROM account_games ag
                            JOIN accounts a ON a.steam_id = ag.steam_id
                            WHERE ag.app_id = g.app_id
                                AND ag.owned = 1
                                AND a.active = 1
                        ) AS owned_count,
                        (
                            SELECT COUNT(*)
                            FROM accounts a
                            WHERE a.active = 1
                        ) AS account_count,
                        (
                            SELECT MAX(p.valid_to)
                            FROM promotions p
                            WHERE p.app_id = g.app_id
                                AND p.is_test = 0
                                AND p.status = 'approved'
                        ) AS last_promotion,
                        (
                            SELECT MAX(gr.cycle_number)
                            FROM game_rotation gr
                            WHERE gr.app_id = g.app_id
                                AND gr.status = 'used'
                        ) AS last_rotation_cycle
                    FROM games g
                    LEFT JOIN game_metadata gm ON gm.app_id = g.app_id
                    WHERE g.managed = 1
                    ORDER BY g.steam_name
                    """
                )
            )

    def promotion_sheet_row(self, promotion_id: int) -> sqlite3.Row:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT
                    p.*,
                    g.steam_name,
                    COALESCE(p.image_url, gm.header_image) AS publish_image_url,
                    gr.cycle_number
                FROM promotions p
                JOIN games g ON g.app_id = p.app_id
                LEFT JOIN game_metadata gm ON gm.app_id = p.app_id
                LEFT JOIN game_rotation gr ON gr.promotion_id = p.id
                WHERE p.id = ?
                """,
                (promotion_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"Промо #{promotion_id} не найдено")
            return row

    def rotation_summary(self) -> dict[str, int]:
        with self.connect() as conn:
            cycle_row = conn.execute(
                "SELECT MAX(cycle_number) AS value FROM game_rotation"
            ).fetchone()
            cycle_number = int(cycle_row["value"] or 1)
            approved = conn.execute(
                """
                SELECT COUNT(*)
                FROM games g
                WHERE g.is_approved = 1
                    AND EXISTS (
                        SELECT 1
                        FROM account_games ag
                        JOIN accounts a ON a.steam_id = ag.steam_id
                        WHERE ag.app_id = g.app_id
                            AND ag.owned = 1
                            AND a.active = 1
                    )
                """
            ).fetchone()[0]
            used = conn.execute(
                """
                SELECT COUNT(DISTINCT app_id)
                FROM game_rotation
                WHERE cycle_number = ? AND status = 'used'
                """,
                (cycle_number,),
            ).fetchone()[0]
            reserved = conn.execute(
                """
                SELECT COUNT(DISTINCT app_id)
                FROM game_rotation
                WHERE cycle_number = ? AND status = 'reserved'
                """,
                (cycle_number,),
            ).fetchone()[0]
            return {
                "cycle_number": cycle_number,
                "approved_games": approved,
                "used_games": used,
                "reserved_games": reserved,
                "available_games": max(approved - used - reserved, 0),
            }

    def approved_license_matrix(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(
                conn.execute(
                    """
                    SELECT
                        g.app_id,
                        COALESCE(g.official_name, g.steam_name) AS game_name,
                        g.player_count,
                        a.club_name,
                        a.vanity_url AS zone,
                        COALESCE(ag.owned, 0) AS owned,
                        ag.last_seen_at,
                        ag.last_playtime_minutes
                    FROM games g
                    CROSS JOIN accounts a
                    LEFT JOIN account_games ag
                        ON ag.app_id = g.app_id
                        AND ag.steam_id = a.steam_id
                    WHERE g.is_approved = 1
                        AND a.active = 1
                    ORDER BY g.official_name, a.club_name, a.vanity_url
                    """
                )
            )
