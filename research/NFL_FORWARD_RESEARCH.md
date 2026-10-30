# NFL forward research — 2026-10-30

The 2026 retrospective search found promising candidates but no established independent edge. The deployed improvement is a prospective, append-once comparison using actual future pregame builder saves. Candidate rows do not enter official picks, records or posting feeds.

## Frozen candidates

- Totals weighting: saved total + 0.5 centered scoring contribution − 0.5 centered success contribution + 0.5 full weather contribution + net injury contribution + 2 points; minimum 1.75-point gap. The full league center must be present; missing inputs are skipped.
- Injury alignment: minimum 0.5-point gap; net defensive absence minus offensive absence must support the projection direction.
- RB receiving: matchup confidence ≥80 and projection gap ≥10%; choose the projection-gap direction with its own saved price, requiring both prices. An availability hold excludes the row.

Parameters, formula coefficients, capture policy and a review floor of 50 distinct graded games across at least four NFL weeks are stored with an immutable policy hash. A changed policy requires a new version. There is no automatic promotion or retuning.

## Capture and accounting

Automatic capture runs in the same successful pregame transaction as the official model. Early previews and late changes cannot replace that research cohort. Successful scheduled manual saves can also append the first capture. Test games, elapsed kickoffs, old dates and stale/mismatched policies cannot be backfilled.

The original projection, two team ratings, full-league center, selected/skipped decisions, market line, side-specific odds, source model, input hash and capture/kickoff times are retained. Assumed −110 game prices, manual inputs and recorded prop prices are labeled. Research weight changes do not receive invented win probabilities.

Each selected candidate is compared with the original model and simple Over/Under or home/away benchmarks. Same-game comparisons exclude unrelated games and unresolved outcomes. Strategies overlap; their returns cannot be summed as independent bets. Brier scores compare original supplied model probabilities with subsequent settled outcomes.

## Settlement and cost

The existing 30-minute builder service settles pending research outcomes. Reads use a policy/selected/result/kickoff index and a 21-day pending window. A game must have started at least four hours earlier and have published final scores; explicit live/incomplete states are rejected. Props require a unique player/week/team stat match and confirmed participation. Unconfirmed zero-yard/DNP cases stay unresolved for review rather than becoming automatic Under wins or assumed voids.

Only result, unit and settlement columns can change after capture. Forecast payloads remain unchanged. Capture is append-once, replaying settlement is idempotent, and policy/schema initialization is idempotent. No extra play-by-play computation, external data subscription, service or cron schedule is added. Candidate evaluation reuses the already-loaded builder inputs; approximately tens of small rows are saved once per game.

Open the existing admin, choose NFL, then Research. The page shows prospective records, pending results, unique game/week counts, matched benchmarks, price quality and model confidence, plus frozen-row exports. Historical test records never count toward forward review progress.

## Validation

Run `python -m pytest research/test_nfl_research.py research/test_nfl_automation.py -q`, then the repository's NFL automation/regression workflow. Coverage includes formula centering, projection-vs-distribution direction, missing data, availability, duplicate quotes, pregame/UTC boundaries, rollback, immutable forecasts, policy version enforcement, push/price accounting, participation holds, matched baselines and the dashboard.
