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
probabilities. Roster availability is unchecked. A/B grades require NCAAM
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
