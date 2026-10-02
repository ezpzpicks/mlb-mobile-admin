"""Historical CFB closing-line helper for research evaluation only.

This mirrors the proven 2025 grade-backtest market extraction. The returned
spread is never a model feature; it is attached only after independent football
predictions are generated so ATS edge ordering can be evaluated.
"""
from __future__ import annotations
import math
import re
from typing import Any
import numpy as np
import pandas as pd
import polars as pl


def number(value: Any, default: float = math.nan) -> float:
    try:
        out=float(value)
        return out if math.isfinite(out) else default
    except Exception:
        return default


def norm_team(cfb: Any, value: Any) -> str:
    try:
        return cfb._normalize_team(value)
    except Exception:
        return re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip()


def same_team(cfb: Any, a: Any, b: Any) -> bool:
    aa,bb=norm_team(cfb,a),norm_team(cfb,b)
    if not aa or not bb:return False
    if aa==bb:return True
    aset={x for x in aa.split() if len(x)>2};bset={x for x in bb.split() if len(x)>2}
    return bool(aset and bset and len(aset & bset)>=min(2,len(aset),len(bset)))


def first_present(names:set[str],*candidates:str)->str|None:
    for c in candidates:
        if c in names:return c
    lower={n.lower():n for n in names}
    for c in candidates:
        if c.lower() in lower:return lower[c.lower()]
    return None


def first_valid(series:pd.Series)->Any:
    for value in series:
        if value is None:continue
        if isinstance(value,float) and math.isnan(value):continue
        if str(value).strip() not in {'','nan','None'}:return value
    return np.nan


def load_pbp_markets(cfb:Any,season:int)->pd.DataFrame:
    loader=getattr(cfb,'_download_open_asset_now',None)
    path=loader('cfbfastR_cfb_pbp',season,('play_by_play','pbp')) if callable(loader) else cfb._download_open_asset('cfbfastR_cfb_pbp',season,('play_by_play','pbp'))
    if path is None or not path.exists():
        raise RuntimeError(f'Could not download CFB PBP market data for {season}')
    scan=pl.scan_parquet(str(path));names=set(scan.collect_schema().names())
    aliases={
        'game_id':('game_id','id_game'),
        'week':('week',),
        'home_team':('home_team','homeTeamName','homeTeam'),
        'away_team':('away_team','awayTeamName','awayTeam'),
        'home_team_spread':('home_team_spread','homeTeamSpread'),
        'home_favorite':('home_favorite','homeFavorite'),
        'spread':('spread','game_spread','gameSpread'),
        'formatted_spread':('formatted_spread','formattedSpread'),
        'over_under':('over_under','overUnder','game_over_under'),
    }
    selected=[]
    for canonical,candidates in aliases.items():
        actual=first_present(names,*candidates)
        if actual:selected.append(pl.col(actual).alias(canonical))
    if not any(expr.meta.output_name()=='game_id' for expr in selected):
        raise RuntimeError(f'PBP market file did not contain game id for {season}')
    frame=scan.select(selected).collect(engine='streaming').to_pandas()
    grouped=frame.groupby('game_id',as_index=False).agg(first_valid)
    grouped['game_id']=grouped['game_id'].astype(str).str.replace(r'\.0$','',regex=True)
    return grouped


def derive_home_spread(cfb:Any,row:pd.Series,home_team:str,away_team:str)->float:
    direct=number(row.get('home_team_spread'))
    if math.isfinite(direct):return direct
    spread=number(row.get('spread'));formatted=str(row.get('formatted_spread') or '').strip()
    if formatted:
        match=re.search(r'([+-]?\d+(?:\.\d+)?)\s*$',formatted)
        if match:
            line=float(match.group(1));favorite=formatted[:match.start()].strip()
            if same_team(cfb,favorite,home_team):return -abs(line)
            if same_team(cfb,favorite,away_team):return abs(line)
    favorite=row.get('home_favorite')
    if math.isfinite(spread) and favorite is not None and str(favorite).lower() not in {'nan','none',''}:
        is_home=bool(favorite) if isinstance(favorite,(bool,np.bool_)) else str(favorite).strip().lower() in {'1','true','yes','y'}
        return -abs(spread) if is_home else abs(spread)
    return math.nan


def market_spread_map(cfb:Any,season:int)->dict[str,float]:
    markets=load_pbp_markets(cfb,season);out={}
    for _,row in markets.iterrows():
        gid=str(row.get('game_id') or '').replace('.0','')
        if not gid:continue
        hs=derive_home_spread(cfb,row,str(row.get('home_team') or ''),str(row.get('away_team') or ''))
        if math.isfinite(hs):out[gid]=float(hs)
    print(f'{season}: exact historical home spreads recovered for {len(out)}/{len(markets)} games')
    return out


def attach_market_spread(frame:pd.DataFrame,cfb:Any,season_col:str='season',game_id_col:str='game_id')->pd.DataFrame:
    out=frame.copy();out['market_home_spread']=pd.to_numeric(out.get('market_home_spread'),errors='coerce') if 'market_home_spread' in out.columns else np.nan
    for season in sorted(pd.to_numeric(out[season_col],errors='coerce').dropna().astype(int).unique()):
        mapping=market_spread_map(cfb,int(season));mask=pd.to_numeric(out[season_col],errors='coerce').eq(int(season));ids=out.loc[mask,game_id_col].astype(str).str.replace(r'\.0$','',regex=True)
        exact=ids.map(mapping);out.loc[mask,'market_home_spread']=pd.to_numeric(out.loc[mask,'market_home_spread'],errors='coerce').fillna(exact).to_numpy()
    return out
