"""Season-specific Division I membership, verified against Warren Nolan and ESPN."""
from __future__ import annotations

import json
from pathlib import Path

_ROSTER = json.loads((Path(__file__).parent / "data" / "ncaam-division-one-2027.json").read_text())
_MEMBERS = frozenset(team["id"] for team in _ROSTER["teams"])
_NAMES = {team['id']:team['team'] for team in _ROSTER['teams']}
# Exact source labels for schools whose verified membership name differs from
# Torvik's export. IDs prevent a fuzzy match from selecting another college.
_TORVIK_NAMES = {
    '2856':'Cal Baptist', '2174':'Detroit Mercy', '2226':'Florida Atlantic', '526':'Florida Gulf Coast',
    '85':'IU Indy', '2443':'New Orleans', '112358':'LIU', '2352':'Loyola MD', '2377':'McNeese St.',
    '2447':'Nicholls St.', '152':'N.C. State', '145':'Mississippi', '2437':'Nebraska Omaha',
    '2506':'Presbyterian', '2565':'SIU Edwardsville', '2608':"Saint Mary's", '2547':'Seattle',
    '2908':'USC Upstate', '2546':'Southeast Missouri St.', '357':'Texas A&M Corpus Chris',
    '82':'Illinois Chicago', '2433':'Louisiana Monroe', '113':'Massachusetts', '2430':'UNC Greensboro',
    '350':'UNC Wilmington', '250':'UT Arlington', '292':'UT Rio Grande Valley',
}
if len(_MEMBERS) != len(_ROSTER["teams"]) or len(_MEMBERS) < 300:
    raise ValueError("Invalid NCAAM Division I membership snapshot.")


def division_one_matchup(away_id, home_id, season):
    """Both IDs must belong to the verified membership group for this season."""
    return (season == _ROSTER["season"] and str(away_id) != str(home_id)
            and str(away_id) in _MEMBERS and str(home_id) in _MEMBERS)


def division_one_aliases(team_id, season):
    tid = str(team_id)
    return list(dict.fromkeys([_NAMES[tid], _TORVIK_NAMES.get(tid, _NAMES[tid])])) if season == _ROSTER['season'] and tid in _NAMES else []
