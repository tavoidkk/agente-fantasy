"""Polite, read-only extraction from public FutbolFantasy pages."""

from __future__ import annotations

import re
import threading
import time
import urllib.error
import urllib.request
import urllib.robotparser
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urlsplit

from .. import config
from ..matching import normalize

USER_AGENT = "LaligaFantasyAdvisor/0.1 (personal read-only fantasy assistant)"
_pace_lock = threading.Lock()
_last_request_at = 0.0
_robots_cache: tuple[float, urllib.robotparser.RobotFileParser] | None = None


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _pace() -> None:
    global _last_request_at
    with _pace_lock:
        delay = config.FF_REQUEST_GAP_SECONDS - (time.monotonic() - _last_request_at)
        if delay > 0:
            time.sleep(delay)
        _last_request_at = time.monotonic()


def _fetch_bytes(url: str, retries: int = 3) -> bytes:
    delay = 2
    for attempt in range(retries + 1):
        _pace()
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "es-ES,es;q=0.9",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=25) as response:
                data = response.read(config.FF_MAX_RESPONSE_BYTES + 1)
                if len(data) > config.FF_MAX_RESPONSE_BYTES:
                    raise RuntimeError("La página supera el tamaño máximo permitido.")
                return data
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < retries:
                retry_after = exc.headers.get("Retry-After", "")
                wait = int(retry_after) if retry_after.isdigit() else delay
                time.sleep(min(wait, 30))
                delay *= 2
                continue
            raise RuntimeError(f"FutbolFantasy respondió HTTP {exc.code}.") from None
        except (TimeoutError, OSError) as exc:
            if attempt < retries:
                time.sleep(delay)
                delay *= 2
                continue
            raise RuntimeError("No se pudo conectar con FutbolFantasy.") from exc
    raise RuntimeError("FutbolFantasy no respondió tras varios intentos.")


def _robots() -> urllib.robotparser.RobotFileParser:
    global _robots_cache
    now = time.monotonic()
    if _robots_cache and now - _robots_cache[0] < 24 * 60 * 60:
        return _robots_cache[1]
    parsed = urlsplit(config.FF_BASE_URL)
    url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    parser = urllib.robotparser.RobotFileParser()
    parser.set_url(url)
    parser.parse(_fetch_bytes(url).decode("utf-8", "replace").splitlines())
    _robots_cache = (now, parser)
    return parser


def _fetch_page(url: str) -> str:
    if urlsplit(url).netloc.lower() not in {"www.futbolfantasy.com", "futbolfantasy.com"}:
        raise ValueError("Solo se permiten páginas del dominio FutbolFantasy.")
    if not _robots().can_fetch(USER_AGENT, url):
        raise RuntimeError("robots.txt no permite consultar esta página.")
    return _fetch_bytes(url).decode("utf-8", "replace")


def _number(value: str | None, cast=int):
    if value is None or value == "":
        return None
    try:
        return cast(value.replace(",", "."))
    except (TypeError, ValueError):
        return None


class _MarketParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.players: list[dict] = []

    def handle_starttag(self, tag: str, attrs):
        if tag.lower() != "tr":
            return
        data = dict(attrs)
        if "elemento_jugador" not in (data.get("class") or "").split():
            return
        fields = {key[5:]: value for key, value in data.items() if key.startswith("data-")}
        if not fields.get("id") or not fields.get("nombre") or not fields.get("valor"):
            return
        player = {
            "id": str(fields["id"]),
            "name": fields["nombre"].strip(),
            "normalized_name": normalize(fields["nombre"]),
            "position": fields.get("posicion"),
            "team_id": fields.get("equipo"),
            "current_value": _number(fields.get("valor")),
            "value_1d": _number(fields.get("valor1")),
            "value_2d": _number(fields.get("valor2")),
            "value_3d": _number(fields.get("valor3")),
            "value_7d": _number(fields.get("valor7")),
            "value_14d": _number(fields.get("valor14")),
            "value_30d": _number(fields.get("valor30")),
            "trend": _number(fields.get("tendencia"), float),
            "acceleration": _number(fields.get("aceleracion"), float),
            "pct_1d": _number(fields.get("diferencia-pct1"), float),
            "pct_2d": _number(fields.get("diferencia-pct2"), float),
            "pct_3d": _number(fields.get("diferencia-pct3"), float),
            "pct_7d": _number(fields.get("diferencia-pct7"), float),
            "pct_14d": _number(fields.get("diferencia-pct14"), float),
            "pct_30d": _number(fields.get("diferencia-pct30"), float),
        }
        self.players.append(player)


def parse_market(html: str) -> list[dict]:
    parser = _MarketParser()
    parser.feed(html)
    unique = {player["id"]: player for player in parser.players}
    if not unique:
        raise RuntimeError("No se reconocieron jugadores; puede haber cambiado el HTML.")
    return list(unique.values())


def fetch_market() -> tuple[list[dict], str]:
    html = _fetch_page(config.FF_MARKET_URL)
    players = parse_market(html)
    return players, utc_now()


def _price_int(value: str) -> int | None:
    text = value.strip()
    digits = re.sub(r"\D", "", text)
    if not digits:
        return None
    amount = int(digits)
    return -amount if text.startswith("-") else amount


class _DailyPriceParser(HTMLParser):
    """Parse the site's public 30-day price rows, without executing its scripts."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows: list[dict] = []
        self.row: dict | None = None
        self.depth = 0
        self.column: str | None = None
        self.column_depth: int | None = None

    def handle_starttag(self, tag: str, attrs):
        if tag.lower() != "div":
            return
        classes = set((dict(attrs).get("class") or "").split())
        if self.row is None and {"row", "data-w30"}.issubset(classes):
            self.row = {"dateText": "", "changeText": "", "valueText": ""}
            self.depth = 1
            return
        if self.row is None:
            return
        self.depth += 1
        if "col-3" in classes:
            self.column = "dateText"
            self.column_depth = self.depth
        elif "col-5" in classes:
            self.column = "changeText"
            self.column_depth = self.depth
        elif "col-4" in classes:
            self.column = "valueText"
            self.column_depth = self.depth

    def handle_data(self, data: str):
        if self.row is not None and self.column:
            self.row[self.column] += data

    def handle_endtag(self, tag: str):
        if tag.lower() != "div" or self.row is None:
            return
        if self.column_depth == self.depth:
            self.column = None
            self.column_depth = None
        if self.depth == 1:
            date_match = re.search(r"(\d{1,2})/(\d{1,2})", self.row["dateText"])
            value = _price_int(self.row["valueText"])
            if date_match and value is not None:
                day, month = map(int, date_match.groups())
                today = datetime.now(timezone.utc).date()
                year = today.year - int((month, day) > (today.month, today.day))
                try:
                    self.rows.append({"date": datetime(year, month, day, tzinfo=timezone.utc).date().isoformat(),
                                      "value": value, "dailyChange": None, "source": "FutbolFantasy"})
                except ValueError:
                    pass
            self.row = None
            self.depth = 0
            self.column = None
            self.column_depth = None
        else:
            self.depth -= 1


def fetch_player_daily_prices(player_id: str) -> tuple[list[dict], str]:
    if not str(player_id).isdigit():
        raise ValueError("Identificador de jugador FutbolFantasy no válido.")
    url = f"{config.FF_BASE_URL.rstrip('/')}/analytics/laliga-fantasy/mercado/detalle/{player_id}?perfil=1"
    html = _fetch_page(url)
    parser = _DailyPriceParser()
    parser.feed(html)
    by_date = {row["date"]: row for row in parser.rows}
    ascending = [by_date[key] for key in sorted(by_date)]
    for index, row in enumerate(ascending):
        if index:
            previous = ascending[index - 1]
            previous_date = datetime.fromisoformat(previous["date"]).date()
            current_date = datetime.fromisoformat(row["date"]).date()
            if (current_date - previous_date).days == 1:
                row["dailyChange"] = row["value"] - previous["value"]
    if not ascending:
        raise RuntimeError("FutbolFantasy no devolvió el historial diario del jugador.")
    return list(reversed(ascending)), utc_now()


class _IndexParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.slugs: set[str] = set()

    def handle_starttag(self, tag: str, attrs):
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href") or ""
        found = re.search(r"/laliga/equipos/([a-z0-9-]+)", href)
        if found:
            self.slugs.add(found.group(1))


class _FixtureParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.fixtures: list[dict] = []
        self.kickoff: str | None = None
        self.current: dict | None = None

    def handle_starttag(self, tag: str, attrs):
        data = dict(attrs)
        if tag == "time" and data.get("itemprop") == "startDate":
            self.kickoff = data.get("content")
        elif tag == "a" and "partido" in (data.get("class") or "").split():
            match = re.search(r"/partidos/(\d+)-", data.get("href") or "")
            if match:
                self.current = {"matchId": match.group(1), "kickoff": self.kickoff,
                                "url": data.get("href")}
        elif tag == "img" and self.current:
            classes = set((data.get("class") or "").split())
            side = "home" if "local" in classes else "away" if "visitante" in classes else None
            if side and "escudo" in classes:
                image = data.get("data-src") or data.get("src") or ""
                if image.startswith("//"):
                    image = "https:" + image
                self.current[side] = data.get("alt") or ""
                self.current[side + "Badge"] = image if image.startswith("https://") else None

    def handle_endtag(self, tag: str):
        if tag == "a" and self.current:
            if self.current.get("home") and self.current.get("away"):
                self.fixtures.append(self.current)
            self.current = None


class _TeamParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.players: list[dict] = []
        self.div_depth = 0
        self.wrapper_depth: int | None = None
        self.wrapper: dict = {}
        self.section_depth: int | None = None
        self.match_id: str | None = None
        self.current_player: dict | None = None

    def handle_starttag(self, tag: str, attrs):
        data = dict(attrs)
        if tag.lower() == "img" and self.current_player is not None:
            image = data.get("data-src") or ""
            if "/jugadores/" in image and image.startswith("https://"):
                self.current_player["image"] = image
            return
        if tag.lower() == "div":
            self.div_depth += 1
            classes = (data.get("class") or "").split()
            match = next((re.fullmatch(r"jugadores-titulares-(\d+)", cls) for cls in classes
                          if cls.startswith("jugadores-titulares-")), None)
            if match:
                self.section_depth = self.div_depth
                self.match_id = match.group(1)
            if "camiseta-wrapper" in classes:
                self.wrapper_depth = self.div_depth
                self.wrapper = data
            return
        if tag.lower() != "a":
            return
        if "camiseta" not in (data.get("class") or "").split():
            return
        href = data.get("href") or ""
        slug_match = re.search(r"/jugadores/([a-z0-9-]+)", href)
        if not slug_match:
            return
        info = {key[5:]: value for key, value in data.items() if key.startswith("data-")}
        slug = slug_match.group(1)
        name = info.get("nombre") or slug.replace("-", " ")
        probability = (info.get("probabilidad") or "").rstrip("%")
        injured = info.get("lesion") not in (None, "-1", "0")
        suspended = info.get("sancionado") == "1"
        unavailable = info.get("nodisponible") == "1"
        player = {
            "slug": slug,
            "name": name,
            "normalized_name": normalize(name),
            "probability": _number(probability),
            "injured": injured,
            "suspended": suspended,
            "available": not (suspended or unavailable or injured),
            "team": info.get("equipo"),
            "starter": self.wrapper.get("data-onceff") == "titular",
            "field_x": self.wrapper.get("data-onceff-x"),
            "field_y": self.wrapper.get("data-onceff-y"),
            "position": self.wrapper.get("data-posicionlaligafantasy") or self.wrapper.get("data-posicion"),
            "market_value": _number(info.get("valor-laliga-fantasy")),
            "daily_change": _number(info.get("valor-diff-laliga-fantasy")),
            "match_id": self.match_id,
            "lineup_order": len(self.players),
        }
        self.players.append(player)
        self.current_player = player

    def handle_endtag(self, tag: str):
        if tag.lower() == "a":
            self.current_player = None
        if tag.lower() == "div":
            if self.wrapper_depth == self.div_depth:
                self.wrapper_depth = None
                self.wrapper = {}
            if self.section_depth == self.div_depth:
                self.section_depth = None
                self.match_id = None
            self.div_depth = max(0, self.div_depth - 1)


def fetch_probable_lineups_and_fixtures() -> tuple[list[dict], list[dict], str]:
    index_html = _fetch_page(config.FF_LINEUPS_INDEX_URL)
    index = _IndexParser()
    index.feed(index_html)
    fixture_parser = _FixtureParser()
    fixture_parser.feed(index_html)
    fixtures = fixture_parser.fixtures[:10]
    if not index.slugs:
        raise RuntimeError("No se reconocieron equipos en las posibles alineaciones.")
    all_players: list[dict] = []
    for slug in sorted(index.slugs):
        page = _fetch_page(config.FF_TEAM_URL.format(slug=slug))
        parser = _TeamParser()
        parser.feed(page)
        for player in parser.players:
            player["club_slug"] = slug
        all_players.extend(parser.players)
    if not all_players:
        raise RuntimeError("No se reconocieron jugadores en las páginas de equipos.")
    # Team pages can repeat the same player anchor (for example in desktop/mobile
    # markup). Collapse those rows before persisting the unique player slug.
    unique: dict[tuple[str, str], dict] = {}
    for player in all_players:
        key = (player["club_slug"], player["slug"])
        current = unique.get(key)
        # Prefer the projected match section and rows with a field position.
        rank = lambda row: (bool(row.get("match_id")), bool(row.get("starter")),
                            bool(row.get("position")), bool(row.get("field_y")), row.get("probability") or 0)
        if current is None or rank(player) > rank(current):
            unique[key] = player
    return list(unique.values()), fixtures, utc_now()


def fetch_probable_lineups() -> tuple[list[dict], str]:
    players, _fixtures, fetched_at = fetch_probable_lineups_and_fixtures()
    return players, fetched_at
