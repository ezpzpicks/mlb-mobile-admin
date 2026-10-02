"""Run weekly FPI edge search with the official ESPN team-id crosswalk."""
from __future__ import annotations
import io
import pandas as pd
from research import cfb_weekly_fpi_edge_search as m


def load_team_map(season: int) -> dict[int,str]:
    url=f'{m.BASE_URL}/cfb_crosswalk/cfb_teams_crosswalk_{season}.parquet'
    df=pd.read_parquet(io.BytesIO(m.download_bytes(url)))
    df.columns=[str(c).lower() for c in df.columns]
    ids=pd.to_numeric(df.get('espn_team_id'),errors='coerce')
    names=df.get('espn_team')
    normkeys=df.get('norm_key')
    out={}
    for i,name,key in zip(ids,names,normkeys):
        if pd.isna(i):continue
        raw=name if pd.notna(name) and str(name).strip() else key
        if pd.notna(raw) and str(raw).strip():out[int(i)]=m.norm(raw)
    return out

m.load_team_map=load_team_map

if __name__=='__main__':
    m.main()
