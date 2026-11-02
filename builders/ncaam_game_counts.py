"""Count completed D1 games, independently of the displayed all-opponent W-L."""
from __future__ import annotations

import csv
from datetime import datetime
import io
from zoneinfo import ZoneInfo
from builders.ncaam_model import team_key, timestamp

POLICY = 'ncaam-d1-completed-games-v1'


def parse_game_counts(text, season, observed_at, teams):
    as_of = timestamp(observed_at).astimezone(ZoneInfo('America/New_York')).date()
    counts = {key: set() for key in teams}
    seen = set()
    for row in csv.reader(io.StringIO(text)):
        if not row: continue
        if len(row) != 31 or row[22] != str(season):
            raise ValueError('Unsupported Torvik team game-log layout or season.')
        day = datetime.strptime(row[0], '%m/%d/%y').date()
        team, opponent, game_id = team_key(row[2]), team_key(row[4]), row[24]
        if not game_id or team == opponent or (game_id, team) in seen:
            raise ValueError('Ambiguous Torvik team game-log identity.')
        seen.add((game_id, team))
        # A date-only archive cannot establish that a same-day result existed
        # before a forecast. Such games enter the count the following day.
        if day >= as_of or team not in counts or opponent not in counts or not row[6].startswith(('W,', 'L,')):
            continue
        counts[team].add(game_id)
    return {'season': season, 'observed_at': observed_at, 'policy': POLICY,
            'counts': {key: len(games) for key, games in counts.items()},
            'source_url': f'https://barttorvik.com/getgamestats.php?year={season}&csv=1'}
