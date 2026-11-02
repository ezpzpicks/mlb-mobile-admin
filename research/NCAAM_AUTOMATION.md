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

## v0.4: source preseason weighting and independent player fallback

Current Torvik adjusted offense, defense and tempo are used unchanged. Torvik
already applies its own preseason prior and recency adjustments. Completed-season
team ratings remain context; they receive no second external team weight.
Displayed W-L never drives confidence or season weighting.

`ncaam_game_counts.py` counts unique completed games from the bulk current-season
team game log, requiring both schools to exist in the Division I ratings group.
Date-only same-day and future rows are excluded. Counts therefore run through
the previous Eastern day. A missing count is unavailable, not an all-opponent
record or an invented zero. These counts support player-profile sample weighting,
not a second blend of team ratings. The bulk count input is cached for six hours.

Player sources load independently. The completed season's full export is cached
for up to 30 days. A sparse current export is accepted if its schema and player
identities are valid, even when BPM fields are blank. Missing BPM remains null.
A current-source failure cannot suppress the completed-season fallback. Older
player-policy caches are revalidated under the v2 contract.

Current team membership comes from season- and team-ID-verified ESPN rosters.
The roster cache is read once for the required teams and refreshed at most daily,
with at most four concurrent requests and a 60-second request budget. A verified
cache remains usable for at most two days during a source outage. Only market-
covered games with fresh RotoWire reports need roster requests. Missing membership
retains the source team baseline. RotoWire remains the injury source; rosters
are not health reports. Member names must uniquely resolve to dated player
profiles; transfers can carry a uniquely matched prior college profile.

Minute deltas start from the verified current roster, never last year's roster.
Current minute shares covering at least 190 of 200 regulation minutes are used;
otherwise evidenced prior shares are projected onto current members with an
explicit flag. A projected starter without evidenced minutes prevents an impact
estimate. Missing/new impact ratings stay unavailable. If an injury would change
an unrated player's minutes, the team baseline is retained instead of assigning
that player a zero-impact coefficient. Departures alone do not create another
roster adjustment on top of Torvik's current-season ratings.

Current player impact metrics are shrunk toward a matching completed-season
profile by `n / (n + 10)`, where n is bounded by verified D1 games. A player with
no historical metric needs at least five verified games before a standalone
current metric is used. These are research sample controls, not optimized betting
thresholds. OUT/Doubtful players receive zero minutes; evidenced replacements
share the rest with a 40-minute cap. Questionable players are assumed IN, and
an OUT scenario is emitted only when the alternative can be calculated.

The bulk player files are indexed once per run, including unique name and family
keys. Matching does not repeatedly scan thousands of players for every roster slot.
Exact source aliases are tied to verified ESPN IDs; they do not use fuzzy school
matching. Forecast digests and immutable prior versions remain intact.

Validation uses the actual 19-row sparse 2027 export, verified roster metadata,
4,509 usable completed-season player profiles, all 119 eligible November 2
matchups, and the completed-season 11,504 team-game rows. This validates input
handling and timing, not predictive ROI. New matchup coefficients and betting
thresholds still require a chronological evaluation with actual historical lines.
