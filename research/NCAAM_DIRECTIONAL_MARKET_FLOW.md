# NCAAM market updates and stronger sizing — v2, November 22, 2026

The research candidate now updates on independent line movement and ordered betting-share changes, including flat-money cases, ticket-only changes, later price confirmation and late reversals. Every new paired snapshot recomputes the correction from the unadjusted baseline. The live builder and site reader are unchanged.

The stronger totals overlay adds a small amount of historical profit while reducing ROI; the stronger spread overlays underperform the current baseline. Larger projection changes are implemented and tested, but the replay does not justify promoting the spread layer.

## Movement coverage

Forty targeted tests cover all eight approved examples under every sizing profile and all 27 up/down/flat combinations of line, money and tickets. The 26 canonical combinations with activity produce an adjustment; the all-flat case remains unchanged. Other tests cover degree, recent reversals with equal opening/final shares, lead/lag timing, missing data, duplicate and future observations, stale baseline arrivals and recomputation without compounding prior corrections.

Line points are independent evidence. A total rising one point contributes upward pressure; a team moving from +6.5 to +4.5 gains spread support. This works with flat or missing splits. Share changes remain provider proportions rather than inferred dollar inflows. Ticket-only movement with observed flat money is treated as weakening relative money backing for the popular side; that is a declared research interpretation.

There is no opening-to-current money-direction clamp. The most recent share changes can reverse earlier support. Earlier price remains independent information; price following earlier money within 60 minutes has a distinct corroboration channel. Current quote and both teams' captured inputs remain separate from the interpretation of who caused a price move.

Split evidence has a two-hour recency weighting that advances only when an observed share changes. A price-only refresh therefore cannot fade existing split evidence and inadvertently raise a total merely because time passed while the line fell. That persistence fix was made after the first implementation screen; the three weight presets, floor and caps were unchanged. This remains exploratory research.

## Explicit sizing

These are declared starting presets. Earlier-date net betting units select among them separately for spreads and totals after at least 150 available games and 20 qualifying prior bets; otherwise the candidate uses balanced sizing. This is a bounded preset comparison, not a claim that the weights are globally optimal.

| Input contribution | Balanced | Strong | Divergence |
|---|---:|---:|---:|
| Per line point | 1.0 | 2.0 | 1.0 |
| Per 10 percentage points of money-share change | 2.0 | 4.0 | 2.0 |
| Additional opposing-ticket divergence, per matched 10 points | 1.0 | 1.0 | 2.0 |
| Ticket-only change, per 10 points, opposite direction | 0.5 | 1.0 | 0.5 |
| Same-window price confirmation | 0.5 | 1.0 | 1.0 |
| Later-price confirmation | 0.25 | 0.5 | 0.5 |
| Maximum spread correction | 4 | 5 | 4 |
| Maximum total correction | 6 | 8 | 6 |

Weights act on the recorded interval evidence before recency, price-opposition damping and caps. Confirmation uses the smaller normalized money/price movement. An active correction is at least one point; exactly neutral evidence stays at zero. This floor implements the requested larger movement and is not evidence that every small signal represents one point of true betting value. Each later snapshot can recalculate the direction and strength without adding the previous complete correction again.

The latest calibration through Nov 21 selects **balanced for spreads** and **balanced for totals**, using 451 eligible earlier games per market. The selected profile's mean absolute change in the main replay is **1.65 spread points** and **2.09 total points**.

## Matched historical comparison

The restored original input package supplies 458 physical games, including 183 initial warm-up games, 268 evaluation games on Nov 14–21 and seven Nov 22 games reported separately. Both markets use identical saved final quotes and captured baseline inputs across all variants. Source hashes match the saved package, and the baseline records reproduce the original market study.

Daily sizing uses only earlier slate dates and excludes training games until a six-hour post-tipoff proxy has passed. Its cutoff precedes the earliest evaluated update for that date. Exact settlement timestamps are unavailable. Spread eligibility remains 5 to below 7 points of edge; totals qualify at 8, with A at 10. Prices are assumed −110, one unit risked. No official historical record is rewritten.

| Formula | Spread W–L–P | Spread units | Spread ROI | Total W–L–P | Total units | Total ROI |
|---|---:|---:|---:|---:|---:|---:|
| Current baseline | 26-12-0 | +11.64 | +30.6% | 9-5-0 | +3.18 | +22.7% |
| Balanced fixed sizing | 18-13-0 | +3.36 | +10.9% | 12-7-0 | +3.91 | +20.6% |
| Strong fixed sizing | 19-14-0 | +3.27 | +9.9% | 23-20-0 | +0.91 | +2.1% |
| Divergence fixed sizing | 17-14-0 | +1.45 | +4.7% | 12-7-0 | +3.91 | +20.6% |
| Earlier-data sizing selection | 18-13-0 | +3.36 | +10.9% | 12-7-0 | +3.91 | +20.6% |

Selected totals move from 14 bets at 9–5 and +3.18 units to 19 bets at 12–7 and +3.91 units: **+0.73 units**, with ROI decreasing from 22.7% to 20.6%. The added totals are 7–3, but five removed bets went 4–1, so the incremental gain is small.

Selected spreads fall from 26–12 and +11.64 units to 18–13 and +3.36 units: **−8.27 units**. The larger changes remove many profitable baseline qualifiers. The stronger spread adjustments therefore do not have support from this replay under the existing B-only selection rule.

Below a 20-point market spread, the baseline is 15–8 and +5.64 units; the selected candidate is 12–9 and +1.91 units. The larger version does not preserve the earlier unconstrained research subgroup's apparent advantage. These are different methods, and the records should not be merged.

## Continuous UConn–Arizona example

The full history shows Over money share falling from 85% to 31%, then rebounding from 31% to 66% in the final 25 minutes. Holding the model to the opening-to-final 85 → 66 comparison misses that reversal. The example below uses the declared fixed balanced profile and, at each snapshot, the latest baseline forecast inputs already captured by that time. It is an illustration, not a claim that a later calibration was available earlier.

| Nov 18 observation | Total line | Over tickets | Over money | Baseline total | Updated total |
|---|---:|---:|---:|---:|---:|
| Nov 18 06:40 PM ET | 147.5 | 22% | 31% | 149.1 | 144.3 |
| Nov 18 08:15 PM ET | 146.5 | 22% | 31% | 149.1 | 143.3 |
| Nov 18 08:40 PM ET | 146.5 | 26% | 66% | 149.1 | 152.2 |

At 8:15 PM, the line falls with unchanged splits and lowers the projection. At 8:40 PM, the large new Over-money share raises it again. The new final projection is 152.2, versus the 149.1 baseline. At the saved 146.5 line, that still does not reach the eight-point total eligibility threshold.

The visible timeline checks every underlying baseline, rating and market timestamp against the current update. No later model inputs or final score are used in those projections. A fresh underlying baseline can replace the earlier baseline, while an out-of-order older baseline cannot replace a newer one.

## Status and validation

- 40 targeted tests passed, including the eight approved examples under all three profiles and the 27 basic movement states.
- 550 game/market comparisons and 2,750 prediction rows use matching quotes and decisions.
- 749 projections using the original fixed baselines are emitted only after those baseline inputs were available; a separate full UConn timeline uses fresh, earlier captured baselines at every tick.
- Every active correction is at least one point, with the declared market-specific cap.
- All source hashes and baseline summary parity checks passed.
- No production import, official-pick eligibility, database write, deployment or scheduled task is added.

The candidate is saved for review. Totals show a small exploratory gain in units, while stronger spreads underperform. The floor, three presets, timing windows and selection policy are design choices tested on overlapping historical research. A correctness fix followed the initial screen. None of these returns establishes a sustainable future edge.

The preceding v1 implementation and its smaller corrections remain available at commit faf86895157c2ab3386179841d3af5974525519f. The dated v1 aggregate is preserved separately.

## Reproduction

Extract the original NCAAM_Market_Adjustment_Data_2026-11-22.zip with its internal paths intact. From this repository root:

    python3 -m unittest discover -s research -p 'test_ncaam_market_flow.py' -v
    python3 research/replay_ncaam_market_flow.py --input-root /path/to/extracted-package

Private working rows and generated trajectories go under the ignored research/results/ncaam_market_flow_v2 directory. The compact review is research/results/ncaam_directional_market_flow_v2_2026-11-22.json. The design is config/ncaam_market_flow_research.json. The scoring module and MarketProjectionState updater are pure research calculations and do not poll a live service themselves.
