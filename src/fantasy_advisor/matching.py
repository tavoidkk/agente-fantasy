"""Conservative cross-source player-name matching."""

import re
import unicodedata


# LALIGA Fantasy often shortens the display name; FutbolFantasy uses the full
# player name. Keep these explicit aliases narrow so ambiguous surnames do not
# turn into accidental matches.
_PLAYER_NAME_ALIASES = {
    "gimenez": "jose maria gimenez",
    "jose maria gimenez de vargas": "jose maria gimenez",
    "de las sias": "marco de la sias",
    "de la sias": "marco de la sias",
    "marco de las sias": "marco de la sias",
    "marco de la sias": "marco de la sias",
}


def normalize(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def canonical_player_name(value: str | None) -> str:
    key = normalize(value)
    return _PLAYER_NAME_ALIASES.get(key, key)


def find_match(name: str, players: list[dict]) -> dict | None:
    key = canonical_player_name(name)
    exact = [p for p in players
             if canonical_player_name(p.get("normalized_name") or p.get("name")) == key]
    if len(exact) == 1:
        return exact[0]
    if exact:
        return None
    tokens = key.split()
    if not tokens:
        return None
    candidates = []
    for player in players:
        other = canonical_player_name(player.get("normalized_name") or player.get("name")).split()
        if len(tokens) == len(other) and all(
            ((left.endswith(".") and len(left) == 2) or len(left) == 1)
            and right.startswith(left[0])
            or left == right
            for left, right in zip(tokens, other)
        ):
            candidates.append(player)
    if len(candidates) == 1:
        return candidates[0]
    # Ambiguous or merely similar names remain unmatched so that unrelated
    # players never inherit each other's external prices or lineup data.
    return None
