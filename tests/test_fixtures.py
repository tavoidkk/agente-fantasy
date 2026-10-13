import unittest

from fantasy_advisor.web import _fixture_data


class FixtureTests(unittest.TestCase):
    def test_extra_starter_does_not_displace_goalkeeper(self):
        fixtures = [{"match_id": "42", "kickoff": None, "home": "Espanyol", "away": "Málaga",
                     "home_badge": None, "away_badge": None}]
        players = [{"name": f"Jugador {index}", "club_slug": "espanyol",
                    "player_slug": f"jugador-{index}", "starter": 1,
                    "field_x": f"{15 + index * 6}%", "field_y": "45%",
                    "lineup_order": index, "match_id": "42", "available": True}
                   for index in range(11)]
        players.append({"name": "Marko Dmitrović", "club_slug": "espanyol",
                        "player_slug": "marko-dmitrovic", "starter": 1,
                        "field_x": "50%", "field_y": "87%", "position": None,
                        "lineup_order": 26, "match_id": None, "available": True})
        result = _fixture_data(fixtures, players, [])[0]
        self.assertTrue(result["homeComplete"])
        self.assertEqual(len(result["homeXI"]), 11)
        self.assertEqual([p["name"] for p in result["homeXI"] if p["goalkeeper"]],
                         ["Marko Dmitrović"])
        self.assertEqual(len(result["homeBench"]), 1)

    def test_missing_goalkeeper_is_reported_as_incomplete(self):
        fixtures = [{"match_id": "42", "kickoff": None, "home": "Espanyol", "away": "Málaga",
                     "home_badge": None, "away_badge": None}]
        players = [{"name": f"Jugador {index}", "club_slug": "espanyol",
                    "starter": 1, "field_y": "40%", "lineup_order": index,
                    "available": True} for index in range(11)]
        result = _fixture_data(fixtures, players, [])[0]
        self.assertFalse(result["homeComplete"])
        self.assertFalse(any(p["goalkeeper"] for p in result["homeXI"]))


if __name__ == "__main__":
    unittest.main()
