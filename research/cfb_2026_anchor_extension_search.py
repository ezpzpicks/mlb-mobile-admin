"""Extend the frozen 2026 four-variable CFB challenger with additional football-only inputs.

Anchor (locked from prior search):
- gamecontrol_diff
- def_returning_diff
- explosive_diff (competitive-state)
- current_power_margin

All evaluation uses 2026 Weeks 1-4 only. Model development uses leave-one-week-out
blocked CV over Weeks 2,3,4 so every reported prediction is out of fold. Market
spreads are evaluation-only and never enter model features.

An addition must improve pooled MAE versus the four-variable anchor, improve MAE
in at least 2/3 held-out weeks, and preserve useful edge ordering. This script
adds at most three variables beyond the locked anchor.
"""
from __future__ import annotations

import io
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from research import cfb_2026_w1_4_regime_search as reg
from research import cfb_returning_production_edge_search as ret
from research import cfb_turnover_luck_edge_search as tov
from research import cfb_weekly_fpi_edge_search as fpi

OUT = Path('research/results'); OUT.mkdir(parents=True, exist_ok=True)
FOLDS = (2, 3, 4)
ANCHOR = ['gamecontrol_diff', 'def_returning_diff', 'explosive_diff', 'current_power_margin']
ALPHAS = (4., 16., 64., 256.)
CAPS = (4., 6., 8.)

# Weekly team-summary features not yet fully explored in the 2026-only search.
SUMMARY_SPECS = {
    'third_down_off_diff': ('third_down_success_off',),
    'third_down_def_diff': ('third_down_success_def',),
    'red_zone_off_diff': ('red_zone_success_off',),
    'red_zone_def_diff': ('red_zone_success_def',),
    'late_down_off_diff': ('late_down_success_off',),
    'late_down_def_diff': ('late_down_success_def',),
    'line_yards_off_diff': ('line_yards_off',),
    'line_yards_def_diff': ('line_yards_def',),
    'opportunity_off_diff': ('opportunity_rate_off',),
    'opportunity_def_diff': ('opportunity_rate_def',),
    'stuff_off_diff': ('play_stuffed_off',),
    'stuff_def_diff': ('play_stuffed_def',),
    'havoc_off_diff': ('havoc_off',),
    'havoc_def_diff': ('havoc_def',),
    'nonexplosive_epa_off_diff': ('nonexplosiveepaperplay_off', 'nonExplosiveEpaPerPlay_off'),
    'nonexplosive_epa_def_diff': ('nonexplosiveepaperplay_def', 'nonExplosiveEpaPerPlay_def'),
    'epa_drive_off_diff': ('epadrive_off', 'EPAdrive_off'),
    'epa_drive_def_diff': ('epadrive_def', 'EPAdrive_def'),
    'epa_game_off_diff': ('epagame_off', 'EPAgame_off'),
    'epa_game_def_diff': ('epagame_def', 'EPAgame_def'),
    'yards_play_off_diff': ('yardsplay_off',),
    'yards_play_def_diff': ('yardsplay_def',),
    'plays_game_off_diff': ('playsgame_off',),
    'plays_game_def_diff': ('playsgame_def',),
    'drives_game_off_diff': ('drivesgame_off',),
    'drives_game_def_diff': ('drivesgame_def',),
}

EXISTING_EXTRA = [
    # FPI / opponent-adjusted efficiency
    'adjavgingamewp_diff', 'adj_net_diff', 'fei_net_diff', 'net_z_diff',
    'adj_off_epa_diff', 'adj_def_epa_diff', 'fei_off_diff', 'fei_def_diff',
    'adj_st_epa_diff', 'accomplishment_diff', 'avgsosrank_diff', 'topsosrank_diff',
    # competitive-state detail
    'off_epa_diff', 'def_epa_edge', 'off_success_diff', 'def_success_edge',
    'pass_epa_diff', 'rush_epa_diff', 'def_pass_epa_edge', 'def_rush_epa_edge',
    'early_epa_diff', 'def_early_epa_edge', 'def_explosive_edge',
    # core model components / continuity / luck
    'prior_power_margin', 'current_scoring_diff', 'current_allowed_diff',
    'off_returning_diff', 'overall_returning_diff', 'avg_overall_returning',
    'turnover_margin_diff', 'expected_turnover_margin_diff', 'turnover_luck_diff',
    'expected_turnovers_off_diff', 'expected_turnovers_def_diff',
]


def safe(x):
    return pd.to_numeric(x, errors='coerce')


def prior_row(wk: pd.DataFrame, tid, week):
    try: tid = float(tid)
    except Exception: return None
    x = wk[(wk.team_id == tid) & (wk.week < int(week))]
    return None if x.empty else x.sort_values('week').iloc[-1]


def find_col(columns, opts):
    lower = {str(c).lower(): c for c in columns}
    for o in opts:
        if o in columns: return o
        if str(o).lower() in lower: return lower[str(o).lower()]
    return None


def add_weekly_summary_features(df):
    wk = tov.load_weekly(2026).copy()
    matched = {}
    for new, opts in SUMMARY_SPECS.items():
        c = find_col(wk.columns, opts)
        if c is not None: matched[new] = c
    for idx, row in df.iterrows():
        a = prior_row(wk, row.away_team_id, row.week)
        h = prior_row(wk, row.home_team_id, row.week)
        if a is None or h is None: continue
        for new, c in matched.items():
            av = pd.to_numeric(pd.Series([a.get(c)]), errors='coerce').iloc[0]
            hv = pd.to_numeric(pd.Series([h.get(c)]), errors='coerce').iloc[0]
            if pd.notna(av) and pd.notna(hv): df.loc[idx, new] = float(hv - av)
    print('weekly summary matched:', matched)
    return df, matched


def add_talent(df):
    url = f'{fpi.BASE_URL}/cfb_team_talent/cfb_team_talent_2026.parquet'
    try:
        tal = pd.read_parquet(io.BytesIO(fpi.download_bytes(url)))
    except Exception as exc:
        print('talent load failed:', exc); return df
    tal.columns = [str(c).lower() for c in tal.columns]
    if 'team_id' not in tal.columns: return df
    tal['team_id'] = pd.to_numeric(tal.team_id, errors='coerce')
    for c in ('talent_composite', 'blue_chip_ratio'):
        if c in tal.columns: tal[c] = pd.to_numeric(tal[c], errors='coerce')
    tal = tal[tal.team_id.notna()].drop_duplicates('team_id').set_index('team_id')
    for idx, row in df.iterrows():
        try: ai = float(row.away_team_id); hi = float(row.home_team_id)
        except Exception: continue
        if ai not in tal.index or hi not in tal.index: continue
        for c in ('talent_composite', 'blue_chip_ratio'):
            if c not in tal.columns: continue
            av, hv = tal.loc[ai, c], tal.loc[hi, c]
            if pd.notna(av) and pd.notna(hv):
                df.loc[idx, f'{c}_diff'] = float(hv - av)
                df.loc[idx, f'avg_{c}'] = float((hv + av) / 2.)
    return df


def prepare():
    df = reg.build_2026().copy()
    df, matched = add_weekly_summary_features(df)
    df = add_talent(df)

    # Interactions centered on the four-variable anchor. All are football-only.
    p = safe(df.current_power_margin)
    gc = safe(df.gamecontrol_diff)
    ex = safe(df.explosive_diff)
    dr = safe(df.def_returning_diff)
    for name, b in [('gamecontrol', gc), ('explosive', ex), ('defreturn', dr)]:
        df[f'power_x_{name}_2026'] = p * b
    df['gamecontrol_x_explosive'] = gc * ex
    df['gamecontrol_x_defreturn'] = gc * dr
    df['explosive_x_defreturn'] = ex * dr

    # Opponent-adjusted confirmation interactions if present.
    for c in ('adj_net_diff', 'fei_net_diff', 'adj_off_epa_diff', 'adj_def_epa_diff'):
        if c in df.columns: df[f'power_x_{c}'] = p * safe(df[c])

    return df, matched


def fit(train, features, alpha): return reg.fit(train, features, alpha)
def pred(df, model, cap): return reg.pred(df, model, cap)
def metrics(df, p): return reg.metrics(df, p, min_bucket=8)


def blocked_oof(df, features, alpha, cap):
    pieces, folds = [], []
    for w in FOLDS:
        tr = df[df.week != w].copy(); te = df[df.week == w].copy()
        if tr.empty or te.empty: continue
        m = fit(tr, features, alpha); q = te.copy(); q['_pred'] = pred(te, m, cap)
        bm = metrics(te, safe(te.baseline).to_numpy(float)); cm = metrics(te, q._pred.to_numpy(float))
        folds.append({'week': w, 'baseline': bm, 'challenger': cm, 'mae_improved': cm['mae'] < bm['mae']})
        pieces.append(q)
    oof = pd.concat(pieces, ignore_index=True)
    return metrics(oof, oof._pred.to_numpy(float)), folds


def evaluate_grid(df, features):
    out = []
    for a in ALPHAS:
        for c in CAPS:
            try: m, folds = blocked_oof(df, features, a, c)
            except Exception: continue
            out.append({'features': features.copy(), 'alpha': a, 'cap': c, 'metrics': m, 'folds': folds})
    return out


def quality_key(r, anchor):
    m = r['metrics']; folds = r['folds']
    n_improve = sum(x['mae_improved'] for x in folds)
    mae_gain = anchor['mae'] - m['mae']
    # Primary: stable MAE. Secondary: preserve/strengthen edge ordering.
    return (n_improve, 1 if mae_gain > 0 else 0, mae_gain, m['steps'], -m['violation'], m['slope'], m['high'] or -1., m['auc'] or -1.)


def rt(m, t): return reg.rt(m, t)

def clean(v):
    if isinstance(v, dict): return {k: clean(x) for k, x in v.items()}
    if isinstance(v, list): return [clean(x) for x in v]
    if isinstance(v, (np.integer,)): return int(v)
    if isinstance(v, (np.floating, float)): return float(v) if math.isfinite(float(v)) else None
    return v


def main():
    df, matched = prepare()
    usable_anchor = [x for x in ANCHOR if x in df.columns and df[x].notna().any()]
    anchor_trials = evaluate_grid(df, usable_anchor)
    # Anchor hyperparameters fixed to prior winner where available; otherwise best blocked result.
    anchor = next((x for x in anchor_trials if x['alpha'] == 16. and x['cap'] == 6.), None)
    if anchor is None: anchor = min(anchor_trials, key=lambda x: x['metrics']['mae'])
    anchor_m = anchor['metrics']

    candidate_pool = []
    for f in EXISTING_EXTRA + list(SUMMARY_SPECS) + [
        'talent_composite_diff','blue_chip_ratio_diff','avg_talent_composite','avg_blue_chip_ratio',
        'power_x_gamecontrol_2026','power_x_explosive_2026','power_x_defreturn_2026',
        'gamecontrol_x_explosive','gamecontrol_x_defreturn','explosive_x_defreturn',
        'power_x_adj_net_diff','power_x_fei_net_diff','power_x_adj_off_epa_diff','power_x_adj_def_epa_diff']:
        if f not in usable_anchor and f in df.columns and df[f].notna().any() and f not in candidate_pool:
            candidate_pool.append(f)

    # First, test every single addition to the locked anchor.
    singles = []
    for f in candidate_pool:
        trials = evaluate_grid(df, usable_anchor + [f])
        if not trials: continue
        b = max(trials, key=lambda x: quality_key(x, anchor_m))
        b['added'] = f; singles.append(b)
    singles.sort(key=lambda x: quality_key(x, anchor_m), reverse=True)

    # Greedily add at most three variables, but require incremental pooled MAE gain
    # and MAE improvement in >=2/3 week folds at every step.
    selected = usable_anchor.copy(); current = anchor; path = []
    remaining = candidate_pool.copy()
    for _ in range(3):
        trials = []
        for f in remaining:
            for r in evaluate_grid(df, selected + [f]):
                r['added'] = f; trials.append(r)
        if not trials: break
        trials.sort(key=lambda x: quality_key(x, current['metrics']), reverse=True)
        b = trials[0]; nimp = sum(x['mae_improved'] for x in b['folds'])
        gain = current['metrics']['mae'] - b['metrics']['mae']
        # Need a real incremental accuracy gain; don't add variables just for a hot ATS bucket.
        if gain < 0.02 or nimp < 2: break
        selected = b['features'].copy(); current = b; path.append(b)
        remaining = [x for x in remaining if x != b['added']]

    # Fit selected Week-5+ candidate on all W1-4 for coefficient inspection only.
    final = fit(df, selected, current['alpha'])
    coefs = {'intercept': float(final['b'][0])}
    for f, b in zip(selected, final['b'][1:]): coefs[f] = float(b)

    out = {
        'season': 2026, 'market_predictor': False, 'usage': 'research candidate for Week 5+; no production change',
        'anchor': anchor, 'matched_weekly_summary_columns': matched,
        'single_additions': singles[:40], 'greedy_path': path, 'selected': current,
        'final_fit_standardized_coefficients': coefs,
    }
    (OUT/'cfb_2026_anchor_extension_search.json').write_text(json.dumps(clean(out), indent=2, allow_nan=False))

    lines = ['# CFB 2026 Four-Variable Anchor Extension Search','',
             f"Anchor: **{', '.join(usable_anchor)}**", f"Anchor alpha={anchor['alpha']:.0f}, cap={anchor['cap']:.0f}",'',
             '| Model | Features | MAE | Steps | slope | AUC | 2+ | 4+ | 6+ | 8+ | 10+ |',
             '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for label, r in [('Anchor', anchor), ('Extended', current)]:
        m = r['metrics']; lines.append(f"| {label} | {', '.join(r['features'])} | {m['mae']:.3f} | {m['steps']}/{m['possible']} | {100*m['slope']:+.1f}pp | {m['auc']:.3f} | " + ' | '.join(rt(m,t) for t in reg.TH) + ' |')
    lines += ['', '## Held-out week MAE', '', '| Week | Anchor | Extended | Improvement |', '|---:|---:|---:|---:|']
    anchor_by_week = {x['week']: x for x in anchor['folds']}
    ext_by_week = {x['week']: x for x in current['folds']}
    for w in FOLDS:
        a = anchor_by_week[w]['challenger']['mae']; e = ext_by_week[w]['challenger']['mae']
        lines.append(f'| {w} | {a:.3f} | {e:.3f} | {a-e:+.3f} |')
    lines += ['', '## Best single additions to anchor', '', '| Added | MAE | MAE gain | Weeks improved | Steps | slope | 10+ | AUC |', '|---|---:|---:|---:|---:|---:|---:|---:|']
    for r in singles[:20]:
        m=r['metrics']; nimp=sum(x['mae_improved'] for x in r['folds']); gain=anchor_m['mae']-m['mae']
        lines.append(f"| {r['added']} | {m['mae']:.3f} | {gain:+.3f} | {nimp}/3 | {m['steps']}/{m['possible']} | {100*m['slope']:+.1f}pp | {100*(m['high'] or 0):.1f}% | {m['auc']:.3f} |")
    lines += ['', '## Greedy extension path', '', '| Step | Added | MAE | Steps | slope | 10+ |', '|---:|---|---:|---:|---:|---:|']
    for i,r in enumerate(path,1):
        m=r['metrics']; lines.append(f"| {i} | {r['added']} | {m['mae']:.3f} | {m['steps']}/{m['possible']} | {100*m['slope']:+.1f}pp | {100*(m['high'] or 0):.1f}% |")
    lines += ['', '## Final standardized coefficients', '', '```json', json.dumps(coefs, indent=2), '```', '', '## Weekly summary columns found', '', '```json', json.dumps(matched, indent=2), '```']
    (OUT/'CFB_2026_ANCHOR_EXTENSION_SEARCH.md').write_text('\n'.join(lines)+'\n')
    print('\n'.join(lines))

if __name__ == '__main__': main()
