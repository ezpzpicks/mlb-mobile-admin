# NBA chronological research check — 2025–26

The lineup version **did not improve held-out margin RMSE** against the team-only control.
These are retrospective score predictions with dated inputs and a stated availability
lag. There is no historical injury-report archive, current-lineup validation,
market-line archive, ATS record, betting profit, probability calibration or A/B grade.
Every output remains research-only and ineligible for posting.

## Coverage and independent checks

- Team baselines and minutes forecasts: 1230 of 1230 scheduled regular-season games passed box-score and team-minute checks.
- Player fit: 471 of 1230 games passed event-lineup checks. 759 were excluded; the audit JSON lists each reason and game ID.
- Accepted player games reproduce every player's box plus/minus exactly. Clock-derived minutes differ from boxes by at most 0.500 seconds (limit 2 seconds).
- Free throws use the most recent same-clock foul lineup. This convention is checked against independent player box plus/minus before a game enters the fit.
- Possessions are an explicit box estimate: average both sides' FGA + 0.44×FTA − player OREB + TOV. Counted-possession exposure is shared across distinct event lineups and rescaled to that game estimate.
- The player sample excludes many source games and may be unrepresentative. Team baselines and held-out outcomes do not depend on those lineup exclusions.

## Date split and selection

- Candidate ratings use only games available before midnight ET on 2026-01-01.
- Validation: 2026-01-01 through the day before 2026-03-01; 399 evaluated games.
- Five player penalties (150, 500, 1500, 5000, 15000) × two past-minute windows (5, 10 games); fixed team penalty 100.
- Selection uses validation margin RMSE, then total RMSE, stronger shrinkage, and shorter window. Chosen player penalty 15000; window 10.
- The selected model is refit before midnight ET on 2026-03-01 using 890 team games and 306 event-verified player games.
- Holdout: 2026-03-01 through the day before 2026-04-13; 335 of 335 evaluated games. Hyperparameters are not chosen from this period.

| Model | Margin MAE | Margin RMSE | Total MAE | Total RMSE |
| --- | ---: | ---: | ---: | ---: |
| Team only | 12.120 | 15.262 | 14.883 | 18.770 |
| Team + past-minute lineup forecast | 12.133 | 15.280 | 14.885 | 18.784 |

## What the evaluation can establish

The control uses opponent/venue-adjusted team ratings. The lineup model adds
regularized offensive and defensive player effects relative to the exact box
sample's reference rotation. Forecast minutes are the prior 5/10-game mean,
restricted to the latest known box roster, normalized to 240, with a 48-minute cap.
No target-game minutes, target-game roster or later results enter its prediction.
An unseen player has an explicit prior-only estimate and zero fitted sample.

Dates in the source are game dates, not publication times or tipoff timestamps.
The explicit availability rule is **next-day noon America/New_York**. Each
prediction uses midnight ET at the start of its game date; the engine receives
a placeholder noon tipoff only to enforce pregame input order. It is not an
archived game-start time. Later stat corrections in today's archive cannot be
dated retrospectively. Final scores include overtime; minutes/pace are normalized.
Report statuses are UNKNOWN and lineups remain conditional. This does not test
the benefit of verified injury reports or confirmed starting lineups.

## Saved ratings

The final manual-research bundle uses 1230 team games and
471 player games through **2026-04-12**,
with 30 teams. Its final refit includes holdout outcomes for
future research; the reported holdout metrics come from the separate frozen
pre-holdout bundle. These are prior-season inputs, not current roster, injury or
lineup assertions. Verify and edit the full rotation before using a matchup.

No collector, refresh schedule, paid feed, deployment or public pick writer is added.
The manifest records source URLs, SHA-256 file hashes, cutoffs and parameters.
