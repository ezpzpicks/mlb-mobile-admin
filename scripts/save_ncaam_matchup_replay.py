#!/usr/bin/env python3
"""Save explicitly labeled replay grades without changing original forecasts."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from builders.ncaam_matchup_grades import load_policy, policy_digest, population_benchmark
from builders.ncaam_matchup_live import capture_grade, grade_storage_row
from research.replay_ncaam_matchup_grades import latest_forecasts
from shared import ncaam_auto_storage as store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--public', required=True)
    parser.add_argument('--benchmark-cache', required=True)
    parser.add_argument('--reference-public', nargs='+', required=True)
    parser.add_argument('--expected-policy-sha', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--commit', action='store_true')
    args = parser.parse_args()
    policy = load_policy()
    if policy_digest(policy) != args.expected_policy_sha:
        raise ValueError('The reviewed policy changed; refusing to save a different replay.')
    public = json.loads(Path(args.public).read_text())
    now = datetime.now(timezone.utc).isoformat()
    day = public['ncaamMarket']['date']
    _, forecasts, _ = latest_forecasts(public, now)
    benchmarks = [population_benchmark(item['bundle']) for item in json.loads(Path(args.benchmark_cache).read_text())['snapshots']]
    references = []
    for path in args.reference_public:
        reference = json.loads(Path(path).read_text())
        if reference['ncaamMarket']['date'] >= day:
            raise ValueError('Market reference must use earlier game dates.')
        _, previous, _ = latest_forecasts(reference, now)
        references.extend({'game_id':game_id,'total':f['market']['total'],'observed_at':f['market']['as_of']}
                          for game_id, f in previous.items())
    entries = []
    for forecast in forecasts.values():
        grade = capture_grade(forecast, benchmarks, references, mode='replay', policy=policy, published_at=now)
        entries.append((store.MATCHUP_GRADES, grade_storage_row(forecast, grade, day), True))
    Path(args.output).write_text(json.dumps([row for _, row, _ in entries], indent=2)+'\n')
    if args.commit:
        from shared import turso_storage as db
        if not db.is_turso_ready():
            raise ValueError('The existing Turso connection is required.')
        # Match existing immutable capture identities before adding annotations.
        q = db._sql_text
        identities = db._result_rows(db._execute(f"SELECT selection FROM dataset_rows WHERE sport='NCAAM' AND dataset={q(store.FORECASTS)} AND date_key={q(day)}"))
        saved = {row['selection'] for row in identities}
        if not all(f['recordId'] in saved for f in forecasts.values()):
            raise ValueError('A source forecast is absent from the saved slate.')
        store.upsert_many(db, entries)
    print(json.dumps({'date':day,'mode':'replay','saved':bool(args.commit),'rows':len(entries),
                      'grades':dict(Counter(json.loads(row['Details JSON'])['grade'] for _,row,_ in entries)),
                      'policy_sha256':policy_digest(policy)}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
