import unittest
import tempfile
import io
import json
from pathlib import Path
from unittest.mock import patch

from fantasy_advisor.advisor import suggest_lineup
from fantasy_advisor.decisions import bid_guidance, manager_purchase_cost, purchase_cost
from fantasy_advisor.ai import explain_dashboard
from fantasy_advisor.web import _price_days
from fantasy_advisor import storage
from fantasy_advisor.matching import find_match, normalize


def player(player_id, position, probability=80, average=5):
    return {"id": str(player_id), "playerMaster": {
        "id": player_id, "name": f"Jugador {player_id}", "positionId": position,
        "averagePoints": average, "playerStatus": "ok"}}


class DecisionTests(unittest.TestCase):
    def test_complete_lineup_comes_before_higher_probability(self):
        players = ([player(1, 1)] + [player(i, 2) for i in range(2, 7)]
                   + [player(i, 3) for i in range(7, 11)]
                   + [player(i, 4) for i in range(11, 13)])
        result = suggest_lineup(players, [])
        self.assertTrue(result["complete"])
        self.assertEqual(len(result["players"]), 11)

    def test_missing_position_is_reported(self):
        players = ([player(1, 1)] + [player(i, 2) for i in range(2, 7)]
                   + [player(i, 3) for i in range(7, 10)] + [player(10, 4)])
        result = suggest_lineup(players, [])
        self.assertFalse(result["complete"])
        self.assertEqual(len(result["players"]), 10)
        self.assertEqual(sum(result["missing"].values()), 1)

    def test_own_purchase_cost_precedes_unattributed_event(self):
        events = [
            {"player_id": "8", "event_type": 1, "amount": 20000000,
             "user1_id": "other", "user2_id": "", "event_id": "new", "occurred_at": "2026-09-30"},
            {"player_id": "8", "event_type": 31, "amount": 15000000,
             "user1_id": "mine", "user2_id": "", "event_id": "old", "occurred_at": "2026-09-29"},
        ]
        with patch("fantasy_advisor.decisions.storage.purchase_cost_override", return_value=None), patch(
            "fantasy_advisor.decisions.storage.league_activity", return_value=events
        ):
            result = purchase_cost("league", "8", {"mine"})
        self.assertEqual(result["amount"], 15000000)
        self.assertEqual(result["confidence"], "Inferida")

    def test_rival_purchase_cost_requires_an_event_attributed_to_that_manager(self):
        events = [
            {"player_id": "8", "event_type": 1, "amount": 20000000,
             "user1_id": "other", "user2_id": "third", "event_id": "other-transfer",
             "occurred_at": "2026-09-30"},
            {"player_id": "8", "event_type": 31, "amount": 15000000,
             "user1_id": "mine", "user2_id": "rival", "event_id": "rival-transfer",
             "occurred_at": "2026-09-29"},
        ]
        with patch("fantasy_advisor.decisions.storage.league_activity", return_value=events):
            result = manager_purchase_cost("league", "8", "rival")
            missing = manager_purchase_cost("league", "8", "another-rival")
        self.assertEqual(result["amount"], 15000000)
        self.assertEqual(result["confidence"], "Orientativa · sentido no publicado")
        self.assertIsNone(missing["amount"])

    def test_rival_purchase_cost_matches_team_or_manager_activity_ids(self):
        events = [
            {"player_id": "8", "event_type": 1, "amount": 66000000,
             "user1_id": "rival-manager", "user2_id": "", "event_id": "manager-event",
             "occurred_at": "2026-10-02T10:00:00Z"},
            {"player_id": "8", "event_type": 31, "amount": 70000000,
             "user1_id": "rival-team", "user2_id": "", "event_id": "team-event",
             "occurred_at": "2026-10-04T10:00:00Z"},
            {"player_id": "8", "event_type": 1, "amount": 80000000,
             "user1_id": "another-manager", "user2_id": "", "event_id": "other-event",
             "occurred_at": "2026-10-05T10:00:00Z"},
        ]
        with patch("fantasy_advisor.decisions.storage.league_activity", return_value=events):
            result = manager_purchase_cost("league", "8", {"rival-manager", "rival-team"})
        self.assertEqual(result["amount"], 70000000)
        self.assertEqual(result["eventId"], "team-event")

    def test_manager_activity_ids_include_team_and_manager_aliases(self):
        from fantasy_advisor.web import _manager_activity_ids
        team = {"id": 700, "managerId": "manager-7", "manager": {"id": 7}}
        self.assertEqual(_manager_activity_ids(team), {"700", "manager-7", "7"})

    def test_rival_purchase_cost_never_matches_blank_activity_ids(self):
        events = [{"player_id": "8", "event_type": 1, "amount": 9000000,
                   "user1_id": "", "user2_id": "", "event_id": "unattributed",
                   "occurred_at": "2026-10-04T10:00:00Z"}]
        with patch("fantasy_advisor.decisions.storage.league_activity", return_value=events):
            result = manager_purchase_cost("league", "8", "")
        self.assertIsNone(result["amount"])

    def test_bid_floor_respects_game_market_value(self):
        listing = {"listingType": "marketPlayerLeague", "value": 10000000,
                   "asking": 9000000, "position": "Medio", "dailyChange": 100000}
        guidance = bid_guidance(listing, 12000000, {"sample": 0}, "Medio")
        self.assertEqual(guidance["floor"], 10000000)
        self.assertGreaterEqual(guidance["ceiling"], guidance["floor"])

    def test_falling_player_is_not_marked_as_growth_recommendation(self):
        listing = {"listingType": "marketPlayerLeague", "value": 12000000,
                   "asking": 12000000, "position": "Delantero", "dailyChange": -300000,
                   "averagePoints": 4}
        guidance = bid_guidance(listing, 14000000, {"sample": 0}, "Delantero")
        self.assertFalse(guidance["recommended"])

    def test_bid_uses_position_when_sample_is_sufficient(self):
        listing = {"listingType": "marketPlayerLeague", "value": 10000000,
                   "asking": 10000000, "position": "Medio", "dailyChange": 100000,
                   "averagePoints": 5}
        premiums = {"sample": 20, "medianPct": 10,
                    "byPosition": {"Medio": {"sample": 6, "medianPct": 4}},
                    "byTier": {"5to20m": {"sample": 8, "medianPct": 7}}}
        guidance = bid_guidance(listing, 12000000, premiums)
        self.assertEqual(guidance["premiumScope"], "posición")
        self.assertEqual(guidance["target"], 10400000)
        self.assertEqual(guidance["ceiling"], 10800000)

    def test_bid_target_does_not_shrink_to_fit_available_balance(self):
        listing = {"listingType": "marketPlayerLeague", "value": 10000000,
                   "asking": 10000000, "position": "Medio", "dailyChange": 250000,
                   "averagePoints": 6, "probability": 80}
        premiums = {"sample": 10, "medianPct": 8,
                    "byPosition": {"Medio": {"sample": 6, "medianPct": 8}}}
        guidance = bid_guidance(listing, 1000000, premiums)
        self.assertEqual(guidance["target"], 10800000)
        self.assertEqual(guidance["ceiling"], 11350000)

    def test_ai_retries_when_model_rejects_structured_format(self):
        from fantasy_advisor.ai import _post_completion
        rejection = __import__("urllib.error", fromlist=["HTTPError"]).HTTPError(
            "https://openrouter.ai/api/v1/chat/completions", 400, "unsupported format", {},
            io.BytesIO(b"unsupported response_format json_schema"))
        successful = io.BytesIO(json.dumps({"choices": [{"message": {"content": "{}"}}]}).encode())
        with patch("fantasy_advisor.ai.urllib.request.urlopen", side_effect=[rejection, successful]) as request:
            _post_completion("test-key", "openrouter/free", [{"role": "user", "content": "test"}])
        self.assertEqual(request.call_count, 2)
        first = json.loads(request.call_args_list[0].args[0].data)
        second = json.loads(request.call_args_list[1].args[0].data)
        self.assertEqual(first["response_format"]["type"], "json_schema")
        self.assertEqual(second["response_format"]["type"], "json_object")
        self.assertNotIn("provider", second)

    def test_ai_retries_when_model_returns_empty_structured_response(self):
        from fantasy_advisor.ai import _post_completion
        empty = io.BytesIO(json.dumps({"choices": [{"message": {"content": None}}]}).encode())
        valid = io.BytesIO(json.dumps({"choices": [{"message": {"content": "{}"}}]}).encode())
        with patch("fantasy_advisor.ai.urllib.request.urlopen", side_effect=[empty, valid]) as request:
            _post_completion("test-key", "openrouter/free", [{"role": "user", "content": "test"}])
        self.assertEqual(request.call_count, 2)
        self.assertEqual(json.loads(request.call_args_list[1].args[0].data)["response_format"]["type"], "json_object")

    def test_ai_limits_context_and_sorts_with_missing_numeric_values(self):
        from fantasy_advisor.ai import _evidence
        market = [{"masterId": str(i), "name": f"Mercado {i}",
                   "averagePoints": "n/d" if i % 2 else i, "dailyChange": i}
                  for i in range(50)]
        rivals = [{"name": f"Manager {manager}", "roster": [{
            "masterId": f"{manager}-{player_id}", "name": "Rival",
            "position": "Delantero", "averagePoints": player_id,
            "dailyChange": player_id, "recommendation": "Recomendable", "rising": True
        } for player_id in range(10)]} for manager in range(20)]
        dashboard = {"market": market, "rivals": rivals,
                     "clauseRadar": [{"masterId": str(i), "recommendation": "Recomendable"}
                                     for i in range(30)],
                     "sellRecommendations": [{"masterId": str(i), "signals": []}
                                             for i in range(30)]}
        evidence = _evidence(dashboard)
        self.assertEqual(len(evidence["market"]), 8)
        self.assertEqual(len(evidence["rivals"]), 8)
        self.assertEqual(len(evidence["clauseCandidates"]), 6)
        self.assertEqual(len(evidence["sellCandidates"]), 4)

    def test_ai_requires_local_key(self):
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": ""}):
            with patch("fantasy_advisor.ai._keyring", return_value=type("Vault", (), {
                "get_password": lambda *_: None})()):
                with self.assertRaisesRegex(ValueError, "Configuración"):
                    explain_dashboard({})

    def test_short_display_names_match_their_full_futbolfantasy_records(self):
        ff_players = [
            {"name": "carlos gimenez", "normalized_name": "carlos gimenez"},
            {"name": "jose maria gimenez", "normalized_name": "jose maria gimenez"},
            {"name": "marco de la sias", "normalized_name": "marco de la sias"},
        ]
        self.assertEqual(find_match("Giménez", ff_players)["name"], "jose maria gimenez")
        self.assertEqual(find_match("De Las Sías", ff_players)["name"], "marco de la sias")

    def test_ai_settings_default_to_openrouter_free_and_store_key_outside_sqlite(self):
        class Vault:
            def __init__(self): self.values = {}
            def get_password(self, service, username): return self.values.get((service, username))
            def set_password(self, service, username, value): self.values[(service, username)] = value
            def delete_password(self, service, username): self.values.pop((service, username), None)
        vault = Vault()
        settings = {}
        with patch("fantasy_advisor.ai._keyring", return_value=vault), \
             patch("fantasy_advisor.ai.storage.set_setting", side_effect=lambda key, value: settings.__setitem__(key, value)), \
             patch("fantasy_advisor.ai.storage.get_setting", side_effect=lambda key: settings.get(key)):
            self.assertEqual(__import__("fantasy_advisor.ai", fromlist=["settings_status"]).settings_status()["model"], "openrouter/free")
            saved = __import__("fantasy_advisor.ai", fromlist=["save_settings"]).save_settings("sk-or-v1-test", "openrouter/free")
            self.assertTrue(saved["configured"])
            self.assertNotIn("sk-or-v1-test", json.dumps(settings))
            self.assertEqual(next(iter(vault.values.values())), "sk-or-v1-test")

    def test_ai_validates_lineup_and_market_amounts_before_showing_them(self):
        from fantasy_advisor.ai import _validated_lineup, _validated_actions
        roster = [{"masterId": "1", "name": "Portero", "position": "Portero"},
                  {"masterId": "2", "name": "Defensa", "position": "Defensa"}]
        fallback = list(roster)
        proposed = [{"masterId": "2", "reason": "Mejor puntuación"}, {"masterId": "1", "reason": "Titular"}]
        lineup, accepted = _validated_lineup(proposed, roster, fallback)
        self.assertTrue(accepted)
        self.assertEqual({row["masterId"] for row in lineup}, {"1", "2"})
        dashboard = {"roster": [], "clauseRadar": [], "market": [{"masterId": "3", "name": "Mercado",
            "bidGuidance": {"recommended": True, "floor": 100, "ceiling": 120}}]}
        action = {"kind": "fichar", "masterId": "3", "player": "Mercado", "amount": 130,
                  "reason": "", "risk": ""}
        self.assertEqual(_validated_actions([action], dashboard), [])

    def test_ai_uses_free_router_by_default_and_passes_recommendation_context(self):
        response = io.BytesIO(json.dumps({"model": "openrouter/free/test:free", "choices": [{"message": {"content": json.dumps({
            "summary": "XI listo", "lineup": [{"masterId": "1", "reason": "Más puntos"}],
            "lineupReason": "Prioriza puntos", "actions": []})}}]}).encode())
        dashboard = {"balance": 100, "teamValue": 1000, "netWorth": 1100,
            "leaguePremiums": {"sample": 6, "medianPct": 7},
            "report": {"lineup": {"complete": True, "formation": (1, 0, 0), "players": [
                {"masterId": "1", "id": "1", "name": "Portero", "position": "Portero"}]}},
            "roster": [{"masterId": "1", "name": "Portero", "position": "Portero"}],
            "market": [], "rivals": [], "clauseRadar": [], "sellRecommendations": []}
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key", "OPENROUTER_MODEL": ""}), \
             patch("fantasy_advisor.ai.storage.get_setting", return_value=None), \
             patch("fantasy_advisor.ai.urllib.request.urlopen", return_value=response) as request:
            result = explain_dashboard(dashboard)
        sent = json.loads(request.call_args.args[0].data)
        self.assertEqual(sent["model"], "openrouter/free")
        self.assertIn("rivals", sent["messages"][1]["content"])
        self.assertEqual(result["lineup"][0]["name"], "Portero")

    def test_ai_uses_structured_response_without_game_token(self):
        responses = [io.BytesIO(json.dumps({"choices": [{"message": {"content": json.dumps(content)}}]}).encode())
                     for content in ({"summary": "Completa el XI", "lineup": [], "lineupReason": ""},
                                     {"actions": []})]
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}), patch(
            "fantasy_advisor.ai.urllib.request.urlopen", side_effect=responses
        ) as request:
            with self.assertRaisesRegex(ValueError, "no produjo recomendaciones verificables"):
                explain_dashboard({"balance": 1000000, "market": [], "roster": []})
        sent = json.loads(request.call_args.args[0].data)
        self.assertEqual(sent["response_format"]["type"], "json_schema")
        self.assertNotIn("oauth", json.dumps(sent).lower())

    def test_zero_placeholder_is_not_a_historical_low(self):
        from datetime import datetime, timedelta, timezone
        today = datetime.now(timezone.utc).date().isoformat()
        yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
        _, stats = _price_days([], [], [{"date": yesterday, "value": 0},
                                       {"date": today, "value": 4000000}])
        self.assertEqual(stats["low"], 4000000)

    def test_activity_purchase_and_manual_correction_in_local_database(self):
        with tempfile.TemporaryDirectory() as directory, patch(
            "fantasy_advisor.storage.database_path", return_value=Path(directory) / "advisor.sqlite3"
        ):
            storage.save_league_observation("league", [], [], [{
                "id": "event-1", "playerMasterId": 8, "activityTypeId": 31,
                "amount": 15000000, "createdAt": "2026-09-29T12:00:00Z", "user1Id": "mine"
            }], "2026-09-30T12:00:00Z")
            inferred = purchase_cost("league", "8", {"mine"})
            self.assertEqual(inferred["amount"], 15000000)
            storage.set_purchase_cost_override("league", "8", 14700000)
            corrected = purchase_cost("league", "8", {"mine"})
            self.assertEqual(corrected["amount"], 14700000)
            self.assertEqual(corrected["confidence"], "Confirmada por ti")


if __name__ == "__main__":
    unittest.main()
