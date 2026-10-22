"""Fictional, reproducible NBA rotation example. Never live player/team data."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone


def example_package(as_of: datetime | None = None) -> dict:
    as_of = as_of or datetime.now(timezone.utc)
    stamp = as_of.isoformat()
    reference = [36, 34, 32, 30, 28, 24, 22, 18, 16]
    away_players, home_players = [], []
    for prefix, output in (("A", away_players), ("H", home_players)):
        for i, minutes in enumerate(reference):
            output.append({"player_id": f"{prefix}{i + 1}", "name": f"Fictional {prefix} Player {i + 1}",
                           "reference_minutes": minutes, "offensive_impact": 6.0 if i == 0 else 0.0,
                           "defensive_impact": 1.0 if i == 0 else 0.0})

    def team(team_id, players):
        return {"team_id": team_id, "name": f"Fictional {team_id}", "offensive_rating": 115.0,
                "defensive_rating": 115.0, "pace": 100.0, "ratings_as_of": stamp,
                "reference_window": "FICTIONAL demo baseline; not a real sample",
                "ratings_source": "FICTIONAL example", "impact_source": "FICTIONAL example, not fitted ratings",
                "impacts_as_of": stamp, "players": players}

    def rotation(players, away=False):
        return {"injury_as_of": stamp, "lineup_as_of": stamp,
                "injury_source": "FICTIONAL example", "lineup_source": "FICTIONAL example",
                "report_complete": True, "starters_confirmed": not away,
                "players": [{"player_id": p["player_id"], "minutes": p["reference_minutes"],
                             "status": "QUESTIONABLE" if away and i == 0 else "AVAILABLE",
                             "starter": i < 5,
                             "assumption": "IN" if away and i == 0 else "", "minutes_limit": 48.0}
                            for i, p in enumerate(players)]}

    away_in, home = rotation(away_players, away=True), rotation(home_players)
    away_out = deepcopy(away_in)
    away_out["players"][0].update(minutes=0, assumption="OUT", starter=False)
    away_out["players"][5]["starter"] = True
    for index, extra in ((5, 12), (6, 12), (7, 12)):
        away_out["players"][index]["minutes"] += extra
    return {"is_demo": True, "context": {"game_id": "fictional-nba-demo",
             "tipoff": (as_of + timedelta(hours=2)).isoformat(), "as_of": stamp,
             "league_efficiency": 115.0, "league_pace": 100.0,
             "home_court_points": 2.0, "max_lineup_age_minutes": 60.0},
            "away": team("Away", away_players), "home": team("Home", home_players),
            "market": {"home_spread": -4.0, "total": 228.0, "as_of": stamp,
                       "source": "FICTIONAL example; current lines will use ScoresAndOdds at -110"},
            "scenarios": [{"name": "Away scorer plays", "away": away_in, "home": home},
                          {"name": "Away scorer sits", "away": away_out, "home": deepcopy(home)}]}
