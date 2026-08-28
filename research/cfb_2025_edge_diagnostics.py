"""Fast diagnostics for the already-generated 2025 CFB edge backtest."""
from __future__ import annotations

import csv, json, math, statistics
from collections import defaultdict
from pathlib import Path

SRC = Path('research/results/cfb_2025_game_edges.csv')
OUT = Path('research/results/cfb_2025_edge_diagnostics.json')

rows = list(csv.DictReader(SRC.open()))

def f(x):
    try: return float(x)
    except: return math.nan

def corr(xs, ys):
    pairs=[(x,y) for x,y in zip(xs,ys) if math.isfinite(x) and math.isfinite(y)]
    if len(pairs)<3:return math.nan
    ax=sum(x for x,_ in pairs)/len(pairs); ay=sum(y for _,y in pairs)/len(pairs)
    num=sum((x-ax)*(y-ay) for x,y in pairs)
    dx=sum((x-ax)**2 for x,_ in pairs); dy=sum((y-ay)**2 for _,y in pairs)
    return num/math.sqrt(dx*dy) if dx>0 and dy>0 else math.nan

def slope(xs, ys):
    pairs=[(x,y) for x,y in zip(xs,ys) if math.isfinite(x) and math.isfinite(y)]
    if len(pairs)<3:return math.nan
    ax=sum(x for x,_ in pairs)/len(pairs); ay=sum(y for _,y in pairs)/len(pairs)
    den=sum((x-ax)**2 for x,_ in pairs)
    return sum((x-ax)*(y-ay) for x,y in pairs)/den if den>0 else math.nan

def rec(sub):
    w=sum(r['result']=='W' for r in sub); l=sum(r['result']=='L' for r in sub); p=sum(r['result']=='P' for r in sub)
    d=w+l
    units=w*(100/110)-l
    return {'n':len(sub),'wins':w,'losses':l,'pushes':p,'win_pct':round(100*w/d,2) if d else None,'roi_pct':round(100*units/d,2) if d else None}

def cumulative(market, fbs=True):
    base=[r for r in rows if r['market']==market and (not fbs or r['fbs_fbs']=='True')]
    out=[]
    for i in range(1,81):
        cut=i*.5
        sub=[r for r in base if f(r['edge'])>=cut]
        x={'cutoff':cut,**rec(sub)}
        out.append(x)
    return out

bins=[(0,2),(2,4),(4,6),(6,8),(8,10),(10,12),(12,15),(15,20),(20,30),(30,999)]
def binned(market):
    base=[r for r in rows if r['market']==market and r['fbs_fbs']=='True']
    out=[]
    for lo,hi in bins:
        sub=[r for r in base if f(r['edge'])>=lo and f(r['edge'])<hi]
        out.append({'lo':lo,'hi':hi,**rec(sub)})
    return out

tot=[r for r in rows if r['market'] in ('Over','Under') and r['fbs_fbs']=='True']
spread=[r for r in rows if r['market']=='Spread' and r['fbs_fbs']=='True']

t_signed=[]; t_actual=[]; total_errors=[]
for r in tot:
    line=f(r['line']); proj=f(r['projected_total']); actual=f(r['actual_away'])+f(r['actual_home'])
    t_signed.append(proj-line); t_actual.append(actual-line); total_errors.append(actual-proj)

s_signed=[]; s_actual=[]
for r in spread:
    line=f(r['line']); pm=f(r['projected_margin']); am=f(r['actual_home'])-f(r['actual_away'])
    s_signed.append(pm+line); s_actual.append(am+line)

weekly=defaultdict(list)
for r in tot:
    weekly[int(float(r['week']))].append(r)
weekly_diag={}
for wk,sub in sorted(weekly.items()):
    errs=[]
    for r in sub:
        errs.append((f(r['actual_away'])+f(r['actual_home']))-f(r['projected_total']))
    weekly_diag[str(wk)]={'n':len(sub),'mean_actual_minus_projection':round(sum(errs)/len(errs),3),'mae':round(sum(abs(x) for x in errs)/len(errs),3),'record':rec(sub)}

payload={
  'fbs_fbs':{
    'spread_signal':{'n':len(spread),'corr_signed_edge_to_actual_cover':round(corr(s_signed,s_actual),4),'linear_slope':round(slope(s_signed,s_actual),4)},
    'total_signal':{'n':len(tot),'corr_signed_edge_to_actual_total_edge':round(corr(t_signed,t_actual),4),'linear_slope':round(slope(t_signed,t_actual),4),'mean_actual_minus_projection':round(sum(total_errors)/len(total_errors),3),'mae_points':round(sum(abs(x) for x in total_errors)/len(total_errors),3)},
    'cumulative':{m:cumulative(m) for m in ('Spread','Over','Under')},
    'bins':{m:binned(m) for m in ('Spread','Over','Under')},
    'total_by_week':weekly_diag,
  }
}
OUT.write_text(json.dumps(payload,indent=2))
print(json.dumps(payload['fbs_fbs']['spread_signal'],indent=2))
print(json.dumps(payload['fbs_fbs']['total_signal'],indent=2))
