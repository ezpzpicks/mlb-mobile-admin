"""Season-specific Division I membership, verified against Warren Nolan and ESPN."""
from __future__ import annotations

import json
from pathlib import Path

_ROSTER = json.loads((Path(__file__).parent / "data" / "ncaam-division-one-2027.json").read_text())
_MEMBERS = frozenset(team["id"] for team in _ROSTER["teams"])
if len(_MEMBERS) != len(_ROSTER["teams"]) or len(_MEMBERS) < 300:
    raise ValueError("Invalid NCAAM Division I membership snapshot.")


def division_one_matchup(away_id, home_id, season):
    """Both IDs must belong to the verified membership group for this season."""
    return (season == _ROSTER["season"] and str(away_id) != str(home_id)
            and str(away_id) in _MEMBERS and str(home_id) in _MEMBERS)
