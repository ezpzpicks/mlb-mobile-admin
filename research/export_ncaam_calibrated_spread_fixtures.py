"""Generate TypeScript parity fixtures from the native Python implementation."""
import argparse
import json
from pathlib import Path
from builders.ncaam_matchup_projection import MODEL_VERSION, INDEPENDENT_MODEL_VERSION
from research.test_ncaam_point_edges import forecast, grades


def cases():
    output=[]
    for name,kwargs,gap in [
        ('neutral_B',{},6.),
        ('personnel_B',{'court':3.2,'impact':{'away':{'offense_change_per_100':-2.,'defense_improvement_per_100':1.},
            'home':{'offense_change_per_100':1.,'defense_improvement_per_100':0.}}},-6.),
        ('elite_7_research',{'away':{'team':'Away','offense':117.,'defense':93.,'tempo':70.},
            'home':{'team':'Home','offense':105.,'defense':108.,'tempo':70.}},7.),
    ]:
        old,_=forecast(**kwargs,model_version=INDEPENDENT_MODEL_VERSION)
        f,b=forecast(**kwargs,model_version=MODEL_VERSION)
        f['market']['home_spread']=gap-f['matchup_projection']['margin']
        f['market']['total']=f['total']-11
        f.update(gameId=name,recordId=f'{name}|{MODEL_VERSION}|fixture',home_spread_edge=gap,
            total_edge=11.,spread_pick='',total_pick='')
        g=grades(f,b)
        for market,grade in g.items():
            f['matchup_'+market]=grade;f[market+'_grade']=grade['grade']
            f[market+'_pick']=(f"{grade['selection_team']} {grade['line']:+g}" if market=='spread'
                else f"{grade['projected_side']} {grade['line']:g}")
        output.append({'name':name,'forecast':f,'before':{'margin':-old['fair_home_spread'],'total':old['total']}})
    return {'cases':output}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True)
    args=parser.parse_args();Path(args.output).write_text(json.dumps(cases(),indent=2,allow_nan=False)+'\n')
