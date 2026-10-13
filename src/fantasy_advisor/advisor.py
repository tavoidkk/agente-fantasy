"""Explainable, read-only first-pass squad and market recommendations."""

from __future__ import annotations

from datetime import datetime, timezone

from .matching import find_match

POSITIONS = {1: "Portero", 2: "Defensa", 3: "Medio", 4: "Delantero"}
COACH_POSITION_ID = 5
FORMATIONS = ((3, 4, 3), (3, 5, 2), (4, 3, 3), (4, 4, 2), (4, 5, 1),
              (5, 3, 2), (5, 4, 1), (3, 6, 1), (3, 3, 4), (4, 2, 4),
              (4, 6, 0), (5, 2, 3))


def _owned_players(team: dict) -> list[dict]:
    players = team.get("players")
    if not isinstance(players, list):
        raise ValueError("La respuesta de LALIGA no incluye la plantilla esperada.")
    return players


def _master(player: dict) -> dict:
    return player.get("playerMaster") or {}


def _name(player: dict) -> str:
    master = _master(player)
    return master.get("nickname") or master.get("name") or "Jugador sin nombre"


def _position(player: dict) -> str:
    return POSITIONS.get(_master(player).get("positionId"), "Desconocida")


def _lineup_info(player: dict, probable_lineups: list[dict]) -> dict | None:
    master = _master(player)
    for candidate_name in (master.get("nickname"), master.get("name")):
        if not candidate_name:
            continue
        match = find_match(candidate_name, probable_lineups)
        if match:
            return match
    return None


def suggest_lineup(players: list[dict], probable_lineups: list[dict]) -> dict:
    by_position: dict[str, list[dict]] = {key: [] for key in POSITIONS.values()}
    for player in players:
        position = _position(player)
        if position not in by_position:
            continue
        info = _lineup_info(player, probable_lineups)
        api_status = str(_master(player).get("playerStatus") or "ok").lower()
        is_available = (info.get("available", True) if info else True) and api_status in ("ok", "duda", "doubtful", "warned")
        probability = info.get("probability") if info else None
        # Unknown is kept behind every known positive probability and ahead of players
        # explicitly forecast at 0%; it is labelled unknown in the final report.
        rank = -100 if not is_available else (1 if probability is None else probability)
        by_position[position].append({
            "player": player,
            "id": str(player.get("id") or _master(player).get("id") or ""),
            "masterId": str(_master(player).get("id") or ""),
            "name": _name(player),
            "position": position,
            "probability": probability,
            "known": probability is not None,
            "available": is_available,
            "rank": rank,
            "expectedPoints": max(0.0, float(_master(player).get("averagePoints") or 0))
            * (max(0, min(100, probability)) / 100 if probability is not None else 0.5)
            if is_available else 0.0,
        })
    for entries in by_position.values():
        entries.sort(key=lambda item: (item["available"], item["expectedPoints"],
                                       item["rank"], _master(item["player"]).get("lastSeasonPoints") or 0), reverse=True)

    best = None
    for defenders, midfielders, forwards in FORMATIONS:
        requirements = {
            "Portero": 1,
            "Defensa": defenders,
            "Medio": midfielders,
            "Delantero": forwards,
        }
        selected = []
        score = 0.0
        known_count = 0
        complete = True
        for position, count in requirements.items():
            choices = by_position[position][:count]
            selected.extend(choices)
            if len(choices) < count:
                complete = False
            for choice in choices:
                score += choice["expectedPoints"]
                known_count += int(choice["known"])
        candidate = {
            "formation": (defenders, midfielders, forwards),
            "players": selected,
            "complete": complete,
            "missing": {position: count - min(count, len(by_position[position]))
                        for position, count in requirements.items()
                        if len(by_position[position]) < count},
            "score": (int(complete), len(selected), score, known_count),
        }
        if best is None or candidate["score"] > best["score"]:
            best = candidate
    return best or {"formation": None, "players": [], "complete": False,
                    "missing": {}, "score": (0, 0, 0, 0)}


def _change_text(player: dict, days: int = 7) -> str:
    pct = player.get(f"pct_{days}d")
    if pct is None:
        return "sin variación disponible"
    sign = "+" if pct > 0 else ""
    return f"{sign}{pct:.1f}% en {days} días"


def build_report(team: dict, ff_players: list[dict], probable_lineups: list[dict], market: list[dict] | None = None) -> dict:
    all_roster = _owned_players(team)
    roster = [player for player in all_roster if _master(player).get("positionId") in POSITIONS]
    coaches = [player for player in all_roster if _master(player).get("positionId") == COACH_POSITION_ID]
    unsupported = len(all_roster) - len(roster) - len(coaches)
    trends = []
    for player in roster:
        source = None
        for candidate_name in (_master(player).get("nickname"), _master(player).get("name")):
            if candidate_name:
                source = find_match(candidate_name, ff_players)
                if source:
                    break
        trends.append({
            "name": _name(player),
            "position": _position(player),
            "market_value": _master(player).get("marketValue"),
            "ff": source,
        })

    lineup = suggest_lineup(roster, probable_lineups)
    rising = [p for p in trends if p["ff"] and p["ff"].get("pct_7d") is not None and p["ff"]["pct_7d"] >= 5]
    falling = [p for p in trends if p["ff"] and p["ff"].get("pct_7d") is not None and p["ff"]["pct_7d"] <= -5]

    opportunities = []
    for item in market or []:
        if item.get("discr") != "marketPlayerLeague":
            continue
        master = item.get("playerMaster") or {}
        match = None
        for candidate_name in (master.get("nickname"), master.get("name")):
            if candidate_name:
                match = find_match(candidate_name, ff_players)
                if match:
                    break
        if not match:
            continue
        asking = item.get("salePrice")
        current = match.get("current_value")
        if isinstance(asking, (int, float)) and isinstance(current, (int, float)) and current > 0:
            gap_pct = (current - asking) / current * 100
            if gap_pct >= 5:
                opportunities.append({"name": master.get("nickname") or master.get("name"),
                                      "asking_price": asking, "ff_value": current,
                                      "gap_pct": gap_pct, "trend_7d": match.get("pct_7d")})
    opportunities.sort(key=lambda item: item["gap_pct"], reverse=True)

    warnings = [
        "El XI prioriza la probabilidad de titularidad; no es una predicción exacta de puntos.",
        "Una diferencia de precio es una señal para revisar, no una garantía de beneficio.",
        "Los jugadores que no se pudieron emparejar entre fuentes se muestran sin datos de FutbolFantasy.",
    ]
    if coaches:
        warnings.append(f"Se excluyeron {len(coaches)} entrenadores del XI y del análisis de precios.")
    if unsupported:
        warnings.append(f"Se excluyeron {unsupported} elementos con posición no reconocida.")

    return {
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "balance": team.get("teamMoney"),
        "roster": trends,
        "lineup": lineup,
        "rising": rising,
        "falling": falling,
        "market_opportunities": opportunities[:10],
        "warnings": warnings,
    }
