# NCAAM matchup-led scores and point-edge grades

Current model: `ncaam-v0.8-adjusted-tempo-2026-11-15`.
Archived point model: `ncaam-v0.7-matchup-projection-2026-11-14`.
Grading policy: `ncaam-point-edge-2026-11-14-v1`.

The November 14 NCAAF workflow is now applied in basketball's own units. Each
offense faces the opposing defense, matchup tempo determines possessions, and
the line is compared only after projecting both team scores. This is EZPZ's
provisional construction, not Torvik's proprietary game projection formula.

## Score construction

Let O and D be the captured Division I national means of adjusted offense and
defense, and C = (O + D) / 2. Let T be the captured national mean adjusted
tempo. Expected possessions combine both teams' relative adjusted tempos:

`possessions = T × (away tempo / T) × (home tempo / T)`

The archived v0.7 formula used their arithmetic mean. That halves each team's
departure from average when the opponent is average, and understates the
combined pace effect when both teams are slow or fast. Version 0.8 keeps
offense-versus-defense efficiency construction unchanged. For each team:

`expected efficiency = C + (team offense - O) + (opponent defense - D)`

`neutral points = expected possessions × expected efficiency / 100`

The captured home-court value adjusts the margin once, split equally between
the two scores. Neutral courts use zero. Captured player offense and opposing
defense changes are converted from points per 100 possessions to scoring points
once. The earlier multiplicative baseline and opening-margin strength scaling
remain comparison metadata. SOS, raw four factors and preference ratings do
not create extra bonuses on top of opponent-adjusted efficiencies.

The [ratings glossary](https://kenpom.com/blog/ratings-glossary/) documents the
standard per-100-possession efficiency and per-40-minute tempo units. Production
inputs remain Bart Torvik's dated source exports, including Torvik's own
preseason prior; the completed prior season is context rather than another blend.

Pomeroy's [historical tempo prediction explanation](https://kenpom.com/blog/bracket-breakdown/)
uses a product of adjusted tempos divided by a national baseline. Applying
that construction to the captured Torvik national mean is EZPZ's own provisional
choice, not a reconstruction of either provider's current proprietary projector.
Combined possessions outside the supported 45–95 range are rejected, never
clamped or moved across a betting line. A slow matchup can still project above
an unusually low market total.

## November 15 pace correction

The frozen-input comparison changes only pace. No coefficient, line threshold,
efficiency weight or result-based adjustment is fitted. Inputs are the latest
saved pregame ratings and matching dated national benchmark from November 11–14.
Duplicate captures are counted once. Actual pace is matched to reciprocal
Torvik game logs; final totals require matching ESPN schedule identity.

| Measure | Games | v0.7 mean pace | v0.8 combined pace |
|---|---:|---:|---:|
| Pace mean absolute error, possessions | 174 | 4.083 | 3.864 |
| Both-slow pace mean absolute error | 46 | 5.462 | 4.640 |
| Both-fast pace mean absolute error | 32 | 3.762 | 3.393 |
| Mixed-pace mean absolute error | 96 | 3.529 | 3.650 |
| Total-score mean absolute error, points | 173 | 12.623 | 12.664 |
| Both-slow total mean overprediction, points | 45 | 4.880 | 1.609 |

Overall total accuracy is effectively unchanged and slightly worse numerically;
the evidence supports better pace handling, not a proven betting improvement.
The sample covers four recent dates, not an independent season holdout. The
pace source is per 40 minutes; final scoring comparisons include overtime.
One game lacks a matching verified ESPN final identity for total evaluation.
Sixty-six other games lack the exact saved pregame national benchmark.

At the original November 15 quotes, Harvard/American changes from 141.762 to
136.200 with possessions from 67.451 to 64.805. Its Over 130.5 becomes Research
under the unchanged eight-point minimum; the revised forecast is still above
the line. Boise State/Nebraska changes from 148.875 to 148.652 against 159.5.
Its near-average pace barely changes; the Under is principally Nebraska's
strong defense suppressing Boise's above-average offense. Neither example's
result is used to construct or select the correction.

Production readers accept and validate both versions against their own pace
formula. Old hashes, scores and grades remain reproducible. New calculations
are saved only through the existing pregame builder; replays never replace
started games or create official selections. Manual-only official picks remain.

The reproducible evaluator is `research/evaluate_ncaam_tempo.py`, with the
captured comparison in `research/results/ncaam_tempo_replay_2026-11-15.json`.
Run it with `python -m research.evaluate_ncaam_tempo INPUTS... --game-stats LOG.csv --output REPORT.json`.
The original November 14 replay below describes v0.7, whose embedded
`model_version` is honored by `replay_ncaam_point_model.py`. An explicit
`--model-version` selects another supported construction for comparison.

### Additional extreme-rating effect: evaluated, not activated

Ryan proposed giving the very top and bottom of the scale an extra effect.
An exploratory, symmetric candidate multiplied each metric's departure from
its captured national mean by `1 + b × min(1, max(0, (abs(z) - 1) / 0.5))`.
Ordinary ratings stayed unchanged; the extra effect increased continuously
from one SD and reached its full size at 1.5 SD. The same rule applied to good
and bad offense, defense and tempo, using the v0.8 pace construction afterward.
Player adjustments and venue points were applied once, after the team effects.

| Extra effect at the extremes | Total-score MAE | Team-score MAE |
|---|---:|---:|
| None | 12.664 | 7.871 |
| 25% | 12.966 | 7.938 |
| 50% | 13.384 | 8.148 |

These are exploratory results on the same 173 recent games, not independent
validation. Both boost sizes worsened this sample. No such bonus is enabled.
At the saved Nebraska/Boise inputs, the 25% candidate raises Nebraska from
78.870 to 80.830 but lowers Boise from 69.782 to 67.700: total 148.530 rather
than 148.652. Amplifying Nebraska's strong offense and strong defense mostly
widens its margin. It does not supply an Over at 159.5. At Harvard/American,
the same candidate produces 134.663, still above the low 130.5 market total.

## Independent market tiers

| Grade | Spread cover edge | Total edge |
|---|---:|---:|
| A | 7+ points | 10+ points |
| B | 5 to under 7 | 8 to under 10 |
| Research | Under 5 | Under 8 |

Ryan requested a lower spread minimum after seeing that a shared 8-point
minimum produced only one spread and eight totals. At five points there are
five spreads and eight totals on the frozen November 14 slate. Four points
would produce eighteen combined candidates; six points would produce ten.
These are provisional volume choices, not thresholds optimized on winning
results or claims of equivalent cover probabilities. The full Saturday target
is roughly 10–15; qualifying plays are not added or removed to fill a fixed count.

Favorite/underdog and Over/Under use the same rules within each market. Matchup
strength already drives expected scoring and receives no second grade bonus
or veto. The former spread 1–under-4-point cap and factor-profile total grading
remain available only for their original captures. On November 16, Ryan removed
the 20-point spread exclusion. The active policy has no market-spread limit;
archived policies retain their captured limits. Numerical edge grades, eligibility and input
confidence flags are separate. NCAAM retains assumed -110 prices; a worse quote
requires separate probability evidence instead of inventing a cover probability.

## Quotes, history and mix

New quote evaluation recomputes the point tier from the saved score and
captured thresholds. It supports upgrades, downgrades, Research promotion and
the opposing side when the line passes the fair line. Source, publication and
tipoff clocks still apply. Replays cannot create pregame model signals.

The website displays actual counts and shares for favorites, underdogs, Overs
and Unders, A/B totals and forecast coverage. Repeated captures and revised game
times count once per Eastern date/game/market. Category quotas do not change
forecasts or grades. Today's concentration in underdogs among spread candidates
is visible; more slates are needed to assess persistence.

Original forecasts and old grading policies remain readable. The new model's
forward records exclude their outcomes and keep retrospective replay outcomes
separate. Existing manual-only official EZPZ selection and posting remain intact.

## November 14 frozen-input replay

There are 60 scheduled D1 games and 57 with usable saved pregame forecasts.
Latest valid captures are selected by timestamp and physical game identity,
without using final scores. All 57 replay successfully and pass the website's
score/grade provenance readers. The three unmodeled games are not fabricated.

| Type | Candidates |
|---|---:|
| Favorites | 1 |
| Underdogs | 4 |
| Overs | 4 |
| Unders | 4 |
| Total | 13 |

Five candidates grade A and eight grade B. These are retrospective formula
results at the original saved lines, not predictions published before those
games or evidence of betting profitability.

[Frozen inputs, scoring decompositions and all 13 candidates](https://chatgpt.com/library?file_id=libfile_8ca3bd5e34dc8191863014bcdef7e08b&account_id=personal)
are retained together. The replay file can itself be supplied to:

`PYTHONPATH=. python research/replay_ncaam_point_model.py --input REPLAY.json --output REPRODUCED.json`

Tests cover score units, defensive suppression, pace and underdog margin,
home-court/player overlay accounting, market/result independence, exact market
boundaries, repricing and side changes, source clocks, price and projection-only
holds, legacy policies, separate records and the rendered play-mix display.
