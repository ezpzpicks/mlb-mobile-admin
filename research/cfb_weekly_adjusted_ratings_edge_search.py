"""Run the weekly edge-search harness with SportsDataverse opponent-adjusted ratings.

The upstream cfb_ratings_weekly data contains as-of-week opponent-adjusted EPA
and drive-efficiency ratings built only through each snapshot week. Market lines
remain evaluation-only through the imported harness. Team matching is inherited
from the base harness and uses ESPN team ids directly from the PBP game rows.
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

def load_ratings(season:int)->pd.DataFrame:
    url=f'{m.BASE_URL}/cfb_ratings_weekly/cfb_ratings_weekly_{season}.rds'
    data=pyreadr.read_r(io.BytesIO(m.download_bytes(url)));df=next(iter(data.values())).copy();df.columns=[str(c).lower() for c in df.columns]
    for c in ('season','team_id','through_week',*m.RAW):
        if c in df.columns:df[c]=pd.to_numeric(df[c],errors='coerce')
    if 'through_week' not in df.columns:raise RuntimeError(f'{season} adjusted ratings missing through_week')
    df['week']=df['through_week']
    return df

m.load_fpi=load_ratings

if __name__=='__main__':
    m.main()
    src_json=m.OUT/'cfb_weekly_fpi_edge_search.json';dst_json=m.OUT/'cfb_weekly_adjusted_ratings_edge_search.json'
    src_md=m.OUT/'CFB_WEEKLY_FPI_EDGE_SEARCH.md';dst_md=m.OUT/'CFB_WEEKLY_ADJUSTED_RATINGS_EDGE_SEARCH.md'
    if src_json.exists():src_json.replace(dst_json)
    if src_md.exists():
        text=src_md.read_text().replace('CFB Weekly Opponent-Adjusted FPI Edge Search','CFB Weekly Opponent-Adjusted EPA/FEI Edge Search').replace('FPI layer','Adjusted ratings layer')
        dst_md.write_text(text);src_md.unlink()
