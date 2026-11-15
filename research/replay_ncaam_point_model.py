"""Replay the new formula on frozen, deduplicated pregame inputs, without results."""
from copy import deepcopy
from datetime import datetime, timezone
import argparse
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from builders.ncaam_matchup_grades import policy_digest, stamp
from builders.ncaam_matchup_projection import from_captured_inputs, MODEL_VERSION, GRADE_POLICY
from builders.ncaam_point_edges import capture_grades, load_policy


def replay(payload):
    data=payload.get('ncaamMarket',payload);latest={};computed=datetime.now(timezone.utc).isoformat()
    for f in data.get('forecasts',data.get('games',[])):
        if not stamp(f['lockedAt'])<stamp(f['tipoff']):continue
        key=(stamp(f['tipoff']).astimezone(ZoneInfo('America/New_York')).date().isoformat(),str(f['gameId']));old=latest.get(key)
        if old is None or (stamp(f['lockedAt']),f['recordId'])>(stamp(old['lockedAt']),old['recordId']):latest[key]=f
    output=[];errors=[];plays=[];policy=load_policy()
    for source in sorted(latest.values(),key=lambda f:(stamp(f['tipoff']),str(f['gameId']))):
        try:
            b=(source.get('matchup_spread') or source.get('matchup_total') or {})['national_benchmark']
            if stamp(b['observed_at'])!=stamp(source['ratings_observed_at']):
                raise ValueError('Captured benchmark does not match the rating observation.')
            inputs={k:deepcopy(source.get(k)) for k in ('away_team','home_team','away_rating','home_rating','market',
                'ratings_observed_at','ratings_source_url','home_court','home_court_points','neutral_site','lineup_adjustment',
                'injury_verified','starters_confirmed','availability_mode','player_profile_mode')}
            inputs['national_benchmark']=deepcopy(b)
            p=from_captured_inputs(source['away_rating'],source['home_rating'],b,as_of=source['lockedAt'],tipoff=source['tipoff'],
                source_url=source['ratings_source_url'],home_court_points=source['home_court_points'],
                neutral_site=source['neutral_site'],lineup_adjustment=source.get('lineup_adjustment'))
            source_record=source.get('replay_source_record_id',source['recordId'])
            f={**{k:v for k,v in inputs.items() if k!='national_benchmark'},'gameId':str(source['gameId']),
                'recordId':f"{source['gameId']}|{MODEL_VERSION}|replay|{policy_digest(source_record)[:16]}",
                'modelVersion':MODEL_VERSION,'projection_engine':MODEL_VERSION,'lockedAt':source['lockedAt'],'tipoff':source['tipoff'],
                'phase':'replay','computed_at':computed,'replay_source_record_id':source_record,'replay_inputs':inputs,
                'matchup_projection':p,'away_score':round(p['away_points'],1),'home_score':round(p['home_points'],1),
                'fair_home_spread':-p['margin'],'total':p['total'],'home_spread_edge':p['margin']+source['market']['home_spread'],
                'total_edge':p['total']-source['market']['total'],'grade_policy':GRADE_POLICY,
                'legacy_forecast':deepcopy(source.get('legacy_forecast') or {k:source[k] for k in ('away_score','home_score','fair_home_spread','total')})}
            g=capture_grades(f,[b],mode='replay',policy=policy,published_at=computed)
            for market,grade in g.items():
                f['matchup_'+market]=grade;f[market+'_grade']=grade['grade']
                side=grade['projected_side'];line=grade['line']
                pick=(f"{grade['selection_team']} {line:+g}" if market=='spread' else f"{side} {line:g}")
                f[market+'_pick']=pick
                if grade['eligible']:
                    plays.append({'game_id':f['gameId'],'game':f['away_team']+' @ '+f['home_team'],
                        'market':market.title(),'play':pick,'grade':grade['grade'],'edge_points':grade['edge'],
                        'role':grade['role'] if market=='spread' else side,
                        'fair_home_spread':f['fair_home_spread'],'projected_total':f['total']})
            output.append(f)
        except (ValueError,TypeError,KeyError) as exc:
            errors.append({'game_id':source.get('gameId'),'reason':str(exc)})
    plays.sort(key=lambda p:(-p['edge_points'],p['game'],p['market']))
    return {'computed_at':computed,'date':payload.get('today',data.get('date')),'record_mode':'RETROSPECTIVE_FORMULA_REPLAY',
        'uses_final_results':False,'model_version':MODEL_VERSION,'grading_policy':policy,
        'schedule_games':payload.get('schedule_games',len(data.get('games',[]))),'frozen_forecast_games':len(latest),'games':output,'errors':errors,
        'ranked_candidates':plays,'mix':{role:sum(p['role']==role for p in plays) for role in ('Favorite','Underdog','Over','Under')},
        'grade_counts':{g:sum(p['grade']==g for p in plays) for g in ('A','B')}}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--input',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();result=replay(json.loads(Path(args.input).read_text()))
    Path(args.output).write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ('games','grading_policy')},indent=2))
