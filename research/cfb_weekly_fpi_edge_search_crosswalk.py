"""Run weekly FPI edge search with the official ESPN team-id crosswalk."""
from __future__ import annotations
import io
import pandas as pd
from research import cfb_weekly_fpi_edge_search as m


def load_team_map(season: int) -> dict[int,str]:
    # The 2026 crosswalk asset is not yet published, but ESPN team IDs are stable.
    # Walk backward to the newest available crosswalk. Prefer norm_key because the
    # game/PBP side is already normalized to school identity while espn_team may
    # include the mascot/display-name suffix.
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
    ids=pd.to_numeric(df.get('espn_team_id'),errors='coerce')
    names=df.get('espn_team')
    normkeys=df.get('norm_key')
    out={}
    for i,name,key in zip(ids,names,normkeys):
        if pd.isna(i):continue
        raw=key if pd.notna(key) and str(key).strip() else name
        if pd.notna(raw) and str(raw).strip():out[int(i)]=m.norm(raw)
    return out

m.load_team_map=load_team_map

if __name__=='__main__':
    m.main()
