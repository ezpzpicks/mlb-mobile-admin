# Configurable NCAAM matchup total grades

The matchup grader selects an Over or Under from adjusted offense, adjusted
defense and tempo combinations. It does not require the numeric model to agree
and does not use a minimum model point edge. Every grade retains the qualifying
profile, normalized inputs, market context and downgrade reasons.

The automated NCAAM builder captures a grade alongside each new pregame forecast.
`builders/ncaam_model.py` retains its independent point projections and spread
behavior. The public board reads the saved grade, settles its selected side and
counts it in model records. Only live grades published before the decision can
contribute a model signal; the existing EZPZ selection rules still apply.

`builders/ncaam_matchup_live.py` reads the previous three days of market quotes,
retains the national benchmark and policy digest, and saves the forecast and
grade in one transaction. Material policy, population or quote changes produce
a new immutable capture. Timestamp-only refreshes do not.

## Initial policy

Edit `config/ncaam_matchup_grades.json` to adjust thresholds, enable profiles,
set A/B assignments and priorities, change market quartiles, or change penalties.
`builders/ncaam_matchup_grades.py` validates the policy and emits its version and
SHA-256 with every result. No win probability is attached to an A/B label.

The initial profile paths are:

| Direction | A paths | B paths |
|---|---|---|
| Over | One team has above-average offense, defensive points allowed and tempo; or a very fast team has a favorable offense facing the opposing defense and above-average average matchup tempo. | A fast team with the same support; or an extreme offense facing extreme weak defense with above-average average matchup tempo. |
| Under | One team has below-average offense, defensive points allowed and tempo; or a very slow team has a favorable weak offense facing a good opposing defense and below-average average matchup tempo. | Weak offense, good defense and a slow team somewhere in the matchup; or an extreme slow team with a favorable opposing offense/defense pairing and below-average average matchup tempo. |

Above/below average is relative to a dated national population. Extreme is one
population SD and very extreme is 1.5 SD. Both offense and defensive points
allowed move in the same numeric direction for a scoring profile: high/high for
Over, low/low for Under. Lower defense is better defense.

The strongest matching profile starts at its configured grade. A high total for
an Over or low total for an Under reduces it by one grade, using the 75th/25th
percentile of prior pregame market totals. One extreme fast team facing one
extreme slow team also reduces it by one grade. Strong opposing average matchup
tempo is another one-grade penalty. A -> B -> Research. These market penalties
are initial heuristic context rules, not fitted probabilities or point-edge gates.

Each direction is evaluated separately. If both finish at the same positive
grade, the game is Research. Otherwise only the stronger direction is selected.
There is at most one total selection per game. Disabled profiles cannot qualify.

## Replay and data controls

Use saved public-board JSON, the saved national input cache, and earlier-date
public-board JSON files as price references. The cache format is
`{"snapshots":[{"cache_date":"YYYY-MM-DD","bundle":{...}}]}`; each bundle has
`season`, `observed_at` and `teams` with offense/defense/tempo.

```bash
python research/replay_ncaam_matchup_grades.py grade \
  --public today_public.json --benchmark-cache national_inputs.json \
  --reference-public earlier_day_1.json earlier_day_2.json \
  --as-of 2026-11-12T03:45:00+00:00 --output frozen_grades.json

python research/replay_ncaam_matchup_grades.py settle \
  --grades frozen_grades.json --scoreboard espn_scoreboard.json \
  --as-of 2026-11-12T03:52:16+00:00 --output settled_results.json
```

The grade command does not read a scoreboard or use stored results. It selects
one latest valid pregame forecast per game, uses only national populations and
price references observed before the forecast, and records source hashes.
The settlement command cannot alter frozen selections. It verifies ESPN team
IDs and scheduled tipoffs, settles completed games including overtime, and keeps
live games pending. Units assume one unit risked at -110.

The November 11 demonstration uses that day's saved pregame features and lines,
national snapshots dated November 7-11, and 119 prior market totals from November
8-10. Its initial policy digest is
`82fe89a811f37c7b87fa3586bd31dfc057bf6b03744ae547d868bdf4e2c60679`.
The initial policy is frozen before this replay is settled and is not adjusted
to fit its results. However, November 11 was included in the preceding exploratory
analysis, so this demonstration is retrospective, not an independent validation.
The private input cache and connection credentials are not included in this repo.

`scripts/save_ncaam_matchup_replay.py` can annotate saved forecasts with explicit
`record_mode: replay` grades. It requires the reviewed policy digest, defaults
to a dry run, verifies source capture identities before saving, and writes only
the separate `ncaam_matchup_total_grades` dataset. It never edits source forecasts.
The site keeps replay records separate from live model records and replay grades
cannot create pregame model signals or retrospective official picks.

## Verification

```bash
python -m unittest research.test_ncaam_matchup_grades research.test_ncaam_matchup_live research.test_ncaam_model -v
```

Tests cover directional grading, configurable rules, market-line effects,
opposing-grade ties, model-edge and score independence, pregame snapshot choice,
future/stale inputs, historical price filtering, team identity, and pending
settlement. The existing numerical model is intentionally not changed.
