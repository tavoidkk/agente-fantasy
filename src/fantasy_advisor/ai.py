"""Optional OpenRouter analysis with local-only credential storage."""

from __future__ import annotations

import json
import math
import os
import re
import urllib.error
import urllib.request
from collections import Counter

from . import config, storage

_KEYRING_SERVICE = f"{config.KEYRING_SERVICE}.openrouter"
_KEYRING_USERNAME = "api-key"
_MODEL_SETTING = "openrouter_model"
DEFAULT_MODEL = "openrouter/free"
_MODEL_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._:-]*$")


class AIProviderError(ValueError):
    """Safe, user-facing OpenRouter failure without request or credential data."""


def _keyring():
    from .auth import _keyring as secure_keyring
    return secure_keyring()


def _configured_key() -> str | None:
    env_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if env_key:
        return env_key
    return _keyring().get_password(_KEYRING_SERVICE, _KEYRING_USERNAME)


def settings_status() -> dict:
    try:
        key = _configured_key()
    except Exception:
        key = None
    return {"configured": bool(key), "keySource": "environment" if os.environ.get("OPENROUTER_API_KEY", "").strip()
            else ("credential-manager" if key else None),
            "model": os.environ.get("OPENROUTER_MODEL", "").strip()
            or storage.get_setting(_MODEL_SETTING) or DEFAULT_MODEL}


def save_settings(api_key: str | None, model: str, remove_key: bool = False) -> dict:
    model = str(model or DEFAULT_MODEL).strip()
    if len(model) > 120 or not _MODEL_PATTERN.fullmatch(model):
        raise ValueError("El identificador de modelo debe tener formato proveedor/modelo.")
    if api_key:
        api_key = api_key.strip()
        if len(api_key) > 256 or not api_key.startswith("sk-or-"):
            raise ValueError("La clave debe ser una clave válida de OpenRouter (sk-or-…).")
    if api_key or remove_key:
        keyring = _keyring()
        existing = keyring.get_password(_KEYRING_SERVICE, _KEYRING_USERNAME)
        if api_key:
            keyring.set_password(_KEYRING_SERVICE, _KEYRING_USERNAME, api_key)
        elif remove_key and existing is not None:
            keyring.delete_password(_KEYRING_SERVICE, _KEYRING_USERNAME)
    storage.set_setting(_MODEL_SETTING, model)
    return settings_status()


def _schema() -> dict:
    action = {"type": "object", "properties": {
        "kind": {"type": "string", "enum": ["alineación", "fichar", "clausulazo", "vender", "vigilar"]},
        "masterId": {"type": "string"}, "player": {"type": "string"},
        "amount": {"type": ["integer", "null"]}, "reason": {"type": "string"},
        "risk": {"type": "string"}},
        "required": ["kind", "masterId", "player", "amount", "reason", "risk"],
        "additionalProperties": False}
    lineup_choice = {"type": "object", "properties": {
        "masterId": {"type": "string"}, "reason": {"type": "string"}},
        "required": ["masterId", "reason"], "additionalProperties": False}
    schema = {"type": "object", "properties": {
        "summary": {"type": "string"}, "lineup": {"type": "array", "items": lineup_choice},
        "lineupReason": {"type": "string"},
        "actions": {"type": "array", "items": action}},
        "required": ["summary", "lineup", "lineupReason", "actions"],
        "additionalProperties": False}
    return schema


def _provider_message(error: Exception) -> str:
    if isinstance(error, urllib.error.HTTPError):
        if error.code == 401:
            return "OpenRouter rechazó la clave. Comprueba la clave guardada en Configuración IA."
        if error.code == 402:
            return "OpenRouter no tiene crédito disponible para esta solicitud."
        if error.code == 429:
            return "OpenRouter alcanzó el límite temporal. Espera un poco y vuelve a intentarlo."
        if error.code in (400, 404, 422):
            return "El modelo no aceptó el formato de respuesta. Se probó una respuesta JSON compatible; revisa el modelo seleccionado si persiste."
        return f"OpenRouter respondió con un error HTTP {error.code}. Inténtalo de nuevo más tarde."
    if isinstance(error, (TimeoutError, urllib.error.URLError)):
        return "OpenRouter tardó demasiado o no está disponible. Comprueba tu conexión y vuelve a intentarlo."
    return "OpenRouter no pudo completar el análisis. Comprueba el modelo y vuelve a intentarlo."


def _rank_number(value) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _post_completion(api_key: str, model: str, messages: list[dict], timeout: int = 60,
                     schema: dict | None = None, max_tokens: int = 4000) -> dict:
    schema = schema or _schema()
    base = {"model": model, "temperature": 0.15, "max_tokens": max_tokens,
            "messages": messages}
    formats = [
        ({"type": "json_schema", "json_schema": {
            "name": "fantasy_strategy", "strict": True, "schema": schema}}, True),
        ({"type": "json_object"}, False),
        (None, False),
    ]
    last_error = None
    last_output_error = None
    for response_format, require_parameters in formats:
        payload = dict(base)
        if response_format:
            payload["response_format"] = response_format
        if require_parameters:
            payload["provider"] = {"require_parameters": True}
        request = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions",
                                         data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                         headers={"Authorization": f"Bearer {api_key}",
                                                  "Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                try:
                    completion = json.load(response)
                except (json.JSONDecodeError, UnicodeDecodeError) as error:
                    last_output_error = AIProviderError(
                        "OpenRouter devolvió una respuesta incompleta. Vuelve a intentarlo o cambia el modelo.")
                    if response_format:
                        continue
                    raise last_output_error from error
                try:
                    _message_json(completion)
                except AIProviderError as error:
                    last_output_error = error
                    if response_format:
                        continue
                    raise
                return completion
        except urllib.error.HTTPError as error:
            last_error = error
            # Retry only format/parameter failures, never credentials, billing or rate limits.
            if error.code not in (400, 404, 422):
                raise AIProviderError(_provider_message(error)) from error
            detail = error.read(4096).decode("utf-8", "replace").lower()
            if not any(word in detail for word in ("response_format", "json_schema", "structured", "parameter", "no endpoints")):
                raise AIProviderError(_provider_message(error)) from error
        except (TimeoutError, urllib.error.URLError) as error:
            raise AIProviderError(_provider_message(error)) from error
    if last_output_error:
        raise last_output_error
    raise AIProviderError(_provider_message(last_error or RuntimeError("provider error")))


def _message_json(result: dict) -> dict:
    choices = result.get("choices") or []
    message = choices[0].get("message") if choices and isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, list):
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    if isinstance(content, dict):
        return content
    if not isinstance(content, str) or not content.strip():
        finish_reason = choices[0].get("finish_reason") if choices and isinstance(choices[0], dict) else None
        if finish_reason == "length":
            raise AIProviderError("La IA agotó el límite de respuesta antes de dar recomendaciones. Vuelve a intentarlo o selecciona otro modelo en Configuración IA.")
        raise AIProviderError("OpenRouter respondió, pero el modelo no generó recomendaciones. Vuelve a intentarlo o selecciona otro modelo en Configuración IA.")
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as error:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise AIProviderError("El modelo devolvió una respuesta que no es JSON. Prueba otra vez o selecciona otro modelo.") from error
        try:
            parsed = json.loads(text[start:end + 1])
        except json.JSONDecodeError as nested:
            raise AIProviderError("El modelo devolvió un JSON incompleto. Prueba otra vez o selecciona otro modelo.") from nested
    if not isinstance(parsed, dict):
        raise AIProviderError("La respuesta de IA no tiene el formato esperado.")
    return parsed


def test_connection() -> dict:
    key = _configured_key()
    if not key:
        raise ValueError("Guarda primero tu clave de OpenRouter en Configuración.")
    model = settings_status()["model"]
    result = _post_completion(key, model, [
        {"role": "system", "content": "Devuelve un resumen breve de conexión y arrays vacíos para lineup y actions."},
        {"role": "user", "content": "Prueba de conexión. No solicites ningún dato."}], timeout=25)
    _message_json(result)
    return {"ok": True, "model": result.get("model") or model}


def _evidence(dashboard: dict) -> dict:
    report = dashboard.get("report") or {}
    lineup = report.get("lineup") or {}
    own_roster = [{key: row.get(key) for key in ("masterId", "name", "position", "value", "points",
                   "averagePoints", "probability", "available", "dailyChange", "purchase")}
                  for row in (dashboard.get("roster") or []) if row.get("position") != "Entrenador"]
    selected_lineup = [{"masterId": str(row.get("masterId") or row.get("id") or ""),
                        "name": row.get("name"), "position": row.get("position"),
                        "expectedPoints": row.get("expectedPoints"),
                        "probability": row.get("probability")}
                       for row in (lineup.get("players") or [])]
    market_rows = sorted((row for row in (dashboard.get("market") or []) if row.get("masterId")),
                         key=lambda row: (not bool(row.get("recommended")),
                                          -_rank_number(row.get("averagePoints")),
                                          -_rank_number(row.get("dailyChange"))))
    market = [{**{key: row.get(key) for key in ("masterId", "name", "position", "value", "asking",
               "dailyChange", "averagePoints", "bids", "owner", "buyoutClause")},
               "bidGuidance": {key: (row.get("bidGuidance") or {}).get(key)
                               for key in ("recommended", "floor", "target", "ceiling")}}
              for row in market_rows[:8]]
    rival_rows = []
    for manager in dashboard.get("rivals") or []:
        players = sorted((player for player in (manager.get("roster") or [])
                          if player.get("position") != "Entrenador"),
                         key=lambda player: (player.get("recommendation") != "Recomendable",
                                             not bool(player.get("rising")),
                                             -_rank_number(player.get("averagePoints")),
                                             -_rank_number(player.get("dailyChange"))))
        rival_rows.extend({"manager": manager.get("name"), "rank": manager.get("position"),
               "teamValue": manager.get("teamValue"), "teamDailyChange": manager.get("dailyValueChange"),
               "player": {key: player.get(key) for key in ("masterId", "name", "position", "value", "dailyChange",
                    "averagePoints", "probability", "buyoutClause", "clauseLockedUntil", "shielded",
                    "premiumPct", "recommendation", "purchase")}}
               for player in players[:2])
    rivals = sorted(rival_rows, key=lambda row: (
        row["player"].get("recommendation") != "Recomendable",
        -_rank_number(row["player"].get("dailyChange")),
        -_rank_number(row["player"].get("averagePoints"))))[:8]
    candidates = sorted((row for row in (dashboard.get("clauseRadar") or []) if row.get("masterId")),
                        key=lambda row: (row.get("recommendation") != "Recomendable",
                                         not bool(row.get("clauseActionable")),
                                         not bool(row.get("rising")),
                                         -_rank_number(row.get("averagePoints")),
                                         -_rank_number(row.get("dailyChange"))))
    candidates = [{key: row.get(key) for key in ("masterId", "name", "position", "value", "buyoutClause",
                   "dailyChange", "averagePoints", "probability", "recommendation", "recommendationReason",
                   "clauseActionable", "premiumPct")}
                  for row in candidates[:6]]
    sales_rows = sorted((row for row in (dashboard.get("sellRecommendations") or []) if row.get("masterId")),
                        key=lambda row: (-(len(row.get("signals") or [])),
                                         _rank_number(row.get("dailyChange")),
                                         _rank_number(row.get("probability"))))
    sales = [{key: row.get(key) for key in ("masterId", "name", "position", "value", "dailyChange",
                "averagePoints", "probability", "purchase", "signals", "recommendation")}
             for row in sales_rows[:4]]
    return {"objective": "Aumentar patrimonio (saldo + valor de plantilla) y disponer de 11 jugadores que puntúen",
            "balance": dashboard.get("balance"), "teamValue": dashboard.get("teamValue"),
            "netWorth": dashboard.get("netWorth"), "leagueOverpriceSample": dashboard.get("leaguePremiums"),
            "roster": own_roster, "deterministicXI": {"complete": lineup.get("complete"),
                "missing": lineup.get("missing"), "formation": lineup.get("formation"), "players": selected_lineup},
            "market": market, "rivals": rivals, "clauseCandidates": candidates, "sellCandidates": sales}


def _validated_lineup(proposed: list, roster: list[dict], fallback: list[dict]) -> tuple[list[dict], bool]:
    by_id = {str(row.get("masterId")): row for row in roster if row.get("masterId")}
    if not isinstance(proposed, list) or len(proposed) != len(fallback) or not fallback:
        return fallback, False
    selected = []
    reasons = {}
    for item in proposed:
        if not isinstance(item, (dict, str)):
            return fallback, False
        player_id = str(item.get("masterId") or "") if isinstance(item, dict) else item
        if player_id not in by_id or player_id in reasons:
            return fallback, False
        reasons[player_id] = str(item.get("reason") or "")[:220] if isinstance(item, dict) else ""
        selected.append(by_id[player_id])
    if Counter(row.get("position") for row in selected) != Counter(row.get("position") for row in fallback):
        return fallback, False
    return [{**row, "aiReason": reasons.get(str(row.get("masterId")), "")} for row in selected], True


def _validated_actions(proposed: list, dashboard: dict) -> list[dict]:
    roster = {str(row.get("masterId")): row for row in dashboard.get("roster", [])}
    market = {str(row.get("masterId")): row for row in dashboard.get("market", [])}
    rivals = {str(row.get("masterId")): row for row in dashboard.get("clauseRadar", [])}
    sell_candidates = {str(row.get("masterId")): row for row in dashboard.get("sellRecommendations", [])}
    accepted = []
    for action in proposed if isinstance(proposed, list) else []:
        if not isinstance(action, dict):
            continue
        kind, player_id = action.get("kind"), str(action.get("masterId") or "")
        target = (market.get(player_id) if kind == "fichar" else
                  rivals.get(player_id) if kind == "clausulazo" else
                  (rivals.get(player_id) or market.get(player_id)) if kind == "vigilar" else
                  sell_candidates.get(player_id) if kind == "vender" else
                  roster.get(player_id))
        if not target or target.get("name") != action.get("player"):
            continue
        amount = action.get("amount")
        if kind == "fichar":
            guidance = target.get("bidGuidance") or {}
            floor, ceiling = guidance.get("floor"), guidance.get("ceiling")
            if not guidance.get("recommended") or not isinstance(amount, int) or floor is None or ceiling is None or not floor <= amount <= ceiling:
                continue
        elif kind == "clausulazo":
            if not target.get("clauseActionable") or amount != target.get("buyoutClause"):
                continue
        elif kind == "vender":
            amount = None
        accepted.append({"kind": kind, "masterId": player_id, "player": action["player"], "amount": amount,
                         "reason": str(action.get("reason") or "")[:300],
                         "risk": str(action.get("risk") or "")[:180]})
    return accepted[:8]


def explain_dashboard(dashboard: dict) -> dict:
    key = _configured_key()
    if not key:
        raise ValueError("Configura una clave de OpenRouter en la página Configuración.")
    model = os.environ.get("OPENROUTER_MODEL", "").strip() or storage.get_setting(_MODEL_SETTING) or DEFAULT_MODEL
    evidence = _evidence(dashboard)
    fallback = evidence["deterministicXI"]["players"]
    lineup_schema = {"type": "object", "properties": {
        "summary": {"type": "string"}, "lineupReason": {"type": "string"},
        "lineup": {"type": "array", "items": {"type": "string"}}},
        "required": ["summary", "lineupReason", "lineup"], "additionalProperties": False}
    action_schema = {"type": "object", "properties": {"actions": _schema()["properties"]["actions"]},
                     "required": ["actions"], "additionalProperties": False}
    lineup_evidence = {key: evidence[key] for key in ("objective", "roster", "deterministicXI")}
    action_evidence = {key: evidence[key] for key in ("objective", "leagueOverpriceSample", "market",
                                                    "clauseCandidates", "sellCandidates", "rivals")}
    errors = []
    lineup, accepted = fallback, False
    summary = "Análisis parcial: se conserva el once local hasta validar una propuesta de IA."
    lineup_reason = "Once elegido para completar todas las posiciones disponibles."
    actions = []
    used_model = model
    try:
        result = _post_completion(key, model, [
            {"role": "system", "content": "Analiza LaLiga Fantasy en español. Primero completa 11 titulares. Elige únicamente IDs del roster; conserva el reparto de posiciones y cantidad del XI determinista. lineup debe ser una lista de IDs, sin objetos. Devuelve solo JSON con summary, lineupReason y lineup."},
            {"role": "user", "content": json.dumps(lineup_evidence, ensure_ascii=False)}],
            schema=lineup_schema, max_tokens=3000)
        parsed = _message_json(result)
        lineup, accepted = _validated_lineup(parsed.get("lineup"), evidence["roster"], fallback)
        summary = str(parsed.get("summary") or summary)[:400]
        lineup_reason = str(parsed.get("lineupReason") or lineup_reason)[:250]
        used_model = result.get("model") or model
        if not accepted:
            errors.append("La IA propuso un once inválido; se usa el once local.")
    except (AIProviderError, ValueError) as error:
        errors.append(f"Once: {error}")
    try:
        result = _post_completion(key, model, [
            {"role": "system", "content": "Recomienda hasta 4 acciones para aumentar el valor neto del equipo. Usa solo estos IDs y precios. Responde JSON con actions. Cada acción lleva kind (fichar, clausulazo, vender o vigilar), masterId, player, amount, reason y risk. Para fichar usa el rango bidGuidance; clausulazo solo si es accionable y por importe exacto. No inventes pujas ajenas."},
            {"role": "user", "content": json.dumps(action_evidence, ensure_ascii=False)}],
            schema=action_schema, max_tokens=1300)
        actions = _validated_actions(_message_json(result).get("actions"), dashboard)
        used_model = result.get("model") or used_model
        if not actions:
            errors.append("No se validó ninguna acción de IA con los datos actuales.")
    except (AIProviderError, ValueError) as error:
        errors.append(f"Fichajes y ventas: {error}")
    if not accepted and not actions:
        raise AIProviderError("OpenRouter no produjo recomendaciones verificables. " + " ".join(errors))
    return {"summary": summary, "lineup": lineup,
            "lineupReason": lineup_reason if accepted else "Se conserva el XI recomendado localmente.",
            "lineupSource": "IA validada" if accepted else "Recomendación local validada",
            "actions": actions, "model": used_model,
            "basis": "Recomendaciones comprobadas con los datos visibles de tu liga.",
            "warnings": errors}
