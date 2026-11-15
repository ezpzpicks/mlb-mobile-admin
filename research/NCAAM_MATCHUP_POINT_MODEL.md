# NCAAM matchup-led scores and point-edge grades

Model: `ncaam-v0.7-matchup-projection-2026-11-14`.
Grading policy: `ncaam-point-edge-2026-11-14-v1`.

The November 14 NCAAF workflow is now applied in basketball's own units. Each
offense faces the opposing defense, matchup tempo determines possessions, and
the line is compared only after projecting both team scores. This is EZPZ's
provisional construction, not Torvik's proprietary game projection formula.

## Score construction

Let O and D be the captured Division I national means of adjusted offense and
defense, and C = (O + D) / 2. Expected possessions are the mean of both teams'
captured adjusted tempos. For each team:

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
remain available only for their original captures. The shared new policy keeps
20+ point spreads projection-only. Numerical edge grades, eligibility and input
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
