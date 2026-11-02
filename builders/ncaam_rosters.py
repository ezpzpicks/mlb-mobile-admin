"""Verified current-team membership; cached rosters are not injury reports."""
from __future__ import annotations

from datetime import timedelta
from builders.ncaam_model import timestamp, team_key

ROSTER_POLICY = 'ncaam-espn-season-roster-v1'


def roster_url(team_id, season):
    if not str(team_id).isdigit() or not 2000 <= int(season) <= 2100:
        raise ValueError('Invalid college roster identity.')
    return f'https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/teams/{team_id}/roster?season={season}'


def parse_roster(payload, team_id, season, observed_at):
    timestamp(observed_at)
    if payload.get('season', {}).get('year') != season or str(payload.get('team', {}).get('id')) != str(team_id):
        raise ValueError('ESPN roster team or season does not match.')
    athletes = payload.get('athletes')
    if not isinstance(athletes, list) or not 5 <= len(athletes) <= 40:
        raise ValueError('ESPN college roster is incomplete.')
    players = []
    for athlete in athletes:
        pid, name = str(athlete.get('id', '')), str(athlete.get('displayName', '')).strip()
        if not pid.isdigit() or not name:
            raise ValueError('ESPN college roster has an unverified player.')
        players.append({'id': 'espn:'+pid, 'name': name})
    if len({p['id'] for p in players}) != len(players) or len({team_key(p['name']) for p in players}) != len(players):
        raise ValueError('ESPN college roster has ambiguous player identities.')
    return {'season': season, 'team_id': str(team_id), 'observed_at': observed_at, 'players': players,
            'team': payload['team']['displayName'], 'policy': ROSTER_POLICY, 'source_url': roster_url(team_id, season)}


def valid_roster(roster, team_id, season, now):
    try:
        return bool(roster and roster['policy'] == ROSTER_POLICY and roster['season'] == season
                    and roster['team_id'] == str(team_id) and 5 <= len(roster['players']) <= 40
                    and timedelta(0) <= now-timestamp(roster['observed_at']) <= timedelta(days=2))
    except (KeyError, ValueError, TypeError): return False
