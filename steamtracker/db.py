"""Хранилище лицензий, дневной динамики и промо-процесса."""

import json
import sqlite3
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Iterable, Iterator, Sequence


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
                    player_count INTEGER,
                    base_description TEXT,
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

                PRAGMA user_version = 2;
                """
            )

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
                        player_count, base_description, description_source,
                        updated_at
                    ) VALUES (?, ?, ?, 1, ?, ?, ?, ?)
                    ON CONFLICT(app_id) DO UPDATE SET
                        official_name = excluded.official_name,
                        is_approved = 1,
                        player_count = excluded.player_count,
                        base_description = excluded.base_description,
                        description_source = excluded.description_source,
                        updated_at = excluded.updated_at
                    """,
                    (
                        game["app_id"],
                        game["steam_name"],
                        game["official_name"],
                        game.get("player_count"),
                        game.get("base_description"),
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
                SET is_approved = 0, updated_at = ?
                WHERE is_approved = 1
                """,
                (timestamp,),
            )
            for game in games:
                conn.execute(
                    """
                    INSERT INTO games (
                        app_id, steam_name, official_name, is_approved,
                        player_count, base_description, description_source,
                        updated_at
                    ) VALUES (?, ?, ?, 1, ?, ?, ?, ?)
                    ON CONFLICT(app_id) DO UPDATE SET
                        steam_name = excluded.steam_name,
                        official_name = excluded.official_name,
                        is_approved = 1,
                        player_count = excluded.player_count,
                        base_description = excluded.base_description,
                        description_source = excluded.description_source,
                        updated_at = excluded.updated_at
                    """,
                    (
                        game["app_id"],
                        game["steam_name"],
                        game["official_name"],
                        game.get("player_count"),
                        game.get("base_description"),
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
                    gm.store_description,
                    gm.source_language
                FROM games g
                LEFT JOIN game_metadata gm ON gm.app_id = g.app_id
                """
            ).fetchall()
        return {row["app_id"]: dict(row) for row in rows}

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
            game = conn.execute(
                "SELECT is_approved FROM games WHERE app_id = ?",
                (app_id,),
            ).fetchone()
            if game is None or not game["is_approved"]:
                raise ValueError("Промо можно создать только для согласованной игры")
            cursor = conn.execute(
                """
                INSERT INTO promotions (
                    app_id, discount_text, valid_from, valid_to,
                    manager_comment, image_url, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    app_id,
                    discount_text,
                    valid_from,
                    valid_to,
                    manager_comment,
                    image_url,
                    timestamp,
                    timestamp,
                ),
            )
            return int(cursor.lastrowid)

    def promotion_context(self, promotion_id: int) -> sqlite3.Row:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT
                    p.*,
                    COALESCE(g.official_name, g.steam_name) AS game_name,
                    g.player_count,
                    COALESCE(
                        NULLIF(g.base_description, ''),
                        gm.store_description
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
            promo = conn.execute(
                """
                SELECT
                    p.*,
                    COALESCE(p.image_url, gm.header_image) AS publish_image_url
                FROM promotions p
                LEFT JOIN game_metadata gm ON gm.app_id = p.app_id
                WHERE p.id = ?
                """,
                (promotion_id,),
            ).fetchone()
            if promo is None:
                raise ValueError(f"Промо #{promotion_id} не найдено")
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
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE promotions
                SET status = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, utc_now(), promotion_id),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"Промо #{promotion_id} не найдено")

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
                    WHERE g.is_approved = 1
                    ORDER BY g.app_id
                    """
                )
            )

    def save_game_metadata(
        self,
        app_id: int,
        *,
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
