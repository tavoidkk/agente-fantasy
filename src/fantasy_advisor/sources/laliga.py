"""Read-only client for the unofficial LALIGA Fantasy API used by the reference app."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from .. import config
from ..auth import load_tokens, refresh_tokens


class LaligaError(RuntimeError):
    pass


def _jwt_exp(token: str) -> int | None:
    try:
        import base64
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("exp")
    except Exception:
        return None


class LaligaClient:
    """Exposes GET-only methods; no operation in this client can alter the game."""

    def __init__(self):
        self.tokens = load_tokens()

    def _bearer(self) -> str:
        token = self.tokens.get("access_token") or self.tokens.get("id_token")
        if not token:
            raise LaligaError("La sesión no contiene un token de acceso.")
        return token

    def _refresh_if_needed(self) -> None:
        expiry = _jwt_exp(self._bearer())
        if expiry is not None and time.time() >= expiry - 120:
            self.tokens = refresh_tokens(self.tokens)

    def get(self, path: str):
        if not path.startswith("/") or ".." in path:
            raise ValueError("Ruta de API inválida.")
        self._refresh_if_needed()
        return self._request(path, retry_auth=True)

    def _request(self, path: str, retry_auth: bool):
        url = f"{config.LALIGA_API_BASE}{path}"
        request = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {self._bearer()}",
            "Accept": "application/json",
            "x-lang": config.LANGUAGE,
            "User-Agent": "LaligaFantasyAdvisor/0.1 (read-only)",
        }, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                body = response.read()
                return json.loads(body.decode("utf-8")) if body else None
        except urllib.error.HTTPError as exc:
            if exc.code == 401 and retry_auth:
                self.tokens = refresh_tokens(self.tokens)
                return self._request(path, retry_auth=False)
            if exc.code == 429:
                raise LaligaError("LALIGA limitó temporalmente las consultas; espera antes de reintentar.") from None
            raise LaligaError(f"La consulta a LALIGA falló (HTTP {exc.code}).") from None
        except (TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise LaligaError("No se pudieron leer los datos de LALIGA.") from exc

    @staticmethod
    def _competition(path: str) -> str:
        return f"/v1/competition/{config.COMPETITION_ID}{path}"

    def me(self):
        return self.get("/v4/user/me?x-lang=es")

    def leagues(self) -> list[dict]:
        result = self.get(self._competition("/leagues?x-lang=es"))
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            for key in ("leagues", "items", "data"):
                if isinstance(result.get(key), list):
                    return result[key]
        raise LaligaError("LALIGA devolvió un formato de ligas desconocido.")

    def team(self, league_id: str, team_id: str) -> dict:
        return self.get(self._competition(
            f"/leagues/{urllib.parse.quote(str(league_id), safe='')}/teams/"
            f"{urllib.parse.quote(str(team_id), safe='')}?x-lang=es"
        ))

    def lineup(self, team_id: str):
        return self.get(self._competition(
            f"/teams/{urllib.parse.quote(str(team_id), safe='')}/lineup?x-lang=es"
        ))

    def market(self, league_id: str):
        return self.get(self._competition(
            f"/league/{urllib.parse.quote(str(league_id), safe='')}/market?x-lang=es"
        ))

    def league_teams(self, league_id: str):
        return self.get(self._competition(
            f"/leagues/{urllib.parse.quote(str(league_id), safe='')}/teams?x-lang=es"
        ))

    def activity(self, league_id: str, page: int = 0):
        page = max(0, min(int(page), 100))
        return self.get(self._competition(
            f"/leagues/{urllib.parse.quote(str(league_id), safe='')}/activity/{page}?x-lang=es"
        ))

    def all_players(self):
        return self.get(self._competition("/players?x-lang=es"))


def league_id_and_team_id(leagues: list[dict], selected_id: str | None = None) -> tuple[str, str]:
    if selected_id:
        matches = [league for league in leagues if str(league.get("id")) == str(selected_id)]
        if not matches:
            raise LaligaError("La liga guardada ya no aparece en la cuenta; vuelve a conectarla.")
        league = matches[0]
    else:
        if not leagues:
            raise LaligaError("La cuenta no tiene ligas de LALIGA Fantasy.")
        league = leagues[0]
    team = league.get("team") or {}
    if not league.get("id") or not team.get("id"):
        raise LaligaError("La respuesta no incluye el ID de la liga y del equipo propio.")
    return str(league["id"]), str(team["id"])
