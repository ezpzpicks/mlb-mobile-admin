# NCAAM matchup-led scores and point-edge grades

Current model: `ncaam-v0.12-rebound-percentage-2026-11-17`.
Archived capped-rebounding model: `ncaam-v0.11-rebound-transfer-2026-11-16`.
Archived pre-rebounding model: `ncaam-v0.10-progressive-offense-2026-11-16`.
Archived offense-bonus model: `ncaam-v0.9-progressive-offense-2026-11-15`.
Archived adjusted-tempo model: `ncaam-v0.8-adjusted-tempo-2026-11-15`.
Archived point model: `ncaam-v0.7-matchup-projection-2026-11-14`.
Grading policy: `ncaam-point-edge-2026-11-14-v1`.

## November 17 continuous percentage rebounding transfer

Version 0.12 implements the user's percentage-based replacement for the
capped rule. The two-way rebounding edge retains the same definition:

`away matchup = (away OR% + home OR% allowed) / 2`

`home matchup = (home OR% + away OR% allowed) / 2`

`home edge, pp = 100 * (home matchup - away matchup)`

The side with the positive edge receives a percentage of its own projection:

`boost fraction = abs(edge, pp) * (0.15 / 20)`

`requested transfer = favored pre-rebound score * boost fraction`

`favored score += transfer; opposing score -= the identical transfer`

The pre-rebound score includes the existing efficiency, pace, court, lineup
and progressive-offense effects. The percentage replaces the v0.11 transfer;
it is never stacked on its three-point cap. It applies to every offense
category with verified rebound inputs, with no game-count attenuation, edge
tiers, minimum positive edge or fixed point cap. Full precision is retained
until display. A zero edge leaves both scores unchanged.

| Rebounding edge, pp | Boost | Points added to 85 | Points added to 60 |
|---|---:|---:|---:|
| +0.1 | 0.075% | +0.06375 | +0.045 |
| +2.5 | 1.875% | +1.59375 | +1.125 |
| +10 | 7.5% | +6.375 | +4.5 |
| +17.5 | 13.125% | +11.15625 | +7.875 |
| +20 | 15% | +12.75 | +9 |
| +100 | 75% | +63.75 | +45 |

The examples assume sufficient opposing score for the transfer. The only
post-transfer boundary is score validity: the applied transfer cannot exceed
the opponent's projected points. When that boundary is reached the opponent
finishes at zero, and the other team receives exactly those points. The proof
records both requested and applied amounts and whether the floor limited the
request. There is no substitute fixed point cap or arbitrary upper edge band.

An 85–60 baseline with a +20 edge for the 85-point side becomes 97.75–47.25;
the game total remains 145 and the projected margin becomes 50.5. If the
60-point side has that edge instead, it gains 9 and the other team loses 9,
producing 69–76 in that team's order. At +100, an 85–60 baseline would request
63.75 but transfer 60, producing 145–0 and preserving the same total.

Version 0.12 carries the pre-transfer total exactly, preserving full-game
O/U point edges and grades for the same inputs. Missing, stale, future-dated
or invalid rebound inputs remain neutral. A valid rate still requires at
least one verified prior D1 game for each team; no invented default is used
when a team has zero games. Source validation, strict prior-date filtering
and the captured input provenance retain the v0.11 rules described below.

The percentage policy has its own model and scoring-proof version. Archived
v0.11 retains its original cap and GP discount. Seven frozen real-input cases
store their v0.11 hashes and scores from before this change. Replays preserve
all older versions; active percentage forecasts are reconstructed from their
baseline inputs, preventing double application or rehashed transfer edits.

The same read-only evaluator supports both rebound versions through
`--model-version`. The new comparison is
`research/results/ncaam_rebound_percentage_replay_2026-11-16.json`; the capped
comparison below remains unchanged. These are retrospective prior-date input
reconstructions, and no target outcome selects the percentage anchor.

## November 16 archived total-neutral rebounding transfer

The archived v0.11 experimental modifier applies across offense categories.
It compares the two offensive-glass matchups and transfers points from the
unfavorable side to the favorable side. It does not add total points.

`away matchup = (away OR% + home OR% allowed) / 2`

`home matchup = (home OR% + away OR% allowed) / 2`

`home edge, pp = 100 * (home matchup - away matchup)`

`capped home transfer = clamp(0.2 * home edge, -3, +3)`

`sample weight = min(1, minimum prior D1 games of the two teams / 3)`

`home score += capped home transfer * sample weight`

`away score -= capped home transfer * sample weight`

The coefficient and cap are conservative initial trial settings, not fitted
or validated weights. Full-weight edges of 5/10/15 percentage points transfer
1/2/3 points per team, changing the margin by 2/4/6 points. The cap is applied
before sample attenuation: one prior game permits at most one transferred
point per team; two games permit at most two. The rate score is not a
calibrated rebound or win probability. No result-based outlier exclusions or
offense-strength eligibility gates are used.

The pre-transfer total is carried exactly, so over/under projections, point
edges and grades are identical to v0.10 for the same other inputs. Home court,
player effects and the existing progressive offense bonus are each applied
once before the transfer. Spread grading then uses the revised margin and
the existing unchanged thresholds.

The existing verified D1 game-log download also supplies rebound counts;
there is no additional provider request. Cache policy
`ncaam-d1-completed-games-venues-rebounds-v3` refreshes older count-only caches.
Only reciprocal, agreeing D1 boxscores from strictly earlier Eastern dates
contribute. OR% is summed OR / (summed OR + summed opponent DR); allowance is
summed opponent OR / (summed opponent OR + summed own DR). Official source
team rebounds are treated consistently. Same-day outcomes, non-D1 opponents,
unpaired boxes and conflicting source counts cannot supply a modifier.

Both team profiles retain source URL, snapshot hash, season, observation
clock, cutoff, latest game date, GP and raw rebound counts in the captured
forecast. Profiles must be for the correct schools and season, share one
snapshot, precede forecast lock, and be no more than 30 hours old. Missing,
invalid or zero-game inputs leave the original margin intact with an explicit
unavailable proof. The rule never invents a fallback rebound rate.

Captured v0.11 scores include the transfer and its full input proof. Grade
validation reconstructs all scoring adjustments, so rehashing a doubled,
reversed or otherwise edited transfer cannot make it eligible. Archived
v0.7–v0.10 calculations ignore the new modifier and remain reproducible.
Existing started-game captures and official manual selections are retained.

The frozen-input comparison covers 22 November 16 games. Twenty-one have
usable rebound pairs; Missouri State has no prior D1 game. Every total and
total grade is unchanged. For Maryland/Alcorn, v0.10's 106.688–64.126 becomes
109.531–61.283; both total 170.814. Georgia/Jacksonville changes from
90.989–67.850 to 92.501–66.338, with its two-game opponent reducing the weight.
Kent State's one-game opponent limits its transfer to one point per team.

Tarleton/Drake's rate edge favors Drake, so the transfer lowers Tarleton's
projected score from 75.251 to 73.891 and raises Drake from 77.573 to 78.933.
At the user's manual Tarleton +9.5 line, the projected spread edge falls from
7.178 to 4.458 points. It weakens this selection; a good final result does not
reverse the pregame rebound edge. The replay's paired saved quote was +8.5;
the official +9.5 pick and its outcome are not rewritten.

The read-only evaluator is `research/evaluate_ncaam_rebounding.py`; the
comparison is `research/results/ncaam_rebound_transfer_replay_2026-11-16.json`.
Features are retrospective reconstructions from strictly prior-date boxes,
with the later actual download provenance retained separately. They are not
claimed as forecasts issued at those historical pregame times. No target
results choose the transfer coefficient, cap, sample weight or direction.

## November 16 defensive-tier bonus revision

Only very strong offenses (offensive z >= 1.5) receive these additional team
score points, based on the opposing defense's captured national band. Lower
adjusted defensive efficiency is better. These are user-selected trial amounts,
not fitted coefficients or a claim of validated betting improvement.

| Opposing defense | Archived v0.9 bonus | v0.10–v0.12 bonus |
| --- | ---: | ---: |
| Very strong (z <= -1.5) | +1 | +1 |
| Strong (-1.5 < z <= -1) | +3 | +3 |
| Above average (-1 < z < 0) | +5 | +5 |
| Below average (0 <= z < 1) | +8 | +5 |
| Weak (1 <= z < 1.5) | +9 | +8 |
| Very weak (z >= 1.5) | +10 | +15 |

The bonus enters the qualifying team's score once, flowing to the total and
spread before the existing point-edge grading thresholds are applied. Offenses
below the qualifying threshold receive zero additional points or deductions.
Pace, player adjustments, home court and grade thresholds retain their existing
construction. Saved v0.9 forecasts reconstruct and validate with their original
bonus schedule and policy identifier; v0.10 uses a distinct model and policy.
The pregame builder appends the new model's captures for eligible unstarted
games. Replays do not revise historical forecasts or create official picks.

Using November 16's last saved pregame inputs as a comparison, Maryland's
forecast against Alcorn changes from 101.688 to 106.688, with the total moving
from 165.814 to 170.814. Ohio State against Prairie View changes from 98.300 to
95.300, with the total moving from 164.397 to 161.397. These are comparisons
under the revised settings, not forecasts issued before those games.

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
remain comparison metadata. SOS, manual four-factor adjustments and preference
ratings do not create generic bonuses on top of opponent-adjusted efficiencies.
The separately captured v0.11/v0.12 rebound transfers above change only the margin.

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
