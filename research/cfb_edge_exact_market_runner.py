"""Run an edge-quality research module with proven historical market attachment.

Usage: python research/cfb_edge_exact_market_runner.py recent|robust|power
"""
from __future__ import annotations
import sys
from research import cfb_totals_efficiency_regression as base
from research.cfb_historical_markets import attach_market_spread

_original_games_from_pbp=base.games_from_pbp
_cache={}
def games_from_pbp_with_markets(season:int):
    season=int(season)
    if season not in _cache:
        frame=_original_games_from_pbp(season)
        _cache[season]=attach_market_spread(frame,base.cfb)
    return _cache[season].copy()
base.games_from_pbp=games_from_pbp_with_markets

which=sys.argv[1] if len(sys.argv)>1 else ''
if which=='recent':
    from research import cfb_recent_form_edge_quality_search as mod
elif which=='robust':
    from research import cfb_robust_loss_edge_quality_search as mod
elif which=='power':
    from research import cfb_power_edge_quality_search as mod
else:
    raise SystemExit('expected recent, robust, or power')
mod.main()
