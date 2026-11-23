# NCAAM split strength and line confirmation — v3, November 22, 2026

The research updater now gives an ordinary split change only one quarter of its usual weight while the line is flat. Supporting line response progressively restores the full split weight. Money moving against tickets retains a separate, stronger starting weight to preserve the eight previously approved interpretations. Independent line movement continues to count at full weight.

This changes UConn–Arizona's final balanced research projection from 152.2 to **148.1**, below the 149.1 baseline. The late Over-money rebound reduces earlier Under pressure without creating an automatic strong Over adjustment. All 54 targeted tests pass. The revised historical returns still underperform the current baseline, so this is a draft research candidate with no live activation.

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

| Nov 18 update | Total line | Over tickets | Over money | Baseline | Previous v2 | Revised v3 |
|---|---:|---:|---:|---:|---:|---:|
| Nov 18 06:40 PM ET | 147.5 | 22% | 31% | 149.1 | 144.3 | 146.9 |
| Nov 18 08:15 PM ET | 146.5 | 22% | 31% | 149.1 | 143.3 | 145.9 |
| Nov 18 08:40 PM ET | 146.5 | 26% | 66% | 149.1 | 152.2 | 148.1 |

At 8:15 PM, the one-point line decline independently lowers v3's projection by one point. At 8:40 PM, money share rebounds from 31% to 66% while tickets rise from 22% to 26% and the line remains 146.5. Both shares move toward Over, so this is ordinary pressure with a 25% multiplier, rather than opposing-ticket divergence. The new cash interval contributes **1.75 points**, compared with 7 points at v2's full money weight, before the rest of the recorded history, recency and final floor are applied. The final result remains below baseline and below the eight-point total eligibility threshold.

Every underlying baseline, rating and market timestamp is checked against the update time. Every earned confirmation timestamp must already be visible. A fresh underlying baseline may replace an earlier one; an out-of-order older baseline cannot replace a newer one.

## Matched historical replay

The original saved input package contains 458 physical games: 183 warm-up games, 268 Nov 14–21 evaluation games and seven Nov 22 games reported separately. Source hashes match the original package. The baseline exactly reproduces the original study at identical saved quotes.

Daily sizing chooses among the three declared profiles using only earlier slate dates after at least 150 earlier games and 20 qualifying prior bets. A six-hour post-tipoff proxy accounts for outcome availability, and the cutoff precedes the earliest evaluated update that date. Exact settlement timestamps are unavailable. Spread eligibility remains 5 to below 7 points of absolute edge; totals qualify at 8, with A at 10. Prices are assumed −110 with one unit risked.

| Formula | Spread W–L–P | Spread units | Spread ROI | Total W–L–P | Total units | Total ROI |
|---|---:|---:|---:|---:|---:|---:|
| Current baseline | 26-12-0 | +11.64 | +30.6% | 9-5-0 | +3.18 | +22.7% |
| Previous v2 balanced | 18-13-0 | +3.36 | +10.9% | 12-7-0 | +3.91 | +20.6% |
| v3 balanced | 17-14-0 | +1.45 | +4.7% | 9-7-0 | +1.18 | +7.4% |
| v3 strong | 13-15-0 | -3.18 | -11.4% | 14-12-0 | +0.73 | +2.8% |
| v3 divergence | 17-13-0 | +2.45 | +8.2% | 9-7-0 | +1.18 | +7.4% |
| v3 earlier-data selection | 15-14-0 | -0.36 | -1.3% | 10-9-0 | +0.09 | +0.5% |

The fixed balanced version changes spread profit by **-10.18 units** and total profit by **-2.00 units** versus baseline. Earlier-data profile selection changes spread profit by **-12.00 units** and total profit by **-3.09 units**. No v3 preset outperforms baseline in either market. The better behavior in the selected example does not establish a profitable betting improvement.

The latest calibration through Nov 21 selects `divergence` for spreads and `strong` for totals using 451 earlier games per market. Those latest choices are not retroactively applied to earlier evaluation dates. Mean absolute balanced changes are 1.30 spread points and 1.54 total points; chronological-selection changes average 1.81 and 1.81, respectively.

These are overlapping exploratory samples after several model revisions, not a fresh untouched holdout. The current baseline and its selection thresholds were also researched on historical data. Small betting samples, assumed prices and sparse snapshot timing limit what can be inferred.

## Validation and status

- 54 targeted tests pass, including all eight approved examples under all three presets and all 27 basic up/down/flat movement states.
- Tests cover gradual confirmation, flat-line rebounds, later confirmation, earlier-line exclusion, price buybacks, expired windows, duplicate ticks, missing data, symmetry and stream/batch parity.
- 550 matched market comparisons and 2,750 prediction rows use identical quotes across variants.
- 749 visible fixed-baseline updates plus the separate fresh-baseline UConn timeline are saved for inspection.
- Original source hashes, baseline parity, independent net price and already-visible confirmation checks pass.
- The production builder, site reader, official-pick eligibility, schedule and database are unchanged. The research updater does not poll a live service by itself.

The preceding v2 source is preserved at commit bd3877913cec88203243ffcb2f636eabb460d70e and its dated compact aggregate remains unchanged. The v1 aggregate is also preserved. Current review: https://github.com/ezpzpicks/mlb-mobile-admin/pull/69.

## Reproduction

Extract NCAAM_Market_Adjustment_Data_2026-11-22.zip with its internal paths intact, then run from the repository root:

    python3 -m unittest discover -s research -p 'test_ncaam_market_flow.py' -v
    python3 research/replay_ncaam_market_flow.py --input-root /path/to/extracted-package
    python3 research/build_ncaam_market_flow_report.py --input-root /path/to/extracted-package

Generated working rows and trajectories go under ignored research/results/ncaam_market_flow_v3. The compact aggregate is research/results/ncaam_directional_market_flow_v3_2026-11-22.json. The formula and preset configuration are in config/ncaam_market_flow_research.json.
