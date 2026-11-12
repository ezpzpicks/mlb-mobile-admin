"""Production capture adapter for the configurable matchup total grader."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

from builders.ncaam_matchup_grades import (
    grade_saved_forecast, load_policy, number, policy_digest, population_benchmark, stamp,
)
from shared import ncaam_auto_storage as store


def load_reference_quotes(db, day, as_of):
    """One bounded indexed query, returning quote fields rather than player payloads."""
    date = datetime.fromisoformat(day)
    dates = [(date-timedelta(days=n)).date().isoformat() for n in (1, 2, 3)]
    q = db._sql_text
    sql = f"""WITH docs AS (
      SELECT json_extract(payload_json,'$.\"Details JSON\"') AS f FROM dataset_rows
      WHERE sport='NCAAM' AND dataset={q(store.FORECASTS)} AND date_key IN ({','.join(q(d) for d in dates)})
    ) SELECT json_extract(f,'$.gameId') AS game_id, json_extract(f,'$.lockedAt') AS locked_at,
      json_extract(f,'$.tipoff') AS tipoff, json_extract(f,'$.recordId') AS record_id,
      json_extract(f,'$.modelVersion') AS model_version, json_extract(f,'$.market.total') AS total,
      json_extract(f,'$.market.as_of') AS observed_at FROM docs"""
    latest = {}
    for row in db._result_rows(db._execute(sql)):
        try:
            lock, tip, observed = stamp(row['locked_at']), stamp(row['tipoff']), stamp(row['observed_at'])
            if not observed <= lock < tip or lock > stamp(as_of):
                continue
            total = number(row['total'])
            if not 50 < total < 300:
                continue
            key = str(row['game_id'])
            order = (lock, str(row['model_version'])+'|'+str(row['record_id']))
            if key not in latest or order > latest[key][0]:
                latest[key] = (order, {'game_id':key, 'total':total, 'observed_at':observed.isoformat()})
        except (KeyError, TypeError, ValueError):
            continue
    return [value[1] for value in latest.values()]


def capture_grade(forecast, benchmarks, reference_quotes, *, mode='live', policy=None, published_at=None):
    policy = load_policy() if policy is None else policy
    published_at = published_at or datetime.now(timezone.utc).isoformat()
    if mode not in ('live', 'replay'):
        raise ValueError('Grade capture mode must be live or replay.')
    if mode == 'live' and not stamp(forecast['lockedAt']) <= stamp(published_at) < stamp(forecast['tipoff']):
        raise ValueError('Live grades must be captured and published before tipoff.')
    grade = grade_saved_forecast(forecast, benchmarks, reference_quotes, policy)
    benchmark = max((b for b in benchmarks if b['season']==forecast['home_court']['season'] and stamp(b['observed_at'])<=stamp(forecast['lockedAt'])),
                    key=lambda b:stamp(b['observed_at']))
    return {**grade, 'forecast_record_id':forecast['recordId'], 'game_id':str(forecast['gameId']),
            'record_mode':mode, 'published_at':published_at, 'thresholds':deepcopy(policy['thresholds']),
            'national_benchmark':deepcopy(benchmark)}


def unavailable_grade(forecast, error, policy=None):
    policy = load_policy() if policy is None else policy
    return {'policy_version':policy['version'], 'policy_sha256':policy_digest(policy),
            'forecast_record_id':forecast['recordId'], 'game_id':str(forecast['gameId']),
            'grade':'Research', 'side':None, 'line':forecast['market']['total'],
            'as_of':forecast['lockedAt'], 'tipoff':forecast['tipoff'], 'record_mode':'live',
            'published_at':datetime.now(timezone.utc).isoformat(),
            'reason':'Matchup grading inputs unavailable: '+str(error), 'directions':{}, 'z':{}}


def grade_storage_row(forecast, grade, day):
    return {'Date':day, 'Game Key':f"{day}|ncaam|{forecast['gameId']}", 'Game ID':str(forecast['gameId']),
            'Game':forecast['away_team']+' @ '+forecast['home_team'], 'Market':'Total',
            'Selection':forecast['recordId']+'|'+grade['policy_sha256'],
            'Updated At':grade['published_at'], 'Details JSON':json.dumps(grade,sort_keys=True)}
