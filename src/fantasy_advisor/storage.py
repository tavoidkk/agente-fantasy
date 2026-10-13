"""Local SQLite storage for public price observations and app settings."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Iterable


class _ClosingConnection(sqlite3.Connection):
    """Commit or roll back, then release SQLite handles on Windows."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def data_dir() -> Path:
    override = os.environ.get("FANTASY_ADVISOR_HOME")
    if override:
        path = Path(override).expanduser()
    elif os.name == "nt":
        path = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "LaligaFantasyAdvisor"
    else:
        path = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "laliga-fantasy-advisor"
    path.mkdir(parents=True, exist_ok=True)
    return path


def database_path() -> Path:
    return data_dir() / "advisor.sqlite3"


def connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(database_path(), factory=_ClosingConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS ff_players (
            player_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            normalized_name TEXT NOT NULL,
            position TEXT,
            team_id TEXT,
            current_value INTEGER,
            trend REAL,
            acceleration REAL,
            fetched_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_ff_players_name ON ff_players(normalized_name);

        CREATE TABLE IF NOT EXISTS ff_price_snapshots (
            player_id TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            current_value INTEGER,
            value_1d INTEGER,
            value_2d INTEGER,
            value_3d INTEGER,
            value_7d INTEGER,
            value_14d INTEGER,
            value_30d INTEGER,
            trend REAL,
            acceleration REAL,
            pct_1d REAL,
            pct_2d REAL,
            pct_3d REAL,
            pct_7d REAL,
            pct_14d REAL,
            pct_30d REAL,
            PRIMARY KEY(player_id, observed_at)
        );
        CREATE INDEX IF NOT EXISTS ix_ff_prices_player_time
            ON ff_price_snapshots(player_id, observed_at DESC);

        CREATE TABLE IF NOT EXISTS ff_daily_prices (
            player_id TEXT NOT NULL,
            price_date TEXT NOT NULL,
            value INTEGER NOT NULL,
            daily_change INTEGER,
            source TEXT NOT NULL DEFAULT 'FutbolFantasy',
            fetched_at TEXT NOT NULL,
            PRIMARY KEY(player_id, price_date)
        );
        CREATE INDEX IF NOT EXISTS ix_ff_daily_prices_player_date
            ON ff_daily_prices(player_id, price_date DESC);

        CREATE TABLE IF NOT EXISTS ff_lineups (
            player_slug TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            normalized_name TEXT NOT NULL,
            probability INTEGER,
            injured INTEGER NOT NULL,
            suspended INTEGER NOT NULL,
            available INTEGER NOT NULL,
            team TEXT,
            fetched_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_ff_lineups_name ON ff_lineups(normalized_name);

        CREATE TABLE IF NOT EXISTS ff_fixtures (
            match_id TEXT PRIMARY KEY, kickoff TEXT, home TEXT, away TEXT,
            home_badge TEXT, away_badge TEXT, fetched_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS lineup_drafts (
            draft_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            formation TEXT NOT NULL,
            player_ids TEXT NOT NULL,
            captain_id TEXT,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS league_activity (
            league_id TEXT NOT NULL, event_id TEXT NOT NULL,
            player_id TEXT, event_type INTEGER, amount INTEGER,
            occurred_at TEXT, user1_id TEXT, user2_id TEXT,
            PRIMARY KEY (league_id, event_id)
        );
        CREATE INDEX IF NOT EXISTS ix_league_activity_player
            ON league_activity(league_id, player_id, occurred_at DESC);
        CREATE TABLE IF NOT EXISTS league_player_values (
            league_id TEXT NOT NULL, player_id TEXT NOT NULL,
            price_date TEXT NOT NULL, value INTEGER NOT NULL,
            source TEXT NOT NULL DEFAULT 'unknown',
            PRIMARY KEY (league_id, player_id, price_date)
        );
        CREATE TABLE IF NOT EXISTS league_team_values (
            league_id TEXT NOT NULL, team_id TEXT NOT NULL,
            price_date TEXT NOT NULL, value INTEGER NOT NULL,
            PRIMARY KEY (league_id, team_id, price_date)
        );
        CREATE TABLE IF NOT EXISTS purchase_cost_overrides (
            league_id TEXT NOT NULL, player_id TEXT NOT NULL,
            amount INTEGER NOT NULL,
            PRIMARY KEY (league_id, player_id)
        );
        """
    )
    draft_columns = {row["name"] for row in conn.execute("PRAGMA table_info(lineup_drafts)")}
    if "captain_id" not in draft_columns:
        conn.execute("ALTER TABLE lineup_drafts ADD COLUMN captain_id TEXT")
    value_columns = {row["name"] for row in conn.execute("PRAGMA table_info(league_player_values)")}
    if "source" not in value_columns:
        conn.execute("ALTER TABLE league_player_values ADD COLUMN source TEXT NOT NULL DEFAULT 'unknown'")
    lineup_columns = {row["name"] for row in conn.execute("PRAGMA table_info(ff_lineups)")}
    for column, column_type in (("club_slug", "TEXT"), ("starter", "INTEGER"),
                                ("field_x", "TEXT"), ("field_y", "TEXT"),
                                ("position", "TEXT"), ("match_id", "TEXT"), ("image", "TEXT"),
                                ("lineup_order", "INTEGER"),
                                ("market_value", "INTEGER"), ("daily_change", "INTEGER")):
        if column not in lineup_columns:
            conn.execute(f"ALTER TABLE ff_lineups ADD COLUMN {column} {column_type}")
    return conn


def save_league_observation(league_id: str, teams: list[dict], market: list[dict],
                            activity: list[dict], observed_at: str) -> None:
    day = observed_at[:10]
    with connect_db() as conn:
        for team in teams:
            if team.get("id") is not None and isinstance(team.get("teamValue"), (int, float)):
                conn.execute("INSERT INTO league_team_values VALUES (?, ?, ?, ?) "
                             "ON CONFLICT(league_id, team_id, price_date) DO UPDATE SET value=excluded.value",
                             (league_id, str(team["id"]), day, int(team["teamValue"])))
            for owned in team.get("players") or []:
                master = owned.get("playerMaster") or {}
                if master.get("id") is not None and isinstance(master.get("marketValue"), (int, float)):
                    conn.execute("INSERT INTO league_player_values (league_id, player_id, price_date, value, source) VALUES (?, ?, ?, ?, 'laliga') "
                                 "ON CONFLICT(league_id, player_id, price_date) DO UPDATE SET value=excluded.value, source='laliga'",
                                 (league_id, str(master["id"]), day, int(master["marketValue"])))
        for listing in market:
            master = listing.get("playerMaster") or {}
            if master.get("id") is not None and isinstance(master.get("marketValue"), (int, float)):
                conn.execute("INSERT INTO league_player_values (league_id, player_id, price_date, value, source) VALUES (?, ?, ?, ?, 'laliga') "
                             "ON CONFLICT(league_id, player_id, price_date) DO UPDATE SET value=excluded.value, source='laliga'",
                             (league_id, str(master["id"]), day, int(master["marketValue"])))
        for event in activity:
            if event.get("id") is None:
                continue
            conn.execute("INSERT INTO league_activity VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                         "ON CONFLICT(league_id, event_id) DO UPDATE SET "
                         "player_id=CASE WHEN excluded.player_id<>'' THEN excluded.player_id ELSE league_activity.player_id END, "
                         "event_type=COALESCE(excluded.event_type, league_activity.event_type), "
                         "amount=COALESCE(excluded.amount, league_activity.amount), "
                         "occurred_at=COALESCE(excluded.occurred_at, league_activity.occurred_at), "
                         "user1_id=CASE WHEN excluded.user1_id<>'' THEN excluded.user1_id ELSE league_activity.user1_id END, "
                         "user2_id=CASE WHEN excluded.user2_id<>'' THEN excluded.user2_id ELSE league_activity.user2_id END",
                         (league_id, str(event["id"]), activity_player_id(event),
                          event.get("activityTypeId"), event.get("amount"), event.get("createdAt"),
                          str(event.get("user1Id") or ""), str(event.get("user2Id") or "")))


def activity_player_id(event: dict) -> str:
    """Read player identifiers across observed API activity payload variants."""
    if not isinstance(event, dict):
        return ""
    for key in ("playerMasterId", "masterId", "playerId", "player_id", "footballerId", "fantasyPlayerId"):
        value = event.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    for key in ("playerMaster", "player", "footballer", "playerTeam"):
        nested = event.get(key)
        if isinstance(nested, (str, int)) and str(nested).strip():
            return str(nested).strip()
        if isinstance(nested, dict):
            nested = nested.get("playerMaster") if isinstance(nested.get("playerMaster"), dict) else nested
            for id_key in ("id", "playerMasterId", "masterId", "playerId", "playerTeamId"):
                value = nested.get(id_key)
                if value is not None and str(value).strip():
                    return str(value).strip()
    return ""


def league_activity(league_id: str) -> list[dict]:
    with connect_db() as conn:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM league_activity WHERE league_id=? ORDER BY occurred_at DESC", (league_id,))]


def seed_league_values_from_ff(league_id: str, master_to_ff: dict[str, str]) -> int:
    """Use already cached dated FF prices as historical price proxies."""
    saved = 0
    with connect_db() as conn:
        for master_id, ff_id in master_to_ff.items():
            rows = conn.execute("SELECT price_date, value FROM ff_daily_prices WHERE player_id=?",
                                (ff_id,)).fetchall()
            for row in rows:
                conn.execute("INSERT OR IGNORE INTO league_player_values "
                             "(league_id, player_id, price_date, value, source) VALUES (?, ?, ?, ?, 'futbolfantasy')",
                             (league_id, master_id, row["price_date"], row["value"]))
                saved += 1
    return saved


def league_value_at(league_id: str, player_id: str, date: str) -> int | None:
    with connect_db() as conn:
        row = conn.execute("SELECT value FROM league_player_values WHERE league_id=? AND player_id=? "
                           "AND source='laliga' AND price_date BETWEEN date(?, '-2 days') AND ? "
                           "ORDER BY price_date DESC LIMIT 1",
                           (league_id, player_id, date[:10], date[:10])).fetchone()
    return row[0] if row else None


def previous_team_value(league_id: str, team_id: str, today: str) -> tuple[int, str] | None:
    with connect_db() as conn:
        row = conn.execute("SELECT value, price_date FROM league_team_values WHERE league_id=? "
                           "AND team_id=? AND price_date<? ORDER BY price_date DESC LIMIT 1",
                           (league_id, team_id, today)).fetchone()
    return (row[0], row[1]) if row else None


def purchase_cost_override(league_id: str, player_id: str) -> int | None:
    with connect_db() as conn:
        row = conn.execute("SELECT amount FROM purchase_cost_overrides WHERE league_id=? AND player_id=?",
                           (league_id, player_id)).fetchone()
    return row[0] if row else None


def set_purchase_cost_override(league_id: str, player_id: str, amount: int | None) -> None:
    with connect_db() as conn:
        if amount is None:
            conn.execute("DELETE FROM purchase_cost_overrides WHERE league_id=? AND player_id=?",
                         (league_id, player_id))
        else:
            conn.execute("INSERT INTO purchase_cost_overrides VALUES (?, ?, ?) "
                         "ON CONFLICT(league_id, player_id) DO UPDATE SET amount=excluded.amount",
                         (league_id, player_id, amount))


def save_ff_players(players: Iterable[dict], observed_at: str) -> int:
    rows = list(players)
    with connect_db() as conn:
        for player in rows:
            conn.execute(
                """INSERT INTO ff_players (
                    player_id, name, normalized_name, position, team_id,
                    current_value, trend, acceleration, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(player_id) DO UPDATE SET
                    name=excluded.name,
                    normalized_name=excluded.normalized_name,
                    position=excluded.position,
                    team_id=excluded.team_id,
                    current_value=excluded.current_value,
                    trend=excluded.trend,
                    acceleration=excluded.acceleration,
                    fetched_at=excluded.fetched_at""",
                (
                    player["id"], player["name"], player["normalized_name"],
                    player.get("position"), player.get("team_id"),
                    player.get("current_value"), player.get("trend"),
                    player.get("acceleration"), observed_at,
                ),
            )
            conn.execute(
                """INSERT OR IGNORE INTO ff_price_snapshots (
                    player_id, observed_at, current_value, value_1d, value_2d,
                    value_3d, value_7d, value_14d, value_30d, trend,
                    acceleration, pct_1d, pct_2d, pct_3d, pct_7d, pct_14d, pct_30d
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    player["id"], observed_at, player.get("current_value"),
                    player.get("value_1d"), player.get("value_2d"),
                    player.get("value_3d"), player.get("value_7d"),
                    player.get("value_14d"), player.get("value_30d"),
                    player.get("trend"), player.get("acceleration"),
                    player.get("pct_1d"), player.get("pct_2d"),
                    player.get("pct_3d"), player.get("pct_7d"),
                    player.get("pct_14d"), player.get("pct_30d"),
                ),
            )
    return len(rows)


def latest_ff_players() -> list[dict]:
    with connect_db() as conn:
        return [dict(row) for row in conn.execute(
            """SELECT p.*, s.value_1d, s.value_2d, s.value_3d,
                      s.value_7d, s.value_14d, s.value_30d,
                      s.pct_1d, s.pct_2d, s.pct_3d,
                      s.pct_7d, s.pct_14d, s.pct_30d
               FROM ff_players AS p
               LEFT JOIN ff_price_snapshots AS s
                 ON s.player_id = p.player_id AND s.observed_at = p.fetched_at
               ORDER BY p.name COLLATE NOCASE"""
        )]


def find_ff_player(query: str) -> dict | None:
    from .matching import normalize

    normalized = normalize(query)
    players = latest_ff_players()
    rows = [player for player in players if player["normalized_name"] == normalized]
    if len(rows) == 1:
        return rows[0]
    if not rows:
        rows = [player for player in players if normalized in player["normalized_name"]][:10]
    if len(rows) == 1:
        return rows[0]
    if rows:
        raise ValueError("Nombre ambiguo; prueba con nombre y apellido.")
    return None


def price_history(player_id: str, limit: int = 30) -> list[dict]:
    with connect_db() as conn:
        rows = conn.execute(
            """SELECT * FROM ff_price_snapshots
               WHERE player_id = ? ORDER BY observed_at DESC LIMIT ?""",
            (player_id, max(1, min(limit, 3650))),
        ).fetchall()
    return [dict(row) for row in reversed(rows)]


def save_player_daily_prices(player_id: str, rows: Iterable[dict], fetched_at: str) -> int:
    prices = list(rows)
    with connect_db() as conn:
        conn.executemany(
            """INSERT INTO ff_daily_prices (player_id, price_date, value, daily_change, source, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(player_id, price_date) DO UPDATE SET
                 value=excluded.value, daily_change=excluded.daily_change,
                 source=excluded.source, fetched_at=excluded.fetched_at""",
            [(player_id, row["date"], row["value"], row.get("dailyChange"),
              row.get("source", "FutbolFantasy"), fetched_at) for row in prices],
        )
    return len(prices)


def player_daily_prices(player_id: str, days: int = 31) -> list[dict]:
    from datetime import datetime, timedelta, timezone

    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=max(1, min(days - 1, 3650)))).isoformat()
    with connect_db() as conn:
        rows = conn.execute(
            """SELECT price_date AS date, value, daily_change AS dailyChange, source, fetched_at
               FROM ff_daily_prices WHERE player_id = ? AND price_date >= ?
               ORDER BY price_date DESC LIMIT ?""",
            (player_id, cutoff, max(1, min(days, 3651))),
        ).fetchall()
    return [dict(row) for row in rows]


def player_daily_prices_fetched_at(player_id: str) -> str | None:
    with connect_db() as conn:
        row = conn.execute("SELECT MAX(fetched_at) AS ts FROM ff_daily_prices WHERE player_id = ?",
                           (player_id,)).fetchone()
    return row["ts"] if row else None


def latest_price_sync() -> str | None:
    with connect_db() as conn:
        row = conn.execute("SELECT MAX(fetched_at) AS ts FROM ff_players").fetchone()
    return row["ts"] if row else None


def save_ff_lineups(players: Iterable[dict], fetched_at: str) -> int:
    rows = list(players)
    with connect_db() as conn:
        conn.execute("DELETE FROM ff_lineups")
        conn.executemany(
            """INSERT INTO ff_lineups (
                player_slug, name, normalized_name, probability, injured,
                suspended, available, team, fetched_at, club_slug, starter,
                field_x, field_y, position, match_id, image, lineup_order,
                market_value, daily_change
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [(
                player["slug"], player["name"], player["normalized_name"],
                player.get("probability"), int(player.get("injured", False)),
                int(player.get("suspended", False)), int(player.get("available", True)),
                player.get("team"), fetched_at, player.get("club_slug"),
                int(bool(player.get("starter"))), player.get("field_x"),
                player.get("field_y"), player.get("position"), player.get("match_id"), player.get("image"),
                player.get("lineup_order"), player.get("market_value"), player.get("daily_change"),
            ) for player in rows],
        )
    return len(rows)


def latest_lineup_sync() -> str | None:
    with connect_db() as conn:
        row = conn.execute("SELECT MAX(fetched_at) AS ts FROM ff_lineups").fetchone()
    return row["ts"] if row else None


def latest_ff_lineups() -> list[dict]:
    with connect_db() as conn:
        rows = conn.execute("SELECT * FROM ff_lineups ORDER BY name COLLATE NOCASE").fetchall()
    return [dict(row) for row in rows]


def save_ff_fixtures(fixtures: Iterable[dict], fetched_at: str) -> int:
    rows = list(fixtures)
    with connect_db() as conn:
        conn.execute("DELETE FROM ff_fixtures")
        conn.executemany(
            """INSERT INTO ff_fixtures (match_id, kickoff, home, away,
               home_badge, away_badge, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [(str(row["matchId"]), row.get("kickoff"), row.get("home"),
              row.get("away"), row.get("homeBadge"), row.get("awayBadge"), fetched_at)
             for row in rows],
        )
    return len(rows)


def latest_ff_fixtures() -> list[dict]:
    with connect_db() as conn:
        rows = conn.execute("SELECT * FROM ff_fixtures ORDER BY kickoff").fetchall()
    return [dict(row) for row in rows]


def get_setting(key: str) -> str | None:
    with connect_db() as conn:
        row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_setting(key: str, value: str) -> None:
    with connect_db() as conn:
        conn.execute(
            "INSERT INTO app_settings(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def delete_setting(key: str) -> None:
    with connect_db() as conn:
        conn.execute("DELETE FROM app_settings WHERE key = ?", (key,))


def save_lineup_draft(draft_id: str, name: str, formation: str, player_ids: list[str], updated_at: str,
                      captain_id: str | None = None) -> None:
    import json
    with connect_db() as conn:
        conn.execute(
            """INSERT INTO lineup_drafts(draft_id, name, formation, player_ids, updated_at, captain_id)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(draft_id) DO UPDATE SET name=excluded.name,
                 formation=excluded.formation, player_ids=excluded.player_ids,
                 updated_at=excluded.updated_at, captain_id=excluded.captain_id""",
            (draft_id, name, formation, json.dumps(player_ids), updated_at, captain_id),
        )


def get_lineup_draft(draft_id: str) -> dict | None:
    import json
    with connect_db() as conn:
        row = conn.execute("SELECT * FROM lineup_drafts WHERE draft_id = ?", (draft_id,)).fetchone()
    if not row:
        return None
    result = dict(row)
    result["player_ids"] = json.loads(result["player_ids"])
    return result
