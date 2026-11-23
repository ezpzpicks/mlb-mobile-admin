"""Build the v3 confirmation review without changing the saved v1/v2 results."""
from pathlib import Path
import argparse
import hashlib
import json

REPO = Path(__file__).resolve().parents[1]
read = lambda p: json.loads(p.read_text())


def pct(value):
    return '—' if value is None else f'{value:+.1f}%'


def results_table(main, previous):
    rows = ['| Formula | Spread W–L–P | Spread units | Spread ROI | Total W–L–P | Total units | Total ROI |',
            '|---|---:|---:|---:|---:|---:|---:|']
    variants = [('Current baseline', main, 'baseline'),
                ('Previous v2 balanced', previous, 'balanced'),
                ('v3 balanced', main, 'balanced'),
                ('v3 strong', main, 'strong'),
                ('v3 divergence', main, 'divergence'),
                ('v3 earlier-data selection', main, 'selected')]
    for label, source, variant in variants:
        s, t = source['Spread'][variant], source['Total'][variant]
        rows.append(f"| {label} | {s['record']} | {s['netUnits']:+.2f} | {pct(s['roiPct'])} | {t['record']} | {t['netUnits']:+.2f} | {pct(t['roiPct'])} |")
    return '\n'.join(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input-root', type=Path, default=REPO.parent)
    parser.add_argument('--results-root', type=Path, default=REPO/'research/results/ncaam_market_flow_v3')
    args = parser.parse_args()
    root = args.results_root
    results = read(root/'results.json')
    latest = read(root/'latest_profiles_through_2026-11-21.json')
    validation = read(root/'validation.json')
    timeline = read(root/'uconn_arizona_fresh_baseline_updates.json')
    spec_path = REPO/'config/ncaam_market_flow_research.json'
    spec = read(spec_path)
    previous = read(REPO/'research/results/ncaam_directional_market_flow_v2_2026-11-22.json')
    original = read(args.input_root/'market_study/overlay_results.json')
    assert results['policy'] == spec['policy']
    assert results['specificationSha256'] == hashlib.sha256(spec_path.read_bytes()).hexdigest()
    for market in ['Spread', 'Total']:
        old = original['results']['historical'][market]['baseline']
        new = results['results']['historical'][market]['baseline']
        assert all(old[k] == new[k] for k in ['opportunities', 'bets', 'record'])
        assert abs(old['netUnits'] - new['netUnits']) < 1e-9
    final = timeline[-1]
    window = final['latestSplitWindow']
    assert window['money_share_change'] == 35 and window['ticket_share_change'] == 4
    assert window['signed_confirmation_response'] == 0 and window['split_strength_multiplier'] == .25
    assert timeline[-2]['projection'] < final['projection'] < final['baseline']
    validation.update(targetedUnitTests=54, approvedExamplesPerProfile=8, basicMovementStatesPerProfile=27,
                      baselineMatchesOriginalFixedStudy=True, uconnFlatReboundReducesUnderWithoutOverFlip=True)
    aggregate = {
        'policy': results['policy'], 'status': 'research_only_not_live', 'specification': spec,
        'specificationSha256': results['specificationSha256'], 'cohort': results['cohort'],
        'evaluationDates': results['evaluationDates'], 'results': results['results'],
        'latestProfilesThroughNov21': latest, 'validation': validation,
        'previousV2Aggregate': 'research/results/ncaam_directional_market_flow_v2_2026-11-22.json',
        'previousV2MainResults': previous['results']['historical'],
        'uconnArizonaFixedBalancedTimeline': timeline,
        'limits': [
            'The baseline and multiple research revisions use overlapping historical games.',
            'Confirmation multipliers and the one-point floor are design priors, not measured true edges.',
            'This v3 formula and all profile weights were declared before its first betting-return screen.',
            'The strong divergence weight doubles balanced in v3; it was 1 in v2 and is 2 here.',
            'Earlier-data sizing is a bounded preset comparison, not established optimal weights.',
            'Assumed -110 prices and a six-hour post-tipoff settlement availability proxy.',
            'Hourly and irregular saved observations cannot establish the exact causal order within a snapshot window.',
            'Every v3 fixed profile and earlier-data selection underperforms the current baseline in both markets.',
        ],
    }
    output = REPO/'research/results/ncaam_directional_market_flow_v3_2026-11-22.json'
    output.write_text(json.dumps(aggregate, indent=2)+'\n')
    historical = results['results']['historical']
    prior_historical = previous['results']['historical']
    previous_case = {r['snapshotTime']: r for r in previous['uconnArizonaFixedBalancedTimeline']}
    case_table = ['| Nov 18 update | Total line | Over tickets | Over money | Baseline | Previous v2 | Revised v3 |',
                  '|---|---:|---:|---:|---:|---:|---:|']
    for row in timeline[-3:]:
        old = previous_case[row['snapshotTime']]
        case_table.append(f"| {row['timeET']} | {row['line']:.1f} | {row['betsPct']:g}% | {row['moneyPct']:g}% | {row['baseline']:.1f} | {old['projection']:.1f} | {row['projection']:.1f} |")
    s, t = historical['Spread'], historical['Total']
    warmup_games = results['cohort']['uniqueEligibleGames'] - historical['Spread']['baseline']['opportunities'] - results['results']['additional_day']['Spread']['baseline']['opportunities']
    doc = f'''# NCAAM split strength and line confirmation — v3, November 22, 2026

The research updater now gives an ordinary split change only one quarter of its usual weight while the line is flat. Supporting line response progressively restores the full split weight. Money moving against tickets retains a separate, stronger starting weight to preserve the eight previously approved interpretations. Independent line movement continues to count at full weight.

This changes UConn–Arizona's final balanced research projection from {previous_case[final['snapshotTime']]['projection']:.1f} to **{final['projection']:.1f}**, below the {final['baseline']:.1f} baseline. The late Over-money rebound reduces earlier Under pressure without creating an automatic strong Over adjustment. All 54 targeted tests pass. The revised historical returns still underperform the current baseline, so this is a draft research candidate with no live activation.

## Confirmation sizing

| Net supporting line response | Ordinary split multiplier | Money/ticket divergence multiplier |
|---|---:|---:|
| Flat | 25% | 75% |
| 0.5 point | 43.75% | 81.25% |
| 1 point | 62.5% | 87.5% |
| 1.5 points | 81.25% | 93.75% |
| 2+ points | 100% | 100% |

Between those values the multiplier is linear: `base + (1 - base) × clip(supporting points / 2, 0, 1)`. Opposing net price response further reduces the split contribution by `1 / (1 + 0.25 × opposing points)`. The price contribution itself is independent and is never reduced by the confirmation multiplier. Divergence is defined as money-share and ticket-share changes with opposite signs; it retains stronger independent evidence as an explicitly declared interpretation, not a claim about bettor skill.

Each split-change window uses its same-window price response plus later already-visible line-only changes for up to 60 minutes. A new observed split change ends that attribution window. Earlier price cannot confirm later cash, a future snapshot cannot confirm a current forecast, and multiple line ticks do not repeat the same confirmation. Net price response withdraws confirmation when a later price buyback reverses it. The next available snapshot recalculates those contributions from the underlying baseline without compounding the previous complete correction.

## Declared base weights

The confirmation multiplier acts on the split channels before the final floor and caps. Price-confirmation bonuses use the smaller of normalized share change and supporting line points; same-window and later confirmation divide that support rather than count it twice.

| Input contribution | Balanced | Strong | Divergence |
|---|---:|---:|---:|
| Per line point | 1 | 2 | 1 |
| Per 10 percentage points of money-share change | 2 | 4 | 2 |
| Additional opposing-ticket divergence, per matched 10 points | 1 | 2 | 2 |
| Ticket-only change, per 10 points, opposite direction | 0.5 | 1 | 0.5 |
| Same-window price confirmation | 0.5 | 1 | 1 |
| Later-price confirmation | 0.25 | 0.5 | 0.5 |
| Maximum spread correction | 4 | 5 | 4 |
| Maximum total correction | 6 | 8 | 6 |

The strong preset now doubles all balanced channel weights, including divergence. That divergence weight changed from 1 in v2 to 2 in v3 before the first v3 outcome screen, keeping all approved directional examples valid under confirmation damping. No alternative confirmation multipliers were selected after viewing these returns.

Split evidence has a two-hour recency weighting whose clock advances only on observed split changes. A price-only refresh cannot fade split evidence merely because time passed. Missing shares stay unknown. Active corrections retain the requested one-point minimum; an exactly neutral weighted result stays zero. The floor, caps and multipliers are design priors, not measured true betting edges.

## UConn–Arizona: actual saved updates

The timeline below uses the same declared fixed balanced profile for v2 and v3, with the latest underlying baseline inputs already available at each tick. It does not use a profile chosen from later games or the final score.

{chr(10).join(case_table)}

At 8:15 PM, the one-point line decline independently lowers v3's projection by one point. At 8:40 PM, money share rebounds from 31% to 66% while tickets rise from 22% to 26% and the line remains 146.5. Both shares move toward Over, so this is ordinary pressure with a 25% multiplier, rather than opposing-ticket divergence. The new cash interval contributes **1.75 points**, compared with 7 points at v2's full money weight, before the rest of the recorded history, recency and final floor are applied. The final result remains below baseline and below the eight-point total eligibility threshold.

Every underlying baseline, rating and market timestamp is checked against the update time. Every earned confirmation timestamp must already be visible. A fresh underlying baseline may replace an earlier one; an out-of-order older baseline cannot replace a newer one.

## Matched historical replay

The original saved input package contains {results['cohort']['uniqueEligibleGames']} physical games: {warmup_games} warm-up games, {s['baseline']['opportunities']} Nov 14–21 evaluation games and seven Nov 22 games reported separately. Source hashes match the original package. The baseline exactly reproduces the original study at identical saved quotes.

Daily sizing chooses among the three declared profiles using only earlier slate dates after at least 150 earlier games and 20 qualifying prior bets. A six-hour post-tipoff proxy accounts for outcome availability, and the cutoff precedes the earliest evaluated update that date. Exact settlement timestamps are unavailable. Spread eligibility remains 5 to below 7 points of absolute edge; totals qualify at 8, with A at 10. Prices are assumed −110 with one unit risked.

{results_table(historical, prior_historical)}

The fixed balanced version changes spread profit by **{s['balanced']['netUnits']-s['baseline']['netUnits']:+.2f} units** and total profit by **{t['balanced']['netUnits']-t['baseline']['netUnits']:+.2f} units** versus baseline. Earlier-data profile selection changes spread profit by **{s['selected']['netUnits']-s['baseline']['netUnits']:+.2f} units** and total profit by **{t['selected']['netUnits']-t['baseline']['netUnits']:+.2f} units**. No v3 preset outperforms baseline in either market. The better behavior in the selected example does not establish a profitable betting improvement.

The latest calibration through Nov 21 selects `{latest['Spread']['profile']}` for spreads and `{latest['Total']['profile']}` for totals using {latest['Spread']['trainingGames']} earlier games per market. Those latest choices are not retroactively applied to earlier evaluation dates. Mean absolute balanced changes are {s['balanced']['meanAbsoluteAdjustment']:.2f} spread points and {t['balanced']['meanAbsoluteAdjustment']:.2f} total points; chronological-selection changes average {s['selected']['meanAbsoluteAdjustment']:.2f} and {t['selected']['meanAbsoluteAdjustment']:.2f}, respectively.

These are overlapping exploratory samples after several model revisions, not a fresh untouched holdout. The current baseline and its selection thresholds were also researched on historical data. Small betting samples, assumed prices and sparse snapshot timing limit what can be inferred.

## Validation and status

- 54 targeted tests pass, including all eight approved examples under all three presets and all 27 basic up/down/flat movement states.
- Tests cover gradual confirmation, flat-line rebounds, later confirmation, earlier-line exclusion, price buybacks, expired windows, duplicate ticks, missing data, symmetry and stream/batch parity.
- {validation['matchedMarketComparisons']:,} matched market comparisons and {validation['predictionRows']:,} prediction rows use identical quotes across variants.
- {validation['visibleSnapshotProjections']:,} visible fixed-baseline updates plus the separate fresh-baseline UConn timeline are saved for inspection.
- Original source hashes, baseline parity, independent net price and already-visible confirmation checks pass.
- The production builder, site reader, official-pick eligibility, schedule and database are unchanged. The research updater does not poll a live service by itself.

The preceding v2 source is preserved at commit bd3877913cec88203243ffcb2f636eabb460d70e and its dated compact aggregate remains unchanged. The v1 aggregate is also preserved. Current review: https://github.com/ezpzpicks/mlb-mobile-admin/pull/69.

## Reproduction

Extract NCAAM_Market_Adjustment_Data_2026-11-22.zip with its internal paths intact, then run from the repository root:

    python3 -m unittest discover -s research -p 'test_ncaam_market_flow.py' -v
    python3 research/replay_ncaam_market_flow.py --input-root /path/to/extracted-package
    python3 research/build_ncaam_market_flow_report.py --input-root /path/to/extracted-package

Generated working rows and trajectories go under ignored research/results/ncaam_market_flow_v3. The compact aggregate is research/results/ncaam_directional_market_flow_v3_2026-11-22.json. The formula and preset configuration are in config/ncaam_market_flow_research.json.
'''
    report = REPO/'research/NCAAM_DIRECTIONAL_MARKET_FLOW.md'
    report.write_text(doc)
    print(json.dumps({'aggregate': str(output), 'report': str(report), 'baselineParity': True,
                      'targetedTests': 54, 'uconnFinal': final['projection'],
                      'latestProfiles': {m: x['profile'] for m, x in latest.items()}}, indent=2))


if __name__ == '__main__':
    main()
