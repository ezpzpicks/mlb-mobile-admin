# NCAAM automated forecasts — 2026-11-01

The manual v0.1 draft was not a completed production model. Its defensive eFG
term is corrected in v0.1.1. The automatic v0.2 baseline is a separate pure model
in `builders/ncaam_model.py`; it uses real, dated adjusted offense, defense and
tempo instead of silent default teams. It does not double-count raw four-factor
or SOS adjustments already summarized by adjusted efficiency.

The existing Render dispatcher now includes `scripts/run_ncaam_builder.py`.
No new service, cron minimum, schedule or paid data account is added. Set
`NCAAM_AUTO_BUILDER_ENABLED=0` to disable its dispatch; the scheduled child
receives the existing explicit save opt-in. An individual manual `--commit`
also requires `NCAAM_AUTO_BUILDER_ENABLED=1`.

The runner filters the ESPN scoreboard to matchups where both team IDs belong
to the verified season-specific Division I membership snapshot. The 2026–27
snapshot matches the 365 Warren Nolan teams to ESPN IDs, including Lindenwood,
Queens and Southern Indiana. Nonmembers are excluded before market, ratings
and lineup work. The public site uses an identical snapshot. Each new season
requires its own verified membership file; unknown-season games are excluded.

The runner reads the complete ESPN Division I scoreboard for the current
Eastern date, including the Rome opener, neutral courts and postseason games.
It only projects scheduled games at least ten minutes before verified tipoff.
One immutable early forecast and one forecast inside 90 minutes are saved per
model version/game. An ordinary repeat does not rewrite either forecast.

Spread/total inputs prefer saved ScoresAndOdds quartets, then current bookmaker
lines from the same ESPN schedule response, then the existing Covers adapter
once per slate only for still-uncovered games. ESPN quotes preserve bookmaker,
source URL and HTTP observation time. Current spread/total sides must agree;
opening quotes and moneyline-only entries cannot fill missing current sides.
No extra ESPN request is needed, and prices remain assumed -110. Team
orientation is reversed when necessary at a verified neutral site, including
Rome. ESPN lines and Covers percentages never enter public split history. Missing lines or
ratings leave an explicit waiting/skipped result rather than a default projection.

Torvik's current ending-year headered public ratings export supplies adjusted
efficiency and tempo. Save the actual observation timestamp and reuse the bundle
for at most six hours; never use an end-season snapshot to backfill earlier games.
The public collector separately saves Warren Nolan team ELO and current RPI SOS
for basketball, correctly labeled, rather than using football's ELO SOS metric.

Forecasts and inputs use date-indexed NCAAM datasets `ncaam_model_forecasts` and
`ncaam_rating_inputs`. Forecast transactions are atomic and idempotent. Admin
Automated Slate and public Full Slate show the same saved calculations. Manual
draft results retain their own version and storage; they are not overwritten.

The automatic forecasts have Research grades and no claimed calibrated
probabilities. A/B grades require NCAAM
chronological validation, and official EZPZ picks require a separate dated NCAAM
combo-score threshold. Launching collection is not evidence of betting accuracy.

Validation:

```sh
python -m unittest discover -s research -p 'test_cbb_projection.py' -v
python -m unittest discover -s research -p 'test_ncaam_model.py' -v
python scripts/run_ncaam_builder.py --evaluate
```

The first live read-only trial produced a neutral-site Notre Dame–Villanova
forecast using actual current-season ratings and Covers lines. The public SAO
adapter also matched its quartet after correcting neutral-site source order.
Liberty–High Point was explicitly waiting for lines at the time of that check.

## v0.3: completed-season prior and college player rotations

The opening 2026–27 slate uses the completed 2025–26 season (Torvik ending year
2026) as its team efficiency/tempo prior, with actual current-season W–L game
count determining the weight: current results receive `n / (n + 10)`. At 0–0,
the prior receives 100%; after ten games, each season receives 50%. The ten-game
setting is provisional research, not a tuned or calibrated parameter. A school
without a preceding-season rating retains its actual current rating and the
missing prior is visible. Offensive and defensive adjusted efficiencies already
account for schedule strength and competition. SOS and opponent OE/DE are saved
for inspection; they are not applied again as an arbitrary multiplier. Each
offense is matched multiplicatively against the opposing defense, scaled by the
league efficiency and the teams' tempos. Neutral court verification remains
required. Market lines do not determine projected scores.

`builders/ncaam_lineups.py` reads public RotoWire college depth charts from
`https://www.rotowire.com/daily/ncaab/lineups.php`, following at most two additional
advertised, date-matching slate links. College cards are projected depth charts,
never NBA-style confirmed fives. Every card must match both ESPN schools and the
explicit Eastern game date/time, including the November DST transition. Repeated
position slots are deduplicated and incomplete fives stay incomplete. GTD becomes
Questionable, OUT/OFS becomes Out. Blank status remains Unknown. Missing source
coverage does not mean healthy. No subscription minutes are scraped.

`builders/ncaam_player_impact.py` applies the NBA minute-delta mechanics scaled
to 40 minutes per player and 200 regulation team minutes. College player
coefficients are Torvik college OBPM/DBPM, not NBA RAPM. The publicly documented
67-column export at `getadvstats.php?year=2027&csv=1` is validated for season,
unique identities, finite fields and reconciled offensive/defensive BPM. Players
with under 1% minute share or no games are excluded as insufficient exposure.
Current-year preseason player profiles can carry earlier statistics and are
not counted as newly played games. Source roster minute shares are model weights,
not RotoWire projected minutes. Missing/new player ratings are explicit gaps.

The reference rotation blends prior-school and current-school exposure using
the same season weight. Current-school player IDs determine the active roster;
departures lose minutes and transfers carry their prior college coefficients
where available. OUT/Doubtful players receive zero minutes and evidenced
replacements fill the remaining allocation with a 40-minute cap. Questionable
players remain IN in the base forecast, with separate one-player OUT scenarios.
For each player, `minute_delta / 40 * OBPM` changes offense and the analogous
DBPM value improves (lowers) defensive points allowed. No unverified rookie or
missing replacement receives an invented coefficient. Insufficient rotation
coverage preserves the team baseline with a flag.

Player inputs cache for six hours; completed-season inputs for at most 30 days.
RotoWire observations must be within 35 minutes, tightened to ten minutes inside
90 minutes to tipoff. Both the existing public collector and Python builder save
one date-indexed `lineup_snapshots` row; public page visits only read saved data.
The builder reuses a fresh saved observation before fetching. A material lineup,
roster or ratings revision adds an immutable forecast keyed by the input digest;
an observation timestamp alone does not create another forecast. Latest per-game
selection keeps one active forecast for records and signals; earlier versions
remain intact. Official NCAAM EZPZ picks and X posting remain on hold.

Behavioral validation includes the actual RotoWire Rome card, repeated Villanova
slots, injury deduplication, ambiguous matchups, stale availability, replacement
minutes, both sides of player impact, prior transitions and departures. These
checks establish input/math behavior; predictive accuracy still requires a
chronological held-out evaluation.
