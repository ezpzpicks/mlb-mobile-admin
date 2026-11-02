from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import sqlite3
import unittest

from builders.ncaam_model import parse_ratings, project, team_key
from scripts.run_ncaam_builder import parse_slate
from shared.ncaam_auto_storage import upsert, rows

NOW = datetime(2026, 11, 1, 13, tzinfo=timezone.utc)


class NcaamModelTests(unittest.TestCase):
    def setUp(self):
        self.bundle = {"season": 2027, "observed_at": NOW.isoformat(), "source": "test ratings", "source_url": "https://example.test/ratings",
            "league_efficiency": 105, "teams": {"away": {"team": "Away", "offense": 112, "defense": 101, "tempo": 68},
                                                 "home": {"team": "Home", "offense": 109, "defense": 99, "tempo": 72}}}
        self.game = {"away": "Away", "home": "Home", "away_aliases": ["Away"], "home_aliases": ["Home"],
            "tipoff": (NOW + timedelta(hours=1)).isoformat(), "neutral_site": True, "season": 2027}
        self.market = {"home_spread": -3, "total": 145, "as_of": NOW.isoformat()}

    def calculate(self, game=None, bundle=None, market=None):
        return project(game or self.game, bundle or self.bundle, market or self.market, NOW.isoformat())

    def test_neutral_site_and_home_court(self):
        neutral = self.calculate()
        home = self.calculate({**self.game, "neutral_site": False})
        self.assertAlmostEqual(neutral["total"], home["total"])
        self.assertAlmostEqual(neutral["fair_home_spread"] - home["fair_home_spread"], 3.2)
        self.assertEqual(neutral["spread_grade"], "Research")
        self.assertNotIn("spread_probability_estimate", neutral)

    def test_better_defense_reduces_opponent_score(self):
        baseline = self.calculate()
        changed = deepcopy(self.bundle)
        changed["teams"]["home"]["defense"] -= 5
        better = self.calculate(bundle=changed)
        self.assertLess(better["away_score"], baseline["away_score"])
        self.assertEqual(better["home_score"], baseline["home_score"])

    def test_market_does_not_change_projected_scores(self):
        baseline = self.calculate()
        changed = self.calculate(market={**self.market, "home_spread": 10, "total": 180})
        for field in ["away_score", "home_score", "fair_home_spread", "total", "possessions"]:
            self.assertEqual(changed[field], baseline[field])

    def test_missing_future_stale_wrong_season_and_unknown_venue_rejected(self):
        for patch in [{"observed_at": (NOW + timedelta(minutes=1)).isoformat()},
                      {"observed_at": (NOW - timedelta(hours=31)).isoformat()}, {"season": 2026}, {"teams": {}}]:
            with self.subTest(patch=patch), self.assertRaises((ValueError, KeyError)):
                self.calculate(bundle={**self.bundle, **patch})
        with self.assertRaises(ValueError): self.calculate({**self.game, "neutral_site": None})
        with self.assertRaises(ValueError): self.calculate({**self.game, "tipoff": NOW.isoformat()})
        with self.assertRaises(ValueError): self.calculate(market={**self.market, "as_of": (NOW - timedelta(minutes=91)).isoformat()})

    def test_export_validation(self):
        csv = "team,adjoe,adjde,adjt,record\n" + "\n".join(f"Team {i},110,100,68,0-0" for i in range(300))
        bundle = parse_ratings(csv, 2027, NOW.isoformat())
        self.assertEqual(len(bundle["teams"]), 300)
        with self.assertRaises(ValueError): parse_ratings("<html>challenge</html>", 2027, NOW.isoformat())
        with self.assertRaises(ValueError): parse_ratings(csv.replace("Team 1,", "Team 0,"), 2027, NOW.isoformat())

    def test_dst_and_neutral_venue_identity(self):
        event = {"id": "401920982", "season": {"year": 2027, "type": 2}, "competitions": [{
            "date": "2026-11-01T14:30:00Z", "timeValid": True, "neutralSite": True,
            "status": {"type": {"name": "STATUS_SCHEDULED"}}, "competitors": [
                {"homeAway": side, "team": {"id": str(i), "displayName": name, "location": name}}
                for i, side, name in [(87, "away", "Notre Dame"), (222, "home", "Villanova")]]}]}
        game = parse_slate({"events": [event]}, "2026-11-01")[0]
        self.assertEqual(datetime.fromisoformat(game["tipoff"]).astimezone(__import__('zoneinfo').ZoneInfo('America/New_York')).hour, 9)
        self.assertTrue(game["neutral_site"])
        with self.assertRaises(ValueError): parse_slate({"events": [event, event]}, "2026-11-01")
        self.assertEqual(parse_slate({"events": [{**event, "season": {"year": 2027, "type": 1}}]}, "2026-11-01"), [])


class StorageTests(unittest.TestCase):
    def test_immutable_forecasts_and_sport_isolation(self):
        connection = sqlite3.connect(":memory:")
        connection.executescript("""CREATE TABLE dataset_rows(sport,dataset,row_index,payload_json,source_hash,date_key,game_key,game,market,selection,result,snapshot_time,imported_at, PRIMARY KEY(sport,dataset,row_index));
        CREATE TABLE dataset_manifest(sport,dataset,source_workbook,source_worksheet,headers_json,row_count,imported_at,source_kind, PRIMARY KEY(sport,dataset));""")
        class DB:
            _sql_text = staticmethod(lambda v: "'" + str(v).replace("'", "''") + "'")
            @staticmethod
            def _pipeline(requests):
                assert len(requests) == 1 and requests[0]["type"] == "sequence"  # shared client appends close
                connection.executescript(requests[0]["sql"])
        row = {"Date": "2026-11-01", "Game Key": "1", "Market": "Forecast", "Selection": "pregame", "Details JSON": "original"}
        upsert(DB, "ncaam_model_forecasts", row, immutable=True)
        upsert(DB, "ncaam_model_forecasts", {**row, "Details JSON": "changed"}, immutable=True)
        saved = connection.execute("SELECT sport,payload_json FROM dataset_rows").fetchall()
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0][0], "NCAAM")
        self.assertEqual(json.loads(saved[0][1])["Details JSON"], "original")


if __name__ == "__main__": unittest.main()
