#!/usr/bin/env python3
"""Apply the current reviewed policy to unstarted saved NCAAM captures only."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from builders.ncaam_matchup_grades import load_policy, policy_digest, stamp
from builders.ncaam_matchup_live import capture_grade, grade_storage_row, load_reference_quotes
from shared import ncaam_auto_storage as store


def refresh(db, day, *, commit=False, now=None, policy=None):
    now = now or datetime.now(timezone.utc).isoformat()
    policy = load_policy() if policy is None else policy
    latest = {}
    for row in store.rows(db, store.FORECASTS, day):
        forecast = json.loads(row['Details JSON'])
        if not stamp(forecast['lockedAt']) <= stamp(now) < stamp(forecast['tipoff']):
            continue
        game_id = str(forecast['gameId'])
        order = (stamp(forecast['lockedAt']), forecast.get('modelVersion','')+'|'+forecast['recordId'])
        if game_id not in latest or order > latest[game_id][0]:
            latest[game_id] = (order, forecast)
    references = load_reference_quotes(db, day, now)
    entries, summary = [], []
    for _, forecast in latest.values():
        old_grade = forecast.get('matchup_total') or {}
        benchmark = old_grade.get('national_benchmark')
        if not benchmark:
            raise ValueError('Saved national benchmark missing; wait for a fresh builder capture.')
        grade = capture_grade(forecast, [benchmark], references, mode='live', policy=policy, published_at=now)
        entries.append((store.MATCHUP_GRADES, grade_storage_row(forecast, grade, day), True))
        summary.append({'game_id':forecast['gameId'],'game':forecast['away_team']+' @ '+forecast['home_team'],
            'grade':grade['grade'],'side':grade['side'],'line':grade['line'],'projection':forecast['total'],
            'projection_gate':grade.get('projection_gate'),'record_id':forecast['recordId']})
    if commit:
        # Recheck the clock immediately before persisting; closed games retain
        # their original policy and source forecasts are never overwritten.
        current = datetime.now(timezone.utc)
        if any(stamp(json.loads(row['Details JSON'])['tipoff']) <= current for _,row,_ in entries):
            raise ValueError('Tipoff reached before grade refresh.')
        store.upsert_many(db, entries)
    return {'date':day,'saved':commit,'policy':policy['version'],'policy_sha256':policy_digest(policy),
            'rows':len(summary),'grades':dict(Counter(r['grade'] for r in summary)), 'selections':summary}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', default=datetime.now(ZoneInfo('America/New_York')).date().isoformat())
    parser.add_argument('--expected-policy-sha', required=True)
    parser.add_argument('--commit', action='store_true')
    args = parser.parse_args()
    policy = load_policy()
    if policy_digest(policy) != args.expected_policy_sha:
        raise ValueError('The reviewed projection agreement policy changed.')
    from shared import turso_storage as db
    print(json.dumps(refresh(db,args.date,commit=args.commit,policy=policy)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
