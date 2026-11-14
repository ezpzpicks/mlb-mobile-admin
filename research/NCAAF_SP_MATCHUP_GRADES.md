# Published SP+ matchup grades for NCAAF

The November 13 v2 policy adds Ryan's explicit
[selection preferences](NCAAF_SELECTION_PREFERENCES.md). The original v1 policy
below remains available for historical replay; v2 captures preserve its point
model, source/clock and price requirements while adding the review exclusions.

The new policy is `ncaaf-sp-matchup-2026-11-13-v1`. It replaces the old
point-gap-only A/B ladder for newly evaluated spreads and totals. The numerical
score projections, simulation and personnel/weather adjustments are retained.
Official EZPZ Picks still require a separate explicit manual selection.

## Inputs

- Bill Connelly's published SP+ offense and defense, obtained from the public
  CFB Update SP+ table. These are adjusted scoring ratings in points: higher
  offense and lower defense are better. The legacy free-data dispatcher's
  `SP+ Rating` field is an internal proxy and is **not** used by this grader.
- Pace comes from actual SportsDataverse play-by-play metrics through the prior
  week, using the existing four-game prior/current-season blending scheme.
  Team tempo is 0.58 × possessions per game + 0.42 × plays per game / 5.75.
  This is pace, not an opponent-adjusted efficiency rating.
- Offense and defense are standardized against the complete published FBS
  population. Tempo is standardized against that population's available real
  pace inputs, requiring at least 100 FBS teams.

The input smoke check parsed 138 published FBS teams and matched real prior-season
pace for all 138. The smoke check tests feed compatibility, not betting performance.

## Grading

Totals first take their direction from the saved point projection versus its
paired market total. A requires one team's offense, defensive points allowed
and tempo all to support that direction by at least 0.5 population SD, and mean
matchup tempo to agree. B requires favorable offense-versus-opposing-defense
pairings in both directions with supporting mean tempo, or one strongly favorable
pairing with extreme supporting tempo and no strong opposing pairing. A larger
point gap alone does not improve a grade. A projection exactly on the line is
Research; the grader never flips to the opponent to find a grade.

Spreads take the projected **cover** side, so a projected losing underdog can
qualify. A requires relative offense and defense advantages of at least 0.5 SD;
B requires at least one relative advantage. An extreme role/tempo conflict
removes eligibility: a favorite in a matchup at least one SD slow, or an
underdog in one at least one SD fast. Spreads of 20 points or more remain
projection-only.

Both markets retain the actual saved-price positive-edge and positive-EV checks.
A price-selected side opposite the mean projection cannot receive an A/B grade.
Missing source data, unknown kickoff or an evaluation after kickoff yields
Research without changing the numerical prediction.

## Evidence and limits

The earlier October 30 saved-model audit found that current-version totals with
6+ point gaps went 12–22, versus 34–28 for gaps of 3 to under 6 and 47–34 below
3. That justified reconsidering the old grade ordering. Those are records of
the **old** model selections, not results for the new SP+ policy.

No dated published SP+ pregame populations were retained for those earlier
forecasts. The ratings fetched now must not be assigned to past games. Therefore
this policy's 0.5/1.0 SD profile boundaries and tempo-role rules are explicit
**provisional design choices**, not thresholds claimed to have won a historical
backtest. They follow the three-factor framework requested for NCAAF. A/B labels
describe support strength and are not calibrated win probabilities.

Every new slate captures the paired factors, source URL, actual observation time,
population hashes, national benchmark, policy hash, decision time and kickoff.
The tracker retains `Grading Policy Version`. The site displays a separate new
policy record and filters new-play form badges to this policy. Previous
forecasts, outcomes and existing record totals are not relabeled or erased.
The source fetch has a six-hour cache; an observed cached snapshot may be reused
for at most eight days during a temporary feed outage. It cannot be backdated.

## Verification

```sh
python -m unittest research.test_cfb_matchup_grades -v
PYTHONPATH=. python research/test_cfb_research_snapshot.py
PYTHONPATH=. python research/test_cfb_schedule_adjustment_window.py
```

The public repository adds source/clock validation and independent new-policy
record tests. Its existing manual EZPZ approval tests remain part of verification.
