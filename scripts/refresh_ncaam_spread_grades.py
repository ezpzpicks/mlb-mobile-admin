#!/usr/bin/env python3
"""Annotate only the latest unstarted captures with the reviewed spread policy."""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime,timezone
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from builders.ncaam_matchup_grades import policy_digest,stamp
from builders.ncaam_spread_grades import load_spread_policy,capture_spread_grade,spread_storage_row
from builders.ncaam_point_edges import load_policy as load_point_policy
from builders.ncaam_matchup_projection import POINT_MODEL_VERSIONS
from shared import ncaam_auto_storage as store


def refresh(db,day,*,commit=False,now=None,point_model=False):
    now=now or datetime.now(timezone.utc).isoformat()
    policy=load_point_policy() if point_model else load_spread_policy();latest={}
    for row in store.rows(db,store.FORECASTS,day):
        f=json.loads(row['Details JSON'])
        if point_model and f.get('modelVersion') not in POINT_MODEL_VERSIONS:continue
        if not stamp(f['lockedAt'])<=stamp(now)<stamp(f['tipoff']):continue
        key=str(f['gameId']);order=(stamp(f['lockedAt']),f['modelVersion']+'|'+f['recordId'])
        if key not in latest or order>latest[key][0]:latest[key]=(order,f)
    entries=[];selections=[]
    for _,f in latest.values():
        benchmark=(f.get('matchup_spread') or {}).get('national_benchmark') or (f.get('matchup_total') or {}).get('national_benchmark')
        if not benchmark:raise ValueError('Saved national benchmark unavailable; wait for a new builder capture.')
        grade=capture_spread_grade(f,[benchmark],policy=policy,published_at=now)
        entries.append((store.SPREAD_GRADES,spread_storage_row(f,grade,day),True))
        selections.append({'game_id':str(f['gameId']),'game':f['away_team']+' @ '+f['home_team'],
            'team':grade['selection_team'],'line':grade['line'],'edge':grade['edge'],'grade':grade['grade'],'role':grade['role']})
    if commit:
        current=datetime.now(timezone.utc)
        if any(stamp(json.loads(row['Details JSON'])['tipoff'])<=current for _,row,_ in entries):
            raise ValueError('Tipoff reached before spread policy annotations could be saved.')
        store.upsert_many(db,entries)
    return {'date':day,'saved':commit,'policy':policy['version'],'policy_sha256':policy_digest(policy),
        'rows':len(entries),'grades':dict(Counter(r['grade'] for r in selections)),
        'plays':[r for r in selections if r['grade'] in ('A','B')]}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--date',default=datetime.now(ZoneInfo('America/New_York')).date().isoformat())
    p.add_argument('--point-model',action='store_true',help='Use the current point-edge policy for saved point-model captures.')
    p.add_argument('--expected-policy-sha',required=True);p.add_argument('--commit',action='store_true');args=p.parse_args()
    policy=load_point_policy() if args.point_model else load_spread_policy()
    if policy_digest(policy)!=args.expected_policy_sha:raise ValueError('Reviewed spread policy changed.')
    from shared import turso_storage as db
    print(json.dumps(refresh(db,args.date,commit=args.commit,point_model=args.point_model)))


if __name__=='__main__':main()
