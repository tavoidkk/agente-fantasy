"""Local, read-only dashboard and private lineup-draft server."""

from __future__ import annotations

import json
import secrets
import threading
import time
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import storage
from . import auth
from .advisor import build_report
from .decisions import bid_guidance, league_premiums, manager_purchase_cost, purchase_cost
from .cli import _sync_lineups, _sync_prices
from .matching import canonical_player_name, find_match, normalize
from .sources.laliga import LaligaClient, league_id_and_team_id
from .sources.futbolfantasy import fetch_player_daily_prices

WEB_DIR = Path(__file__).resolve().parent / "web"
POSITION_NAMES = {1: "Portero", 2: "Defensa", 3: "Medio", 4: "Delantero", 5: "Entrenador"}
POSITION_SHORT = {"Portero": "POR", "Defensa": "DEF", "Medio": "MED", "Delantero": "DEL", "Entrenador": "ENT"}
_dashboard_lock = threading.Lock()
_dashboard_state: dict = {"loading": False, "data": None, "error": None}
_dashboard_context: dict = {}
_club_catalog_cache: dict[str, tuple[float, dict[str, str], dict[str, str]]] = {}
_pending_auth: dict[str, tuple[str, str, float]] = {}


def _fixture_club_slug(name: str) -> str:
    aliases = {"rayo": "rayo-vallecano", "rayo vallecano": "rayo-vallecano",
               "atletico de madrid": "atletico", "atletico": "atletico",
               "real betis": "betis", "real club deportivo espanyol": "espanyol",
               "rcd espanyol": "espanyol", "espanyol de barcelona": "espanyol",
               "malaga cf": "malaga", "malaga club de futbol": "malaga",
               "fc barcelona": "barcelona", "getafe cf": "getafe",
               "villarreal cf": "villarreal", "elche cf": "elche",
               "c a osasuna": "osasuna", "ca osasuna": "osasuna",
               "r racing club": "racing", "racing club": "racing",
               "valencia cf": "valencia", "levante ud": "levante",
               "sevilla fc": "sevilla", "rc deportivo": "deportivo",
               "deportivo alaves": "alaves", "athletic club": "athletic",
               "real sociedad": "real-sociedad", "real madrid": "real-madrid"}
    name = normalize(name)
    return aliases.get(name, name.replace(" ", "-"))


def _fixture_data(rows: list[dict], probable: list[dict], catalog: list[dict],
                  ff_players: list[dict] | None = None,
                  club_names: dict[str, str] | None = None) -> list[dict]:
    """Build projected teams without allowing a twelfth row to displace the GK."""
    by_name: dict[str, list[dict]] = {}
    by_team: dict[str, list[dict]] = {}
    for master in catalog:
        by_name.setdefault(canonical_player_name(_name(master)), []).append(master)
        by_team.setdefault(_fixture_club_slug(_club_name(master, club_names)), []).append(master)
    by_club: dict[str, list[dict]] = {}
    for row in probable:
        club_slug = row.get("club_slug") or ""
        names = by_name.get(canonical_player_name(row.get("name")), [])
        team_names = [item for item in names if item in by_team.get(club_slug, [])]
        if len(team_names) == 1:
            master = team_names[0]
        elif len(names) == 1:
            master = names[0]
        else:
            master = find_match(row.get("name") or "", [
                {**item, "name": _name(item), "normalized_name": normalize(_name(item))}
                for item in by_team.get(club_slug, [])]) or {}
        ff = find_match(row.get("name") or "", ff_players or [])
        week_points = [p for p in (master.get("weekPoints") or [])
                       if isinstance(p, dict) and str(p.get("weekNumber", "")).isdigit()]
        latest = max(week_points, key=lambda p: int(p["weekNumber"])) if week_points else None
        position = row.get("position")
        y_text = str(row.get("field_y") or "").rstrip("%")
        try:
            y = float(y_text)
        except ValueError:
            y = None
        is_goalkeeper = str(position or "").casefold() in {"1", "por", "portero", "goalkeeper"} or y is not None and y >= 82
        by_club.setdefault(club_slug, []).append({
            "name": _name(master) if master else str(row["name"]).title(),
            "slug": row.get("player_slug") or row.get("slug"),
            "masterId": str(master.get("id")) if master.get("id") else None,
            "probability": row.get("probability"), "image": row.get("image") or _player_image(master),
            "position": "Portero" if is_goalkeeper else position,
            "goalkeeper": is_goalkeeper, "x": row.get("field_x"), "y": row.get("field_y"),
            "available": bool(row.get("available")), "starter": bool(row.get("starter")),
            "matchId": str(row.get("match_id") or ""), "order": row.get("lineup_order"),
            "value": row.get("market_value") or (ff or {}).get("current_value") or master.get("marketValue"),
            "dailyChange": row.get("daily_change") if row.get("daily_change") is not None else _ff_daily_change(ff),
            "lastPoints": latest.get("points") if latest else None,
            "lastPointsWeek": latest.get("weekNumber") if latest else None,
        })

    def side(club: str, match_id: str) -> tuple[list[dict], list[dict], bool]:
        players = by_club.get(club, [])
        exact = [p for p in players if p["matchId"] == match_id]
        if len(exact) >= 11 and (any(p["goalkeeper"] for p in exact)
                                 or not any(p["goalkeeper"] for p in players)):
            players = exact
        order = lambda p: (p["order"] if p["order"] is not None else 10_000)
        starters = sorted((p for p in players if p["starter"]), key=order)
        keepers = [p for p in starters if p["goalkeeper"]]
        if not keepers:
            keepers = sorted((p for p in players if p["goalkeeper"]), key=order)
        keeper = keepers[0] if keepers else None
        field = [p for p in starters if p is not keeper and not p["goalkeeper"]][:10]
        xi = field + ([keeper] if keeper else [])
        bench = [p for p in sorted(players, key=order) if p not in xi]
        complete = len(xi) == 11 and sum(bool(p["goalkeeper"]) for p in xi) == 1
        return xi, bench, complete

    fixtures = []
    for row in rows:
        match_id = str(row["match_id"])
        home, home_bench, home_complete = side(_fixture_club_slug(row["home"]), match_id)
        away, away_bench, away_complete = side(_fixture_club_slug(row["away"]), match_id)
        fixtures.append({"matchId": match_id, "kickoff": row["kickoff"],
                         "home": row["home"], "away": row["away"],
                         "homeBadge": row["home_badge"], "awayBadge": row["away_badge"],
                         "homeXI": home, "awayXI": away,
                         "homeBench": home_bench, "awayBench": away_bench,
                         "homeComplete": home_complete, "awayComplete": away_complete})
    return fixtures


def _name(master: dict) -> str:
    return master.get("nickname") or master.get("name") or "Jugador"


def _manager_activity_ids(team: dict | None) -> set[str]:
    """Return API actor-ID aliases for a manager/team activity feed."""
    if not isinstance(team, dict):
        return set()
    manager = team.get("manager") or {}
    if not isinstance(manager, dict):
        manager = {}
    return {str(value) for value in (
        team.get("id"), team.get("managerId"), manager.get("id"),
        manager.get("managerId"), manager.get("userId"),
    ) if value is not None and str(value).strip()}


def _manager_key(team: dict) -> str:
    """Choose a stable roster key across API variants with missing manager IDs."""
    manager = team.get("manager") or {}
    if not isinstance(manager, dict):
        manager = {}
    return str(manager.get("id") or manager.get("managerId") or
               team.get("managerId") or team.get("id") or "")


def _manager_name_map(teams) -> dict[str, str]:
    names = {}
    for team in teams if isinstance(teams, list) else []:
        manager = team.get("manager") or {}
        if not isinstance(manager, dict):
            manager = {}
        name = manager.get("managerName") or team.get("name") or "Manager"
        for actor_id in _manager_activity_ids(team):
            names[actor_id] = name
    return names


def _club_name(master: dict, club_names: dict[str, str] | None = None) -> str:
    team = master.get("team")
    if isinstance(team, dict):
        return team.get("name") or (club_names or {}).get(str(team.get("id"))) or "LaLiga"
    return (master.get("teamName") or (team if isinstance(team, str) else None)
            or (club_names or {}).get(str(master.get("teamId"))) or "LaLiga")


def _player_image(master: dict | None) -> str | None:
    """Pick the portrait URL from the player master payload, when available."""
    if not isinstance(master, dict):
        return None
    images = master.get("images")
    candidates = [master.get("image")]
    if isinstance(images, dict):
        candidates.extend(images.get(key) for key in ("shirt", "player", "portrait", "profile"))
    candidates.extend(master.get(key) for key in ("imageUrl", "photo", "portrait"))
    return next((url.strip() for url in candidates
                 if isinstance(url, str) and url.strip().startswith("https://")), None)


def _club_image(master: dict | None, club_images: dict[str, str] | None = None) -> str | None:
    """Return the official club badge URL embedded in a player or roster row."""
    if not isinstance(master, dict):
        return None
    team = master.get("team")
    if isinstance(team, dict):
        badge = team.get("badgeColor")
        if isinstance(badge, str) and badge.strip().startswith("https://"):
            return badge.strip()
        team_id = team.get("id")
    else:
        team_id = master.get("teamId")
    return (club_images or {}).get(str(team_id)) if team_id is not None else None


def _club_catalog(client: LaligaClient, league_id: str, own_team: dict,
                  fantasy_teams: list[dict], market: list[dict],
                  players: list[dict]) -> tuple[dict[str, str], dict[str, str]]:
    """Resolve real clubs from expanded fantasy squads, caching the result for a day."""
    needed = {str(row["teamId"]) for row in players if row.get("teamId") is not None}
    cached = _club_catalog_cache.get(league_id)
    if cached and time.monotonic() - cached[0] < (86400 if needed <= cached[2].keys() else 300):
        return cached[1].copy(), cached[2].copy()
    names: dict[str, str] = {}
    images: dict[str, str] = {}

    def collect(master: dict) -> None:
        club = master.get("team")
        if not isinstance(club, dict) or club.get("id") is None:
            return
        club_id = str(club["id"])
        if club.get("name"):
            names[club_id] = club["name"]
        badge = club.get("badgeColor")
        if isinstance(badge, str) and badge.strip().startswith("https://"):
            images[club_id] = badge.strip()

    for owned in own_team.get("players") or []:
        collect(owned.get("playerMaster") or {})
    for listing in market:
        collect(listing.get("playerMaster") or {})

    remaining = [row for row in fantasy_teams if str(row.get("id")) != str(own_team.get("id"))]
    # The league roster lists club IDs. Query the squads that cover the most
    # missing clubs; expanded squad records carry official club names and badges.
    while needed - images.keys() and remaining:
        best = max(remaining, key=lambda squad: len({
            str((owned.get("playerMaster") or {}).get("teamId"))
            for owned in squad.get("players") or []
        } & (needed - images.keys())))
        if not {str((owned.get("playerMaster") or {}).get("teamId"))
                for owned in best.get("players") or []} & (needed - images.keys()):
            break
        remaining.remove(best)
        try:
            detail = client.team(league_id, str(best["id"]))
        except Exception:
            continue
        for owned in detail.get("players") or []:
            collect(owned.get("playerMaster") or {})
    if names or images:
        _club_catalog_cache[league_id] = (time.monotonic(), names.copy(), images.copy())
    return names, images


def _unwrap_list(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("players", "items", "data"):
            if isinstance(data.get(key), list):
                return data[key]
    return []


def _integer(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(float(str(value).replace(".", "").replace(",", ".")))
    except (TypeError, ValueError):
        return None


def _decimal(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if number == number and abs(number) != float("inf") else None
    except (TypeError, ValueError):
        return None


def _ff_daily_change(player: dict | None):
    if not player:
        return None
    current = _integer(player.get("current_value"))
    previous = _integer(player.get("value_1d"))
    return current - previous if current is not None and previous is not None else None


def _ff_display_name(player: dict | None) -> str | None:
    if not player:
        return None
    key = str(player.get("normalized_name") or player.get("name") or "").casefold()
    canonical_names = {"jose maria gimenez": "José María Giménez",
                       "marco de la sias": "Marco de la Sías"}
    return canonical_names.get(key, str(player.get("name") or "").title())


def _price_days(source_history: list[dict], local_history: list[dict],
                official_history: list[dict] | None = None) -> tuple[list[dict], dict]:
    today = datetime.now(timezone.utc).date()
    # Keep the day-30 reference plus today: 31 price points define 30 daily intervals.
    first_day = today.fromordinal(today.toordinal() - 30)
    observed: dict[str, dict] = {}
    for point in source_history:
        try:
            date = datetime.fromisoformat(str(point.get("date")).replace("Z", "+00:00")).date()
        except (TypeError, ValueError):
            continue
        price = _integer(point.get("value"))
        if first_day <= date <= today and price is not None and price > 0:
            observed[date.isoformat()] = {"value": price, "source": "FutbolFantasy"}
    for point in official_history or []:
        date = str(point.get("date") or "")
        price = _integer(point.get("value"))
        if first_day.isoformat() <= date <= today.isoformat() and price is not None and price > 0:
            observed[date] = {"value": price,
                              "dailyChange": _integer(point.get("dailyChange")),
                              "source": point.get("source") or "FutbolFantasy"}
    for point in local_history:
        try:
            date = datetime.fromisoformat(str(point.get("observed_at")).replace("Z", "+00:00")).date()
        except (TypeError, ValueError):
            continue
        current = _integer(point.get("current_value"))
        if first_day <= date <= today and current is not None and current > 0:
            observed[date.isoformat()] = {
                "value": current,
                "dailyChange": (current - _integer(point.get("value_1d")))
                if _integer(point.get("value_1d")) is not None else None,
                "source": "Captura local",
            }
    days = []
    for offset in range(31):
        date = today.fromordinal(today.toordinal() - offset)
        day_key = date.isoformat()
        point = observed.get(day_key)
        change = point.get("dailyChange") if point else None
        previous = observed.get(date.fromordinal(date.toordinal() - 1).isoformat())
        if change is None and point and previous:
            change = point["value"] - previous["value"]
        days.append({"date": day_key, "value": point.get("value") if point else None,
                     "dailyChange": change, "source": point.get("source") if point else None})
    values = [point["value"] for point in observed.values()]
    return days, {"high": max(values) if values else None, "low": min(values) if values else None,
                  "observedDays": len(values)}


def _player_detail(master_id: str, requested_manager_id: str | None = None) -> dict:
    selected = storage.get_setting("selected_league_id")
    with _dashboard_lock:
        context = dict(_dashboard_context)
    context_fresh = (context.get("selected") == selected
                     and time.monotonic() - context.get("loaded_at", 0) < 900)
    client = None
    if context_fresh:
        league_id, team_id = context["league_id"], context["team_id"]
        league_teams, market, own_team = context["league_teams"], context["market"], context["team"]
        master_rows = context["players"]
    else:
        client = LaligaClient()
        leagues = client.leagues()
        league_id, team_id = league_id_and_team_id(leagues, selected)
        league_teams = client.league_teams(league_id)
        market = client.market(league_id)
        own_team = client.team(league_id, team_id)
        master_rows = _unwrap_list(client.all_players())
    club_names, club_images = _club_catalog(
        client or LaligaClient(), league_id, own_team,
        league_teams if isinstance(league_teams, list) else [],
        market if isinstance(market, list) else [], master_rows)
    master = next((row for row in master_rows if str(row.get("id")) == master_id), None)
    listing = next((row for row in market if str((row.get("playerMaster") or {}).get("id")) == master_id), None) if isinstance(market, list) else None
    if master is None and listing:
        master = listing.get("playerMaster") or {}
    if not master:
        raise ValueError("No se encontró ese jugador en los datos de la liga.")

    owner = None
    owner_manager_id = None
    owner_activity_ids: set[str] = set()
    clause = None
    clause_locked_until = None
    shielded = False
    if listing and listing.get("discr") == "marketPlayerTeam":
        player_team = listing.get("playerTeam") or {}
        seller = listing.get("sellerTeam") or {}
        manager = seller.get("manager") or player_team.get("manager") or {}
        owner = manager.get("managerName") or "Manager de la liga"
        owner_manager_id = str(manager.get("id") or manager.get("managerId") or
                               player_team.get("managerId") or seller.get("managerId") or
                               player_team.get("id") or seller.get("id") or "") or None
        owner_activity_ids = (_manager_activity_ids(seller) | _manager_activity_ids(player_team) |
                              _manager_activity_ids({"manager": manager}))
        clause = _integer(player_team.get("buyoutClause"))
        clause_locked_until = player_team.get("buyoutClauseLockedEndTime")
        shielded = bool(player_team.get("isShielded"))
    else:
        for rival in league_teams if isinstance(league_teams, list) else []:
            for owned in rival.get("players") or []:
                if str((owned.get("playerMaster") or {}).get("id")) == master_id:
                    manager = rival.get("manager") or {}
                    owner = manager.get("managerName") or rival.get("name")
                    owner_manager_id = _manager_key(rival) or None
                    owner_activity_ids = _manager_activity_ids(rival)
                    clause = _integer(owned.get("buyoutClause"))
                    clause_locked_until = owned.get("buyoutClauseLockedEndTime")
                    shielded = bool(owned.get("isShielded"))
                    break
            if owner:
                break

    manager_by_id = _manager_name_map(league_teams)
    transactions = []
    if context_fresh:
        transactions = [{
            "type": row.get("type"), "manager": row.get("manager", "Manager de la liga"),
            "otherManager": row.get("otherManager", ""), "operation": row.get("operation"),
            "amount": _integer(row.get("amount")), "date": row.get("date"),
        } for row in context.get("activity", []) if str(row.get("playerMasterId")) == master_id]
    else:
        for page in (0, 1):
            try:
                events = client.activity(league_id, page)
            except Exception:
                continue
            for event in events if isinstance(events, list) else []:
                if str(event.get("playerMasterId")) != master_id:
                    continue
                transactions.append({
                    "type": event.get("activityTypeId"),
                    "manager": manager_by_id.get(str(event.get("user1Id")), "Manager de la liga"),
                    "otherManager": manager_by_id.get(str(event.get("user2Id")), ""),
                    "operation": "Traspaso entre managers" if event.get("user2Id") else "Movimiento de mercado o liga",
                    "amount": _integer(event.get("amount")), "date": event.get("createdAt"),
                })
    transactions.sort(key=lambda row: str(row.get("date") or ""), reverse=True)
    known_amounts = sorted(row["amount"] for row in transactions if row["amount"] and row["type"] in (1, 31))
    ff_players = storage.latest_ff_players()
    ff = find_match(_name(master), ff_players)
    history = storage.price_history(ff["player_id"], 120) if ff else []
    official_history = storage.player_daily_prices(ff["player_id"], 31) if ff else []
    source_history = []
    if ff:
        observed = ff.get("fetched_at")
        for days, key in ((30, "value_30d"), (14, "value_14d"), (7, "value_7d"),
                          (3, "value_3d"), (2, "value_2d"), (1, "value_1d"),
                          (0, "current_value")):
            point_value = _integer(ff.get(key))
            if point_value is None or point_value <= 0:
                continue
            try:
                point_date = datetime.fromisoformat(str(observed).replace("Z", "+00:00"))
                if point_date.tzinfo is None:
                    point_date = point_date.replace(tzinfo=timezone.utc)
                point_date = point_date.timestamp() - days * 86400
                source_history.append({"date": datetime.fromtimestamp(point_date, timezone.utc).isoformat(),
                                       "value": point_value, "daysAgo": days})
            except (TypeError, ValueError, OSError):
                continue
    price_days, price_stats = _price_days(source_history, history, official_history)
    value = _integer(master.get("marketValue"))
    asking = _integer(listing.get("salePrice")) if listing else None
    ff_value = _integer(ff.get("current_value")) if ff else None
    clause_premium_amount = clause - value if clause is not None and value is not None else None
    clause_premium_pct = (clause_premium_amount / value * 100
                          if clause_premium_amount is not None and value else None)
    balance = _integer(own_team.get("teamMoney"))
    team_value = _integer(own_team.get("teamValue"))
    own_team_row = next((row for row in (league_teams if isinstance(league_teams, list) else [])
                         if str(row.get("id")) == str(own_team.get("id") or team_id)), {})
    own_manager = own_team_row.get("manager") or own_team.get("manager") or {}
    own_ids = {str(value) for value in (own_manager.get("id"), own_team.get("id"),
               own_team_row.get("managerId"), own_team.get("managerId")) if value}
    is_owned = any(str(((owned.get("playerMaster") or {}).get("id") or "")) == master_id
                   for owned in (own_team.get("players") or []))
    purchase = (manager_purchase_cost(league_id, master_id, owner_activity_ids or owner_manager_id)
                if requested_manager_id and requested_manager_id == owner_manager_id
                else purchase_cost(league_id, master_id, own_ids, current_owner=is_owned))
    budget_limit = balance
    # Only recorded transaction amounts are evidence; sealed open bids are not visible.
    middle = len(known_amounts) // 2
    median = ((known_amounts[middle - 1] + known_amounts[middle]) // 2
              if known_amounts and len(known_amounts) % 2 == 0
              else (known_amounts[middle] if known_amounts else None))
    q1 = known_amounts[(len(known_amounts) - 1) // 4] if known_amounts else None
    q3 = known_amounts[3 * (len(known_amounts) - 1) // 4] if known_amounts else None
    bid_floor = asking if asking is not None else value
    reasonable = bid_floor is not None and value is not None
    ceiling_candidates = [value] if value is not None else []
    if len(known_amounts) >= 3 and median is not None:
        ceiling_candidates.append(median)
    ceiling = min(ceiling_candidates) if ceiling_candidates else None
    if value is None and not known_amounts:
        decision = "Datos insuficientes"
        reasons = ["No hay precio de LALIGA ni operaciones registradas suficientes para valorar este jugador."]
        confidence = "Baja"
    elif not reasonable:
        decision = "No recomendable ahora"
        reasons = ["Falta un precio actual o de salida para valorar la compra."]
        confidence = "Media"
    elif not ceiling_candidates:
        decision = "Datos insuficientes"
        reasons = ["Hay datos de jugador, pero falta una referencia para calcular un rango de oferta con fundamento."]
        confidence = "Baja"
    elif (bid_floor or 0) > (ceiling or 0):
        decision = "No recomendable ahora"
        reasons = ["El precio de salida supera el precio actual de LALIGA y las operaciones registradas."]
        confidence = "Media" if len(known_amounts) >= 3 else "Baja"
    else:
        decision = "Recomendable"
        reasons = ["La salida queda dentro del rango observado frente al precio actual de LALIGA."]
        if len(known_amounts) >= 3:
            reasons.append("El rango también considera operaciones registradas en la liga.")
        confidence = "Media" if len(known_amounts) >= 3 else "Baja"
    if listing:
        guidance = bid_guidance({"listingType": listing.get("discr"), "value": value,
                                  "asking": asking, "dailyChange": _ff_daily_change(ff),
                                  "averagePoints": _decimal(master.get("averagePoints")),
                                  "position": POSITION_NAMES.get(master.get("positionId")),
                                   "bids": listing.get("numberOfBids")},
                                 balance, league_premiums(league_id, position_by_id={
                                     str(row.get("id")): POSITION_NAMES.get(row.get("positionId"), "")
                                     for row in master_rows if row.get("id") is not None}))
        bid_floor, ceiling, bid_target = guidance["floor"], guidance["ceiling"], guidance.get("target")
        if not guidance["recommended"]:
            decision = "Vigilar" if ceiling is not None else "No recomendable ahora"
            reasons = [guidance["reason"]]
        else:
            decision, reasons = "Recomendable", [guidance["reason"]]
        confidence = guidance["confidence"]
    else:
        bid_target = None
    lock_expired = True
    if clause_locked_until:
        try:
            lock_time = datetime.fromisoformat(str(clause_locked_until).replace("Z", "+00:00"))
            if lock_time.tzinfo is None:
                lock_time = lock_time.replace(tzinfo=timezone.utc)
            lock_expired = lock_time <= datetime.now(timezone.utc)
        except ValueError:
            lock_expired = False
    return {
        "id": master_id, "name": _name(master), "position": POSITION_NAMES.get(master.get("positionId"), "Desconocida"),
        "club": _club_name(master, club_names), "image": _player_image(master),
        "clubImage": _club_image(master, club_images),
        "value": value, "asking": asking, "points": _integer(master.get("points")),
        "averagePoints": _decimal(master.get("averagePoints")), "status": master.get("playerStatus"),
        "owner": owner, "ownerManagerId": owner_manager_id,
        "buyoutClause": clause, "clauseLockedUntil": clause_locked_until, "shielded": shielded,
        "purchase": purchase,
        "clausePremiumAmount": clause_premium_amount, "clausePremiumPct": clause_premium_pct,
        "ff": {"name": _ff_display_name(ff), "playerId": ff.get("player_id"),
               "value": ff_value, "trend7d": ff.get("pct_7d"),
               "dailyChange": _ff_daily_change(ff), "dailyPct": ff.get("pct_1d"),
               "observedAt": ff.get("fetched_at")} if ff else None,
        "history": history, "ffHistory": source_history, "priceDays": price_days,
        "priceStats30d": price_stats,
        "priceHistoryCachedAt": storage.player_daily_prices_fetched_at(ff["player_id"]) if ff else None,
        "transactions": transactions,
        "transactionStats": {"count": len(known_amounts), "median": median, "q1": q1, "q3": q3},
        "recommendation": {"decision": decision, "confidence": confidence, "reasons": reasons,
                           "budgetLimit": budget_limit,
                           "budgetBasis": {"balance": balance, "teamValue": team_value,
                                           "debtMargin": 0},
         "offerFloor": bid_floor, "offerCeiling": ceiling, "offerTarget": bid_target,
                           "clauseActionable": bool(owner and clause is not None and clause <= (balance or 0) and not shielded and lock_expired)},
    }


def _activity_rows(client: LaligaClient, league_id: str, manager_by_id: dict[str, str], page: int,
                   player_rows: list[dict] | None = None,
                   events: list[dict] | None = None) -> list[dict]:
    events = events if events is not None else client.activity(league_id, page)
    if not isinstance(events, list):
        return []
    all_players = player_rows if player_rows is not None else _unwrap_list(client.all_players())
    players_by_id = {}
    for player_row in all_players:
        if not isinstance(player_row, dict):
            continue
        nested = player_row.get("playerMaster")
        master = nested if isinstance(nested, dict) else player_row
        for source in (player_row, master):
            for key in ("id", "playerMasterId", "masterId", "playerId", "playerTeamId", "masterPlayerId"):
                value = source.get(key)
                if value is not None and str(value).strip():
                    players_by_id[str(value).strip()] = master
    rows = []
    for event in events:
        player_id = storage.activity_player_id(event)
        embedded = next((event.get(key) for key in ("playerMaster", "player", "footballer", "playerTeam")
                         if isinstance(event.get(key), dict)), {})
        player = players_by_id.get(player_id) or embedded
        player_name = (next((str(player.get(key)).strip() for key in
                             ("nickname", "name", "playerName", "fullName", "shortName", "displayName")
                             if player.get(key) and str(player.get(key)).strip()), None)
                       if isinstance(player, dict) else None)
        if not player_name:
            for key in ("playerName", "footballerName", "player_name", "name"):
                value = event.get(key)
                if isinstance(value, str) and value.strip():
                    player_name = value.strip()
                    break
        rows.append({
            "eventId": str(event.get("id") or ""),
            "playerMasterId": player_id,
            "managerId": str(event.get("user1Id") or ""),
            "otherManagerId": str(event.get("user2Id") or ""),
            "type": event.get("activityTypeId"), "player": player_name or "Jugador no identificado",
            "manager": manager_by_id.get(str(event.get("user1Id")), "Manager de la liga"),
            "otherManager": manager_by_id.get(str(event.get("user2Id")), ""),
            "operation": "Traspaso entre managers" if event.get("user2Id") else "Movimiento de mercado o liga",
            "directionKnown": False,
            "amount": _integer(event.get("amount")), "date": event.get("createdAt"),
        })
    return rows


def dashboard_data() -> dict:
    global _dashboard_context
    client = LaligaClient()
    selected = storage.get_setting("selected_league_id")
    if not selected:
        raise ValueError("No hay liga seleccionada. Ejecuta fantasy-advisor connect.")
    leagues = client.leagues()
    league_id, team_id = league_id_and_team_id(leagues, selected)
    league = next(row for row in leagues if str(row.get("id")) == league_id)
    team = client.team(league_id, team_id)
    _sync_prices()
    _sync_lineups()
    ff_players = storage.latest_ff_players()
    probable = storage.latest_ff_lineups()
    market = client.market(league_id)
    report = build_report(team, ff_players, probable, market if isinstance(market, list) else [])
    # Team and market payloads often omit the portrait URL. The full player
    # catalogue carries it, as used by the player detail modal.
    player_catalog = _unwrap_list(client.all_players())
    player_by_id = {str(row.get("id")): row for row in player_catalog if row.get("id") is not None}
    latest_weeks = sorted({int(point["weekNumber"]) for row in player_catalog
                           for point in (row.get("weekPoints") or [])
                           if isinstance(point, dict) and str(point.get("weekNumber", "")).isdigit()}, reverse=True)[:3]
    observed_at = datetime.now(timezone.utc).isoformat()

    def image_for(master: dict) -> str | None:
        complete = player_by_id.get(str(master.get("id")), {})
        return _player_image(complete) or _player_image(master)

    own_id = str(team.get("id"))
    teams = client.league_teams(league_id)
    manager_by_id = _manager_name_map(teams)
    club_names, club_images = _club_catalog(
        client, league_id, team, teams if isinstance(teams, list) else [],
        market if isinstance(market, list) else [], player_catalog)

    roster = []
    for owned in team.get("players") or []:
        master = owned.get("playerMaster") or {}
        position = POSITION_NAMES.get(master.get("positionId"), "Desconocida")
        if position == "Desconocida":
            continue
        ff = find_match(_name(master), ff_players)
        lineup_info = find_match(_name(master), probable)
        roster.append({
            "id": str(owned.get("id") or master.get("id")),
            "masterId": str(master.get("id") or ""),
            "name": _name(master), "position": position,
            "club": _club_name(master, club_names),
            "clubImage": _club_image(master, club_images),
            "value": _integer(master.get("marketValue")), "points": _integer(master.get("points")),
            "averagePoints": _decimal(master.get("averagePoints")), "status": master.get("playerStatus"),
            "weekPoints": (player_by_id.get(str(master.get("id")), {}).get("weekPoints") or []),
            "image": image_for(master),
            "probability": lineup_info.get("probability") if lineup_info else None,
            "available": lineup_info.get("available", True) if lineup_info else True,
            "ffValue": ff.get("current_value") if ff else None,
            "trend7d": ff.get("pct_7d") if ff else None,
            "dailyChange": _ff_daily_change(ff), "dailyPct": ff.get("pct_1d") if ff else None,
        })

    # Persist the activity independently of how many rows the browser displays.
    raw_activity = []
    activity_complete = False
    activity_last_page = -1
    for page in range(101):
        try:
            events = client.activity(league_id, page)
        except Exception:
            break
        if not isinstance(events, list) or not events:
            activity_complete = True
            break
        raw_activity.extend(events)
        activity_last_page = page
    activity_market = market if isinstance(market, list) else []
    activity_teams = teams if isinstance(teams, list) else []
    activity_player_catalog = [*player_catalog, *(team.get("players") or []), *activity_market]
    for league_team in activity_teams:
        activity_player_catalog.extend(league_team.get("players") or [])
    activity_rows = _activity_rows(client, league_id, manager_by_id, 0,
                                   activity_player_catalog, raw_activity)
    normalized_activity = [
        {**event, "playerMasterId": row["playerMasterId"]}
        for event, row in zip(raw_activity, activity_rows)
    ]
    storage.save_league_observation(league_id, teams if isinstance(teams, list) else [],
                                    activity_market, normalized_activity, observed_at)
    traded_ids = {row["playerMasterId"] for row in activity_rows if row["playerMasterId"]}
    master_to_ff = {}
    for master_id in traded_ids:
        master = player_by_id.get(master_id)
        ff = find_match(_name(master), ff_players) if master else None
        if ff:
            master_to_ff[master_id] = str(ff["player_id"])
    storage.seed_league_values_from_ff(league_id, master_to_ff)
    own_team_row = next((row for row in (teams if isinstance(teams, list) else [])
                         if isinstance(row, dict) and str(row.get("id")) == own_id), {})
    own_manager = own_team_row.get("manager") or team.get("manager") or {}
    own_manager_id = str(own_manager.get("id") or own_team_row.get("managerId") or team.get("managerId") or "")
    own_manager_name = str(own_manager.get("managerName") or own_manager.get("name") or "")
    # Activity user IDs may refer to the manager or to their team, depending on
    # which version of the unofficial API produced the event.
    own_manager_ids = sorted({value for value in (
        own_manager_id, own_id, str(own_team_row.get("managerId") or ""),
        str(team.get("managerId") or ""), str((team.get("manager") or {}).get("id") or ""),
    ) if value})
    own_id_set = set(own_manager_ids)
    for player in roster:
        player["purchase"] = purchase_cost(league_id, player["masterId"], own_id_set)
    rivals = []
    rival_players = []
    manager_rosters = {}
    for rival in teams if isinstance(teams, list) else []:
        manager = rival.get("manager") or {}
        manager_id = _manager_key(rival)
        manager_name = manager.get("managerName") or rival.get("name") or "Manager"
        if str(rival.get("id")) == own_id:
            continue
        rivals.append({
            "managerId": manager_id, "name": manager_name,
            "teamValue": rival.get("teamValue"), "teamPoints": rival.get("teamPoints"),
            "position": rival.get("position"), "players": len(rival.get("players") or []),
            "balance": rival.get("teamMoney"),
        })
        manager_rosters[manager_id] = []
        for owned in rival.get("players") or []:
            master = owned.get("playerMaster") or {}
            position = POSITION_NAMES.get(master.get("positionId"), "Desconocida")
            if position == "Desconocida":
                continue
            ff = find_match(_name(master), ff_players)
            lineup_info = find_match(_name(master), probable)
            manager_rosters[manager_id].append({
                "id": str(owned.get("id") or master.get("id") or ""),
                "masterId": str(master.get("id") or ""), "name": _name(master),
                "position": position, "club": _club_name(master, club_names),
                "image": image_for(master), "clubImage": _club_image(master, club_images),
                "value": _integer(master.get("marketValue")),
                "points": _integer(master.get("points")),
                "averagePoints": _decimal(master.get("averagePoints")),
                "ffValue": _integer(ff.get("current_value")) if ff else None,
                "dailyChange": _ff_daily_change(ff),
                "dailyPct": ff.get("pct_1d") if ff else None,
                "trend7d": ff.get("pct_7d") if ff else None,
                "buyoutClause": _integer(owned.get("buyoutClause")),
                "clauseLockedUntil": owned.get("buyoutClauseLockedEndTime"),
                "shielded": bool(owned.get("isShielded")),
                "purchase": manager_purchase_cost(
                    league_id, str(master.get("id") or ""), _manager_activity_ids(rival)),
            })
            if position == "Entrenador":
                continue
            clause = _integer(owned.get("buyoutClause"))
            market_value = _integer(master.get("marketValue"))
            ff_value = _integer(ff.get("current_value")) if ff else None
            balance = _integer(team.get("teamMoney"))
            premium_amount = clause - market_value if clause is not None and market_value is not None else None
            premium_pct = (premium_amount / market_value * 100
                           if premium_amount is not None and market_value and market_value > 0 else None)
            lock_until = owned.get("buyoutClauseLockedEndTime")
            lock_expired = True
            if lock_until:
                try:
                    lock_time = datetime.fromisoformat(str(lock_until).replace("Z", "+00:00"))
                    if lock_time.tzinfo is None:
                        lock_time = lock_time.replace(tzinfo=timezone.utc)
                    lock_expired = lock_time <= datetime.now(timezone.utc)
                except ValueError:
                    lock_expired = False
            daily_change = _ff_daily_change(ff)
            daily_pct = ff.get("pct_1d") if ff else None
            trend7d = ff.get("pct_7d") if ff else None
            average_points = _decimal(master.get("averagePoints"))
            rival_players.append({
                "masterId": str(master.get("id") or ""), "name": _name(master),
                "position": position, "club": _club_name(master, club_names),
                "image": image_for(master),
                "clubImage": _club_image(master, club_images),
                "managerId": manager_id, "manager": manager_name, "value": market_value, "points": _integer(master.get("points")),
                "averagePoints": average_points,
                "probability": lineup_info.get("probability") if lineup_info else None,
                "available": lineup_info.get("available", True) if lineup_info else None,
                "ffValue": ff_value, "dailyChange": daily_change, "dailyPct": daily_pct,
                "trend7d": trend7d, "buyoutClause": clause,
                "clauseLockedUntil": lock_until, "shielded": bool(owned.get("isShielded")),
                "premiumAmount": premium_amount, "premiumPct": premium_pct,
                "clauseActionable": bool(clause is not None and balance is not None and clause <= balance
                                          and not owned.get("isShielded") and lock_expired),
                "rising": bool((daily_change is not None and daily_change > 0)
                               or (trend7d is not None and trend7d > 0)),
            })

    own_manager_key = _manager_key(own_team_row or team)
    rivals.append({"managerId": own_manager_key, "name": own_manager_name or team.get("name") or "Mi equipo",
                   "isOwn": True, "teamValue": team.get("teamValue"), "teamPoints": team.get("teamPoints"),
                   "position": own_team_row.get("position") or team.get("position"),
                   "players": len(roster), "balance": team.get("teamMoney"), "roster": roster})
    manager_rosters[own_manager_key] = roster

    # Approximate each roster's daily market movement from the observed FF price
    # change of its players. This is a covered subset, not a game-reported balance.
    rival_price_stats = {}
    for player in rival_players:
        stats = rival_price_stats.setdefault(player["managerId"], {"change": 0, "value": 0, "priced": 0, "eligible": 0})
        stats["eligible"] += 1
        if player["dailyChange"] is None:
            continue
        stats["change"] += player["dailyChange"]
        stats["value"] += player["value"] or 0
        stats["priced"] += 1
    for rival in rivals:
        stats = rival_price_stats.get(rival["managerId"], {"change": 0, "value": 0, "priced": 0, "eligible": 0})
        rival["dailyValueChange"] = stats["change"] if stats["priced"] else None
        rival["dailyValuePct"] = round(stats["change"] / stats["value"] * 100, 2) if stats["value"] else None
        rival["pricedPlayers"] = stats["priced"]
        rival["eligiblePlayers"] = stats["eligible"]
        rival["roster"] = manager_rosters.get(rival["managerId"], [])
        rival_team = next((row for row in teams if str((row or {}).get("managerId")) == rival["managerId"]), None)
        if rival_team:
            previous = storage.previous_team_value(league_id, str(rival_team.get("id")), observed_at[:10])
            if previous and previous[1] == datetime.fromordinal(datetime.fromisoformat(observed_at[:10]).toordinal() - 1).date().isoformat():
                rival["dailyValueChange"] = (_integer(rival["teamValue"]) or 0) - previous[0]
                rival["dailyValueSource"] = "Cambio de valor del equipo desde ayer; puede incluir fichajes y ventas"

    own_priced = [p for p in roster if p["position"] != "Entrenador" and p["dailyChange"] is not None]
    own_price_change = sum(p["dailyChange"] for p in own_priced) if own_priced else None
    own_price_base = sum(p["value"] or 0 for p in own_priced)
    own_price_pct = round(own_price_change / own_price_base * 100, 2) if own_price_base else None
    previous_own = storage.previous_team_value(league_id, own_id, observed_at[:10])
    if previous_own and previous_own[1] == datetime.fromordinal(datetime.fromisoformat(observed_at[:10]).toordinal() - 1).date().isoformat():
        own_price_change = (_integer(team.get("teamValue")) or 0) - previous_own[0]
        own_price_pct = round(own_price_change / previous_own[0] * 100, 2) if previous_own[0] else None
        own_price_source = "Cambio de valor del equipo desde ayer; puede incluir fichajes y ventas"
    else:
        own_price_source = "Suma parcial de cambios de precio FF; entrenadores sin cobertura"
    own_summary = next(row for row in rivals if row.get("isOwn"))
    own_summary.update({"dailyValueChange": own_price_change, "dailyValuePct": own_price_pct,
                        "dailyValueSource": own_price_source, "pricedPlayers": len(own_priced),
                        "eligiblePlayers": sum(p["position"] != "Entrenador" for p in roster),
                        "roster": roster})

    averages = sorted(p["averagePoints"] for p in rival_players if p["averagePoints"] is not None)
    median_average = averages[len(averages) // 2] if averages else None
    radar = []
    for player in rival_players:
        productive = median_average is not None and player["averagePoints"] is not None and player["averagePoints"] >= median_average
        player["productive"] = productive
        player["signals"] = [label for condition, label in (
            (player["rising"], "Valor al alza"), (productive, "Media de puntos alta en la liga"),
            (player["probability"] is not None and player["probability"] >= 70, "Probabilidad de titularidad alta"),
        ) if condition]
        player["recommendation"] = (
            "Recomendable" if player["signals"] and player["clauseActionable"]
            and player["value"] is not None and player["buyoutClause"] <= player["value"]
            else "Vigilar" if player["signals"] else "Sin señal destacada"
        )
        player["recommendationReason"] = (
            "Cláusula igual o inferior al precio actual del juego y señal de rendimiento o tendencia; confirma minutos y calendario."
            if player["recommendation"] == "Recomendable" else
            "Tiene señales deportivas o de valor, pero la cláusula, el bloqueo o los datos disponibles no permiten recomendar el pago ahora."
            if player["signals"] else "Sin tendencia positiva, rendimiento relativo o titularidad suficiente para priorizarlo."
        )
        if player["signals"]:
            radar.append(player)
    radar.sort(key=lambda p: (p["recommendation"] == "Recomendable", len(p["signals"]),
                             p["averagePoints"] or 0, p["dailyChange"] or 0), reverse=True)

    own_player_rows = [p for p in roster if p["position"] != "Entrenador"]
    own_averages = sorted(p["averagePoints"] for p in own_player_rows if p["averagePoints"] is not None)
    own_median_average = own_averages[len(own_averages) // 2] if own_averages else None
    sell_recommendations = []
    for player in own_player_rows:
        signals = []
        if player["probability"] is not None and player["probability"] < 40:
            signals.append("Titularidad probable baja")
        if own_median_average is not None and player["averagePoints"] is not None and player["averagePoints"] < own_median_average:
            signals.append("Media de puntos por debajo de tu plantilla")
        if player["trend7d"] is not None and player["trend7d"] <= -5:
            signals.append("Valor FF cayendo esta semana")
        if len(signals) >= 2:
            sell_recommendations.append({**player, "signals": signals, "recommendation": "Evaluar venta"})
    sell_recommendations.sort(key=lambda p: (len(p["signals"]), p["probability"] if p["probability"] is not None else 100,
                                             p["averagePoints"] or 0))
    premiums = league_premiums(league_id, position_by_id={
        str(row.get("id")): POSITION_NAMES.get(row.get("positionId"), "")
        for row in player_catalog if row.get("id") is not None})
    market_rows = []
    for listing in market if isinstance(market, list) else []:
        master = listing.get("playerMaster") or {}
        ff = find_match(_name(master), ff_players)
        lineup_info = find_match(_name(master), probable)
        club = _club_name(master, club_names)
        market_rows.append({
            "id": str(listing.get("id")), "masterId": str(master.get("id") or ""), "name": _name(master),
            "position": POSITION_NAMES.get(master.get("positionId"), "Desconocida"),
            "club": club,
            "value": master.get("marketValue"), "asking": listing.get("salePrice"),
            "points": master.get("points"), "averagePoints": _decimal(master.get("averagePoints")),
            "probability": lineup_info.get("probability") if lineup_info else None,
            "available": lineup_info.get("available", True) if lineup_info else None,
            "image": image_for(master),
            "clubImage": _club_image(master, club_images),
            "bids": listing.get("numberOfBids"), "offers": listing.get("numberOfOffers"),
            "trend7d": ff.get("pct_7d") if ff else None, "ffValue": ff.get("current_value") if ff else None,
            "dailyChange": _ff_daily_change(ff), "dailyPct": ff.get("pct_1d") if ff else None,
            "status": master.get("playerStatus"),
            "owner": (((listing.get("sellerTeam") or {}).get("manager") or {}).get("managerName")
                      if listing.get("discr") == "marketPlayerTeam" else None),
            "listingType": listing.get("discr"),
            "buyoutClause": _integer(((listing.get("playerTeam") or {}).get("buyoutClause"))),
            "clauseLockedUntil": (listing.get("playerTeam") or {}).get("buyoutClauseLockedEndTime"),
            "shielded": bool((listing.get("playerTeam") or {}).get("isShielded")),
        })
    affordability = _integer(team.get("teamMoney"))
    missing = report.get("lineup", {}).get("missing") if report.get("lineup") else {}
    missing_position = next(iter(missing), None)
    for row in market_rows:
        guidance = bid_guidance(row, affordability, premiums, missing_position)
        row["bidGuidance"] = guidance
        row["recommended"] = guidance["recommended"]
        row["recommendedBidTarget"] = guidance.get("target")
        row["recommendedBidFloor"] = guidance["floor"] if guidance["recommended"] else None
        row["recommendedBidCeiling"] = guidance["ceiling"] if guidance["recommended"] else None
    with _dashboard_lock:
        _dashboard_context = {
            "selected": selected, "loaded_at": time.monotonic(), "league_id": league_id,
            "team_id": team_id, "league_teams": teams, "market": market,
            "team": team, "players": player_catalog, "activity_players": activity_player_catalog,
            "activity": activity_rows,
            "club_names": club_names,
        }
    return {
        "league": league.get("leagueName") or league.get("name") or "Mi liga",
        "ownManagerId": own_manager_id,
        "ownManagerIds": own_manager_ids,
        "ownManagerName": own_manager_name,
        "ownTeamId": own_id,
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "balance": team.get("teamMoney"), "teamValue": team.get("teamValue"),
        "netWorth": (_integer(team.get("teamMoney")) or 0) + (_integer(team.get("teamValue")) or 0),
        "leaguePremiums": premiums,
        "teamPoints": team.get("teamPoints"), "teamPosition": own_team_row.get("position") or team.get("position"),
        "fixturePoints": own_team_row.get("fixturePoints"), "roster": roster, "rivals": rivals,
        "fixtures": _fixture_data(storage.latest_ff_fixtures(), probable, player_catalog, ff_players, club_names),
        "latestWeeks": sorted(latest_weeks),
        "managerRosters": manager_rosters,
        "teamPriceMovement": {"dailyChange": own_price_change, "dailyPct": own_price_pct,
                              "source": own_price_source,
                              "pricedPlayers": len(own_priced),
                              "eligiblePlayers": sum(p["position"] != "Entrenador" for p in roster)},
        "market": market_rows, "activity": activity_rows,
        "clauseRadar": radar[:40], "sellRecommendations": sell_recommendations[:20],
        "activityCount": len(activity_rows), "activityComplete": activity_complete,
        "activityLastPage": activity_last_page,
        "activityObservedCount": len(raw_activity),
        "report": report,
        "sources": {"pricesAt": storage.latest_price_sync(), "lineupsAt": storage.latest_lineup_sync()},
    }


def _refresh_dashboard_worker():
    try:
        result = dashboard_data()
        with _dashboard_lock:
            _dashboard_state.update(loading=False, data=result, error=None)
    except Exception:
        with _dashboard_lock:
            _dashboard_state.update(loading=False, data=None,
                                    error="No se pudieron cargar los datos. Revisa Cuenta y liga y vuelve a actualizar.")


def _start_dashboard_refresh(force: bool = False):
    with _dashboard_lock:
        if _dashboard_state["loading"]:
            return
        if not force and _dashboard_state["data"]:
            return
        _dashboard_state.update(loading=True, data=None, error=None)
        threading.Thread(target=_refresh_dashboard_worker, daemon=True).start()


class _Handler(BaseHTTPRequestHandler):
    server_version = "FantasyAdvisorLocal/0.1"

    def log_message(self, _format, *args):
        return

    def _send(self, status: int, body: bytes, content_type: str):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, data):
        self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _is_local_request(self) -> bool:
        parsed_host = urlparse("http://" + self.headers.get("Host", "")).hostname
        if parsed_host not in {"127.0.0.1", "localhost", "::1"}:
            return False
        origin = self.headers.get("Origin")
        if origin:
            parsed_origin = urlparse(origin)
            expected_port = self.server.server_address[1]
            if parsed_origin.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed_origin.port != expected_port:
                return False
        return True

    def do_GET(self):
        if not self._is_local_request():
            self._json(403, {"error": "Solo disponible desde este dispositivo."})
            return
        path = urlparse(self.path).path
        query = urlparse(self.path).query
        if path == "/api/auth/status":
            try:
                auth.load_tokens()
                connected = True
            except auth.AuthError:
                connected = False
            self._json(200, {"connected": connected, "leagueId": storage.get_setting("selected_league_id")})
            return
        if path == "/api/auth/leagues":
            try:
                leagues = LaligaClient().leagues()
                self._json(200, {"leagues": [{"id": str(row.get("id")),
                    "name": row.get("leagueName") or row.get("name") or "Liga"} for row in leagues]})
            except Exception:
                self._json(503, {"error": "No se pudieron leer las ligas. Comprueba la sesión."})
            return
        if path == "/api/search":
            term = normalize(parse_qs(query).get("q", [""])[0])[:80]
            if len(term) < 2:
                self._json(200, {"items": []})
                return
            with _dashboard_lock:
                context = dict(_dashboard_context)
            items = []
            for row in context.get("players") or []:
                name = _name(row)
                if term in normalize(name):
                    items.append({"type": "player", "id": str(row.get("id")), "name": name,
                                  "detail": POSITION_NAMES.get(row.get("positionId"), "Jugador"),
                                  "image": _player_image(row)})
                if len(items) >= 12:
                    break
            for row in context.get("league_teams") or []:
                name = (row.get("manager") or {}).get("managerName") or row.get("name") or ""
                if term in normalize(name):
                    items.append({"type": "manager", "id": _manager_key(row), "name": name, "detail": "Manager"})
            clubs = set()
            club_names = context.get("club_names") or {}
            for row in context.get("players") or []:
                club = _club_name(row, club_names)
                if club != "LaLiga" and term in normalize(club) and club not in clubs:
                    clubs.add(club)
                    items.append({"type": "club", "name": club, "detail": "Club"})
                    if len(clubs) >= 5:
                        break
            self._json(200, {"items": items[:20]})
            return
        if path.startswith("/api/ff-prices/"):
            player_id = path.removeprefix("/api/ff-prices/")
            if not player_id.isdigit():
                self._json(400, {"error": "Identificador de FutbolFantasy no válido."})
                return
            cached = storage.player_daily_prices(player_id, 31)
            fetched_at = storage.player_daily_prices_fetched_at(player_id)
            fresh = False
            if fetched_at:
                try:
                    synced = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
                    if synced.tzinfo is None:
                        synced = synced.replace(tzinfo=timezone.utc)
                    fresh = (datetime.now(timezone.utc) - synced).total_seconds() < 12 * 3600
                except ValueError:
                    pass
            stale = bool(cached and not fresh)
            if not fresh:
                try:
                    rows, fetched_at = fetch_player_daily_prices(player_id)
                    storage.save_player_daily_prices(player_id, rows, fetched_at)
                    cached = storage.player_daily_prices(player_id, 31)
                    stale = False
                except Exception:
                    if not cached:
                        self._json(503, {"error": "FutbolFantasy no devolvió el historial diario."})
                        return
            daily, stats = _price_days([], [], cached)
            self._json(200, {"priceDays": daily, "priceStats30d": stats,
                             "priceHistoryCachedAt": fetched_at, "stale": stale})
            return
        if path.startswith("/api/player/"):
            player_id = path.removeprefix("/api/player/")
            if not player_id.isdigit():
                self._json(400, {"error": "Identificador de jugador no válido."})
                return
            try:
                manager_id = parse_qs(query).get("managerId", [None])[0]
                if manager_id is not None and not manager_id.isdigit():
                    self._json(400, {"error": "Identificador de manager no válido."})
                    return
                self._json(200, _player_detail(player_id, manager_id))
            except ValueError as exc:
                self._json(404, {"error": str(exc)})
            except Exception:
                self._json(503, {"error": "No se pudo cargar el detalle del jugador."})
            return
        if path == "/api/activity":
            try:
                page = int(query.partition("page=")[2].partition("&")[0] or "0")
                if not 0 <= page <= 100:
                    raise ValueError("Página no válida.")
                client = LaligaClient()
                selected = storage.get_setting("selected_league_id")
                leagues = client.leagues()
                league_id, _team_id = league_id_and_team_id(leagues, selected)
                teams = client.league_teams(league_id)
                manager_by_id = _manager_name_map(teams)
                with _dashboard_lock:
                    context = dict(_dashboard_context)
                player_rows = context.get("activity_players") if context.get("league_id") == league_id else None
                if player_rows is None:
                    player_rows = _unwrap_list(client.all_players())
                rows = _activity_rows(client, league_id, manager_by_id, page, player_rows)
                self._json(200, {"page": page, "count": len(rows), "items": rows})
            except Exception:
                self._json(503, {"error": "No se pudo cargar esa página del historial."})
            return
        if path == "/api/dashboard":
            _start_dashboard_refresh()
            with _dashboard_lock:
                snapshot = dict(_dashboard_state)
            if snapshot["data"]:
                self._json(200, snapshot["data"])
            elif snapshot["error"]:
                self._json(503, {"error": snapshot["error"]})
            else:
                self._json(202, {"loading": True})
            return
        if path == "/api/draft":
            draft = storage.get_lineup_draft("default")
            self._json(200, draft or {"name": "Mi alineación", "formation": "4-3-3", "player_ids": [], "captain_id": None})
            return
        if path == "/api/ai-settings":
            from .ai import settings_status
            self._json(200, settings_status())
            return
        assets = {"/": "index.html", "/app.js": "app.js", "/styles.css": "styles.css",
                  "/conectar": "login.html", "/login.js": "login.js",
                  "/configuracion": "settings.html", "/settings.js": "settings.js",
                  "/settings.css": "settings.css"}
        filename = assets.get(path)
        if not filename:
            self._json(404, {"error": "No encontrado"})
            return
        content_type = "text/html; charset=utf-8" if filename.endswith(".html") else ("text/javascript; charset=utf-8" if filename.endswith(".js") else "text/css; charset=utf-8")
        try:
            self._send(200, (WEB_DIR / filename).read_bytes(), content_type)
        except OSError:
            self._json(500, {"error": "Falta un archivo de la interfaz."})

    def do_POST(self):
        if not self._is_local_request():
            self._json(403, {"error": "Solo disponible desde este dispositivo."})
            return
        path = urlparse(self.path).path
        if path in {"/api/ai-advice", "/api/ai-test"}:
            self._json(410, {"error": "El análisis con IA está desactivado en esta versión."})
            return
        if path.startswith("/api/auth/"):
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size > 20_000:
                    raise ValueError("Solicitud demasiado grande.")
                body = json.loads(self.rfile.read(size)) if size else {}
                if path == "/api/auth/start":
                    auth._keyring()
                    verifier, challenge = auth.make_pkce()
                    state = secrets.token_urlsafe(24)
                    flow = secrets.token_urlsafe(24)
                    with _dashboard_lock:
                        _pending_auth.clear()
                        _pending_auth[flow] = (state, verifier, time.monotonic())
                    self._json(200, {"flow": flow, "url": auth.authorization_url(challenge, state)})
                elif path == "/api/auth/complete":
                    flow = str(body.get("flow") or "")
                    callback = str(body.get("callback") or "")
                    with _dashboard_lock:
                        pending = _pending_auth.pop(flow, None)
                    if not pending or time.monotonic() - pending[2] > 600:
                        raise ValueError("El intento de conexión caducó. Empieza de nuevo.")
                    auth.redeem_authorization_code(callback, pending[0], pending[1])
                    client = LaligaClient()
                    client.me()
                    leagues = client.leagues()
                    if not leagues:
                        raise ValueError("La cuenta conectó, pero no tiene ligas disponibles.")
                    if len(leagues) == 1:
                        league_id, team_id = league_id_and_team_id(leagues, str(leagues[0]["id"]))
                        storage.set_setting("selected_league_id", league_id)
                        storage.set_setting("selected_team_id", team_id)
                    self._json(200, {"leagues": [{"id": str(row.get("id")),
                        "name": row.get("leagueName") or row.get("name") or "Liga"} for row in leagues]})
                elif path == "/api/auth/select":
                    leagues = LaligaClient().leagues()
                    league_id, team_id = league_id_and_team_id(leagues, str(body.get("leagueId") or ""))
                    storage.set_setting("selected_league_id", league_id)
                    storage.set_setting("selected_team_id", team_id)
                    with _dashboard_lock:
                        _dashboard_state.update(loading=False, data=None, error=None)
                    self._json(200, {"connected": True})
                elif path == "/api/auth/logout":
                    auth.delete_tokens()
                    with _dashboard_lock:
                        _dashboard_state.update(loading=False, data=None, error=None)
                    self._json(200, {"connected": False})
                else:
                    self._json(404, {"error": "No encontrado"})
            except (ValueError, auth.AuthError) as exc:
                self._json(400, {"error": str(exc)})
            except Exception:
                self._json(503, {"error": "No se pudo completar la conexión. Inténtalo de nuevo."})
            return
        if path == "/api/ai-settings":
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size < 2 or size > 4096:
                    raise ValueError("Formulario no válido.")
                body = json.loads(self.rfile.read(size))
                from .ai import save_settings
                result = save_settings(body.get("apiKey"), body.get("model"), bool(body.get("removeKey")))
                self._json(200, result)
            except ValueError as exc:
                self._json(400, {"error": str(exc)})
            except Exception:
                self._json(503, {"error": "No se pudo guardar la configuración segura. Revisa el almacén de credenciales de Windows."})
            return
        if path == "/api/ai-test":
            try:
                from .ai import AIProviderError, test_connection
                self._json(200, test_connection())
            except AIProviderError as exc:
                self._json(502, {"error": str(exc)})
            except ValueError as exc:
                self._json(400, {"error": str(exc)})
            except Exception:
                self._json(503, {"error": "No se pudo probar la conexión con OpenRouter. Revisa Configuración IA e inténtalo de nuevo."})
            return
        if path == "/api/ai-advice":
            with _dashboard_lock:
                dashboard = _dashboard_state.get("data")
            if not dashboard:
                self._json(409, {"error": "Actualiza la liga antes de pedir el análisis."})
                return
            try:
                from .ai import AIProviderError, explain_dashboard
                self._json(200, explain_dashboard(dashboard))
            except AIProviderError as exc:
                self._json(502, {"error": str(exc)})
            except ValueError as exc:
                self._json(400, {"error": str(exc)})
            except Exception:
                self._json(503, {"error": "No se pudo obtener el análisis de OpenRouter. Revisa Configuración IA e inténtalo de nuevo."})
            return
        if path == "/api/refresh":
            _start_dashboard_refresh(force=True)
            self._json(202, {"loading": True})
            return
        if path != "/api/draft":
            self._json(404, {"error": "No encontrado"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size < 2 or size > 16_384:
                raise ValueError("Tamaño no válido.")
            body = json.loads(self.rfile.read(size))
            formation = str(body.get("formation", ""))
            player_ids = [str(item) if item else None for item in body.get("player_ids", [])]
            captain_id = str(body.get("captain_id")) if body.get("captain_id") else None
            allowed = {"3-4-3": (3, 4, 3), "3-5-2": (3, 5, 2), "4-3-3": (4, 3, 3),
                       "4-4-2": (4, 4, 2), "4-5-1": (4, 5, 1), "5-3-2": (5, 3, 2), "5-4-1": (5, 4, 1),
                       "3-6-1": (3, 6, 1), "3-3-4": (3, 3, 4), "4-2-4": (4, 2, 4),
                       "4-6-0": (4, 6, 0), "5-2-3": (5, 2, 3)}
            if formation not in allowed:
                raise ValueError("Formación no válida.")
            if len(player_ids) > 11 or len([p for p in player_ids if p]) != len(set(p for p in player_ids if p)):
                raise ValueError("La alineación no puede superar 11 jugadores.")
            league_id = storage.get_setting("selected_league_id")
            if not league_id:
                raise ValueError("No hay una liga seleccionada.")
            client = LaligaClient()
            leagues = client.leagues()
            league_id, team_id = league_id_and_team_id(leagues, league_id)
            team = client.team(league_id, team_id)
            owned = {}
            for item in team.get("players") or []:
                master = item.get("playerMaster") or {}
                player_id = str(item.get("id") or master.get("id"))
                owned[player_id] = POSITION_NAMES.get(master.get("positionId"))
            expected = ["Portero", *(["Defensa"] * allowed[formation][0]),
                        *(["Medio"] * allowed[formation][1]), *(["Delantero"] * allowed[formation][2])]
            for index, player_id in enumerate(player_ids):
                if player_id and (player_id not in owned or index >= 11 or owned[player_id] != expected[index]):
                    raise ValueError("La alineación debe usar jugadores propios en su posición.")
            if captain_id and (captain_id not in player_ids or owned.get(captain_id) == "Entrenador"):
                raise ValueError("El capitán debe ser un jugador de campo incluido en el once.")
            storage.save_lineup_draft("default", "Mi alineación", formation, player_ids,
                                      datetime.now(timezone.utc).isoformat(), captain_id)
            self._json(200, {"saved": True})
        except (ValueError, json.JSONDecodeError) as exc:
            self._json(400, {"error": str(exc)})


def run_server(port: int = 8765, open_browser: bool = True):
    server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    server.daemon_threads = True
    url = f"http://127.0.0.1:{port}"
    print(f"Fantasy Advisor local · {url} · solo lectura")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor local detenido.")
    finally:
        server.server_close()
