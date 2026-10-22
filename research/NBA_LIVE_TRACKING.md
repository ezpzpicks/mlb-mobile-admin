# NBA prospective tracking protocol

Experiment: `nba-2026-27-prospective-v1`. Initial version: `nba-v0.4-live-tracking-2026-10-22`.

This is a live research study. The preceding 335-game historical test did not show a lineup improvement over team-only ratings. Current ESPN injury inputs have no historical injury-aware accuracy result. The fitted ratings still use games through April 12, 2026; the new season is observed prospectively.

## Scheduled builds and manual workflow

Scheduled NBA builds now share the existing 30-minute Render job, 9 AM–midnight ET. One early forecast and a separate near-tipoff refresh use fixed, explicitly conditional rotation assumptions and distinct tracking versions. Results are collected automatically. See [NBA_AUTOMATION.md](NBA_AUTOMATION.md) for the exact policy, skip conditions, safeguards and operation. The manual workflow remains available below.

1. Open **NBA → Live NBA games and current rotations** in the admin. Enter an ET date and **Fetch NBA slate**.
2. Select a scheduled regular-season game and **Prepare live matchup with ESPN inputs**. This explicitly fetches two current rosters, two schedules, up to ten past game boxes per team, and one league injury response. Ordinary page rerenders fetch nothing.
3. Review the current roster / minute evidence tables, injury statuses, estimated return dates and each projected rotation. Correct the minute suggestion and any IN/OUT assumptions. All projected team minutes must total 240 and respect individual caps. Verify starters separately when known. Enter a dated market line only if available.
4. **Compare rotations**, then open **Lock first forecast for live tracking**. Select one scenario, acknowledge roster and minute review, and record the remaining assumptions. Lock the last compared forecast while its cutoff is within 60 minutes and before scheduled tipoff. A fresh game-status request and a database clock check guard against late writes. There is no retroactive lock or post-result scenario selection.
5. **Load / refresh NBA tracker**. Scheduled runs collect recent pending final results. To perform an additional manual refresh, enter the original ET tracking date and **Fetch and save NBA results for date**. Download the complete ledger when needed.

NBA scheduling is enabled through the shared builder cron. Public pick publication remains disabled. These research forecasts have no betting grades or calibrated probabilities. No sportsbook prices, stakes, or profits are assumed.

## Identity, rosters and minute estimates

The slate, roster, schedule and summary sources are ESPN public JSON under `https://site.api.espn.com/apis/site/v2/sports/basketball/nba/`. Injury details use the separately verified `injuries?limit=1000` source described in `ESPN_INJURY_SOURCE.md`. Each source observation retains its URL, observation timestamp and raw response SHA-256. Normalized roster facts, accepted game boxes, minute estimates and source failures are archived with the forecast.

Match live team names uniquely to the rated NBA teams. Match known players by unique full normalized name to the dated NBA Stats impact catalog. An unknown newcomer receives an explicit `espn:<athlete-id>` identity and a zero prior; an ESPN number is never treated as an NBA Stats number. Ambiguous rated names, duplicate current ownership, mismatched roster seasons, and selected-team injury/roster conflicts block locking. Departed players retain their fitted reference minutes but receive zero projected minutes; this preserves the rotation behind the baseline and accounts for trades without rewriting history.

Minute suggestions average up to ten **completed current-season regular-season** games, counting absences as zero. When there is no current-season evidence for a player, use the dated historical player minute seed, including his prior team if traded. A player without either source starts at zero and needs manual assignment. First-game priors are explicitly visible. Past boxes must reconcile player points with both final scores and player minutes with regulation/overtime team minutes. Rounded ESPN minutes have a bounded rounding tolerance, then normalize overtime and team totals to 240. Invalid boxes are omitted with a visible reason; they never become zero-minute healthy rosters.

ESPN OUT players receive zero minutes. The explicit prepare operation allocates the remaining positive minute weights proportionally with each player's cap; it does not invent a rotation role for a newcomer with no evidence. This is a suggestion requiring review. Insufficient capacity blocks preparation. Subsequent injury imports keep the existing manual replacement-minute behavior. Unlisted players remain UNKNOWN, with explicit IN/OUT hypotheses. Estimated return dates never establish availability. Prior-game starters never establish today's starting five. Conditional forecasts retain all unresolved flags and incomplete joint-scenario coverage.

## Fixed record and evaluation rules

- The first saved forecast per experiment, game and model version is the primary record. Database uniqueness makes a second click, different session or later projection retain the existing first lock. Later research snapshots can be examined separately; they do not replace the tracked forecast.
- Archive the complete inputs, selected scenario, every entered scenario, team-only control, current roster/minute evidence, injury source, market observation, rating bundle fingerprint, model version, review note and server lock time. A content fingerprint detects altered archive copies.
- Keep result/status observations in a separate append-only table. A correction appends a new observation; the latest source observation controls settlement. Postponed/live/unfinished games remain visible and ungraded. Wrong identities fail; a final start-time conflict is excluded explicitly. Final scores include overtime, matching the prior historical evaluation convention.
- Report each version separately: locked/graded/pending counts, conditional games, margin and total MAE/RMSE/bias for lineup and team-only predictions on the same games, winner accuracy, and spread/total WIN/LOSS/PUSH/PASS/NO LINE counts. Spread signs use home margin plus home spread; total signs use actual total minus the recorded total. Zero predicted edge is PASS. No line means no market grade.
- Actual player minutes, when checked successfully, are retained with results. Rotation minute MAE compares current-roster players who had projected or actual minutes, excluding zero/zero pairs. If a final box fails the minute check, retain the verified final score and the explicit box error, with no invented minute accuracy.
- Retain all locked games, including losses and conditional inputs. Review missing games and excluded results alongside performance; manually selected games may introduce selection bias. Report sample size before interpreting changes. Do not retune against the already-seen March–April holdout. Changes to model coefficients or minute policy require a new version and are judged on subsequent games; old forecasts remain fixed.

The optional `nba_live_bootstrap.json` contains source-observed opening forecasts sealed and committed before tipoff. They are read alongside database records, deduplicated by the same first-lock rule, and never generated from retrospective outcomes. Database read errors remain errors; an unavailable store is not displayed as an empty successful tracker.

## Runtime and verification

The Streamlit runtime consumes the saved ratings and uses its existing NumPy/pandas stack; no offline SciPy/Arrow fitting dependencies are added to deployment requirements. The existing Turso connection stores NBA research predictions and result observations in dedicated `nba_research_*` tables, with no public slate or pick writes. No environment variables or credentials are added.

Run `python -m unittest discover -s research -p 'test_nba_*.py' -q` for the behavioral suite. It covers source contracts, identities, clock/freshness guards, overtime, caps, zero priors, pregame scenario selection, ledger tampering/conflicts, market sign conventions, version separation, and append-only SQLite persistence. A real Streamlit AppTest also exercises current-input rendering, comparison, pregame locking and SQLite tracker reload. These implementation checks do not establish predictive accuracy.
