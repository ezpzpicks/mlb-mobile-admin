"""Frozen prior-season spread calibration; preserve court and personnel effects."""
from copy import deepcopy
import json
import math
from pathlib import Path

PARAMETERS = json.loads((Path(__file__).resolve().parents[1] / 'config/ncaam_spread_calibration.json').read_text())
SPREAD_MODEL_VERSION = PARAMETERS['version']
B_ONLY_SELECTION = {
    'version': 'ncaam-spread-b-only-2026-11-22-v1',
    'minimum_point_edge': 5.,
    'maximum_point_edge_exclusive': 7.,
}


def calibrated_spread(source):
    raw = source['margin']
    court = source['adjustments']['home']['home_court_points'] - source['adjustments']['away']['home_court_points']
    player = source['adjustments']['home']['player_points'] - source['adjustments']['away']['player_points']
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
               for v in (raw, court, player, PARAMETERS['scale'])):
        raise ValueError('Spread calibration requires finite captured inputs.')
    neutral = raw - court - player
    margin = PARAMETERS['scale'] * neutral + court + player
    total = source['away_points'] + source['home_points']
    return {
        'model_version': SPREAD_MODEL_VERSION, 'margin': margin,
        'away_points': (total - margin) / 2, 'home_points': (total + margin) / 2,
        'calibration': {
            'parameters': deepcopy(PARAMETERS), 'source_model_version': source['model_version'],
            'source_margin': raw, 'home_court_points': court, 'player_margin': player,
            'neutral_margin': neutral,
        },
    }
