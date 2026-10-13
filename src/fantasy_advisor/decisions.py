"""Evidence-bound market prices and purchase costs; no game mutations."""

from __future__ import annotations

from datetime import datetime, timezone
from math import ceil
from statistics import median

from . import storage


def _date(value: str | None) -> datetime | None:
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result
    except (TypeError, ValueError):
        return None


def purchase_cost(league_id: str, player_id: str, own_ids: set[str],
                  current_owner: bool = True) -> dict:
    override = storage.purchase_cost_override(league_id, player_id)
    if override is not None:
        return {"amount": override, "confidence": "Confirmada por ti", "source": "manual"}
    if not current_owner:
        return {"amount": None, "confidence": "Sin dato", "source": None}
    events = [row for row in storage.league_activity(league_id)
              if row["player_id"] == player_id and row["event_type"] in (1, 31)
              and isinstance(row["amount"], int) and row["amount"] > 0]
    if not events:
        return {"amount": None, "confidence": "Sin dato", "source": None}
    # The feed does not publish transaction direction. An own-party event is
    # therefore an inference, even if the player is currently on our roster.
    own = [row for row in events if row["user1_id"] in own_ids or row["user2_id"] in own_ids]
    if own:
        event = own[0]
        return {"amount": event["amount"], "confidence": "Inferida", "source": "actividad",
                "eventId": event["event_id"], "date": event["occurred_at"]}
    # For old feeds without attributable actor IDs, the latest priced transfer
    # of a player still owned is useful, but must remain visibly low confidence.
    event = events[0]
    return {"amount": event["amount"], "confidence": "Orientativa", "source": "último traspaso visible",
            "eventId": event["event_id"], "date": event["occurred_at"]}


def manager_purchase_cost(league_id: str, player_id: str, manager_ids: str | set[str]) -> dict:
    """Infer a current rival's cost only from an event naming that manager/team.

    Activity does not publish transfer direction, so this remains explicitly
    orientative even when the manager currently owns the player. API versions
    differ on whether activity actor IDs refer to the manager or their team.
    """
    if isinstance(manager_ids, str):
        actor_ids = {manager_ids.strip()} if manager_ids.strip() else set()
    else:
        actor_ids = {str(value).strip() for value in manager_ids
                     if value is not None and str(value).strip()}
    if not actor_ids:
        return {"amount": None, "confidence": "Sin dato", "source": None}
    events = [row for row in storage.league_activity(league_id)
              if row["player_id"] == str(player_id) and row["event_type"] in (1, 31)
              and isinstance(row["amount"], int) and row["amount"] > 0
              and actor_ids.intersection({str(row.get("user1_id") or ""),
                                          str(row.get("user2_id") or "")})]
    if not events:
        return {"amount": None, "confidence": "Sin dato", "source": None}
    # Do not rely on the database's input order; choose the newest matching
    # transfer so a previous owner's purchase cannot override a later one.
    event = max(events, key=lambda row: (_date(row.get("occurred_at")) or
                                         datetime.min.replace(tzinfo=timezone.utc),
                                         str(row.get("event_id") or "")))
    return {"amount": event["amount"], "confidence": "Orientativa · sentido no publicado",
            "source": "actividad atribuida al manager", "eventId": event["event_id"],
            "date": event["occurred_at"]}


def _premium_summary(values: list[float]) -> dict:
    values = sorted(values)
    count = len(values)
    return {"sample": count, "medianPct": round(median(values), 1) if values else None,
            "meanPct": round(sum(values) / count, 1) if values else None,
            "p25Pct": round(values[int((count - 1) * .25)], 1) if values else None,
            "p75Pct": round(values[int((count - 1) * .75)], 1) if values else None}


def price_tier(value: int | float | None) -> str:
    if value is None:
        return "unknown"
    return "under5m" if value < 5_000_000 else "5to20m" if value < 20_000_000 else "over20m"


def league_premiums(league_id: str, days: int = 90,
                    position_by_id: dict[str, str] | None = None) -> dict:
    now = datetime.now(timezone.utc)
    values = []
    by_position: dict[str, list[float]] = {}
    by_tier: dict[str, list[float]] = {}
    missing = 0
    for event in storage.league_activity(league_id):
        if event["event_type"] not in (1, 31) or not isinstance(event["amount"], int) or event["amount"] <= 0:
            continue
        occurred = _date(event["occurred_at"])
        if occurred is None or (now - occurred).days > days or occurred > now:
            continue
        value = storage.league_value_at(league_id, event["player_id"], event["occurred_at"])
        if not value:
            missing += 1
            continue
        premium = (event["amount"] / value - 1) * 100
        # The feed does not tell us whether an operation was a purchase or a
        # sale. For a bid premium, retain only amounts at or above the dated
        # market value; cheaper transfers are not evidence of an overbid.
        if premium < 0:
            continue
        values.append(premium)
        position = (position_by_id or {}).get(event["player_id"])
        if position:
            by_position.setdefault(position, []).append(premium)
        by_tier.setdefault(price_tier(value), []).append(premium)
    return {**_premium_summary(values), "withoutDatedValue": missing,
            "byPosition": {key: _premium_summary(rows) for key, rows in by_position.items()},
            "byTier": {key: _premium_summary(rows) for key, rows in by_tier.items()},
            "caveat": "Importes de actividad comparados con valor LALIGA fechado. No revela pujas privadas ni confirma el sentido de cada operación."}


def bid_guidance(listing: dict, balance: int | None, premiums: dict,
                 missing_position: str | None = None) -> dict:
    value = listing.get("value")
    asking = listing.get("asking")
    if not isinstance(value, (int, float)) or value <= 0:
        return {"recommended": False, "reason": "Valor de mercado no disponible.",
                "floor": None, "ceiling": None, "confidence": "Baja"}
    floor = max(int(value), int(asking or 0))
    trend = listing.get("dailyChange")
    points = listing.get("averagePoints")
    demand = listing.get("bids") or 0
    position_stats = premiums.get("byPosition", {}).get(listing.get("position"), {})
    tier_stats = premiums.get("byTier", {}).get(price_tier(value), {})
    basis = (position_stats if position_stats.get("sample", 0) >= 3 else
             tier_stats if tier_stats.get("sample", 0) >= 3 else premiums)
    uplift = basis.get("medianPct")
    # Never fabricate a league-wide default premium. With no matching history,
    # the recommendation stays at the minimum legal/asking price and says so.
    base_pct = max(0.0, float(uplift)) if uplift is not None else 0.0
    quality_bonus = (2.0 if isinstance(points, (int, float)) and points >= 5 else 0.0)
    probability = listing.get("probability")
    if isinstance(probability, (int, float)) and probability >= 75:
        quality_bonus += 1.5
    trend_bonus = 2.0 if isinstance(trend, (int, float)) and trend > 0 else (
        -3.0 if isinstance(trend, (int, float)) and trend < 0 else 0.0)
    target_pct = min(100.0, max(0.0, base_pct + quality_bonus + trend_bonus))
    observed_upper = basis.get("p75Pct")
    ceiling_pct = (max(base_pct, float(observed_upper)) if observed_upper is not None else base_pct)
    ceiling_pct = min(100.0, max(0.0, ceiling_pct + quality_bonus + trend_bonus))
    target = max(floor, int(value * (1 + target_pct / 100)))
    market_ceiling = int(value * (1 + ceiling_pct / 100))
    ceiling = max(floor, market_ceiling) if floor <= market_ceiling else market_ceiling
    feasible = floor <= market_ceiling
    strong = ((trend is not None and trend > 0 and points is not None and points >= 2)
              or (points is not None and points >= 5 and (trend is None or trend >= 0)))
    completes = missing_position == listing.get("position")
    healthy_missing = bool(completes and points is not None and points >= 2
                           and trend is not None and trend >= 0)
    recommended = bool(feasible and (healthy_missing or strong) and (not demand or demand < 8))
    daily = int(trend) if isinstance(trend, (int, float)) else None
    premium = max(0, floor - int(value))
    recovery_days = (ceil(premium / daily) if premium and daily and daily > 0 else
                     0 if premium == 0 else None)
    reason = ("Completa una posición necesaria para llegar a 11 con precio estable o al alza." if healthy_missing else
              "Valor al alza o buen promedio de puntos." if strong else
              "Falta una señal deportiva o de crecimiento suficiente.")
    if not feasible:
        reason = ("El precio de salida supera el valor actual y no hay historial comparable suficiente para justificarlo."
                  if uplift is None else
                  "El precio de salida ya supera lo que suele pagar tu liga por este perfil.")
    elif not (healthy_missing or strong):
        reason = "No hay señales suficientes de rendimiento y tendencia para recomendar la compra."
    else:
        if uplift is None:
            reason = (f"Sin operaciones comparables con valor LALIGA fechado; objetivo conservador "
                      f"{target:,} € (precio mínimo), sin sobreprecio inventado.")
        else:
            reason = (f"Objetivo {target:,} € · techo {ceiling:,} €; mediana observada "
                      f"+{base_pct:.1f}% en {basis.get('sample', 0)} operaciones ({'por posición' if basis is position_stats else 'por tramo de precio' if basis is tier_stats else 'liga'}).")
            if quality_bonus + trend_bonus:
                reason += f" Ajuste por calidad/tendencia {quality_bonus + trend_bonus:+.1f}%."
        reason = reason.replace(",", ".")
    return {"recommended": recommended, "floor": floor, "target": target,
            "ceiling": ceiling if feasible else None,
            "confidence": "Alta" if basis.get("sample", 0) >= 15 else "Media" if basis.get("sample", 0) >= 5 else "Baja",
            "reason": reason, "premiumBasisPct": uplift, "historicalSample": basis.get("sample", 0),
            "premiumScope": "posición" if basis is position_stats else "tramo de precio" if basis is tier_stats else "liga",
            "qualityBonusPct": round(quality_bonus + trend_bonus, 1),
            "visibleBids": demand, "premiumAtFloor": premium,
            "recoveryDaysAtCurrentPace": recovery_days if recovery_days is not None and recovery_days <= 30 else None,
            "scenario7dNetAtFloor": int(value) + 7 * daily - floor if daily is not None else None}
