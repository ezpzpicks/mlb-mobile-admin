"""Run the weekly edge-search harness with SportsDataverse opponent-adjusted ratings.

The upstream cfb_ratings_weekly data contains as-of-week opponent-adjusted EPA
and drive-efficiency ratings built only through each snapshot week. Market lines
remain evaluation-only through the imported harness.
"""
from __future__ import annotations
import io
import pandas as pd
import pyreadr
from research import cfb_weekly_fpi_edge_search as m

m.RAW=['adj_off_epa','adj_def_epa','adj_st_epa','adj_net','fei_off','fei_def','fei_net','off_pace','net_z']
m.FAMILIES={
 'adj_epa':['adj_off_epa_diff','adj_def_epa_diff','adj_net_diff'],
 'drive_fei':['fei_off_diff','fei_def_diff','fei_net_diff'],
 'special_teams':['adj_st_epa_diff'],
 'net_only':['adj_net_diff','fei_net_diff','net_z_diff'],
 'off_def':['adj_off_epa_diff','adj_def_epa_diff','fei_off_diff','fei_def_diff'],
 'all_adjusted':['adj_off_epa_diff','adj_def_epa_diff','adj_st_epa_diff','adj_net_diff','fei_off_diff','fei_def_diff','fei_net_diff','off_pace_diff','net_z_diff'],
}

def load_team_map(season:int)->dict[int,str]:
    last_exc=None
    for year in range(int(season), max(2014, int(season)-3)-1, -1):
        url=f'{m.BASE_URL}/cfb_crosswalk/cfb_teams_crosswalk_{year}.parquet'
        try:
            df=pd.read_parquet(io.BytesIO(m.download_bytes(url)))
            break
        except Exception as exc:
            last_exc=exc
    else:
        raise last_exc if last_exc else RuntimeError(f'No team crosswalk available for {season}')
    df.columns=[str(c).lower() for c in df.columns]
    ids=pd.to_numeric(df.get('espn_team_id'),errors='coerce');names=df.get('espn_team');keys=df.get('norm_key');out={}
    for i,name,key in zip(ids,names,keys):
        if pd.isna(i):continue
        raw=key if pd.notna(key) and str(key).strip() else name
        if pd.notna(raw) and str(raw).strip():out[int(i)]=m.norm(raw)
    return out

def load_ratings(season:int)->pd.DataFrame:
    url=f'{m.BASE_URL}/cfb_ratings_weekly/cfb_ratings_weekly_{season}.rds'
    data=pyreadr.read_r(io.BytesIO(m.download_bytes(url)));df=next(iter(data.values())).copy();df.columns=[str(c).lower() for c in df.columns]
    for c in ('season','team_id','through_week',*m.RAW):
        if c in df.columns:df[c]=pd.to_numeric(df[c],errors='coerce')
    if 'through_week' not in df.columns:raise RuntimeError(f'{season} adjusted ratings missing through_week')
    df['week']=df['through_week']
    return df

m.load_fpi=load_ratings
m.load_team_map=load_team_map

if __name__=='__main__':m.main()
