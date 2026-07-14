# EZPZ Multi-Sport Admin

This package keeps one Streamlit/Render admin URL while isolating each sport in its own file.

## Entry point

Continue using:

```bash
streamlit run app_mobile_admin.py
```

## Files

- `app_mobile_admin.py` — login, sport menu, routing
- `builders/mlb_builder.py` — preserved July 13 MLB production model with only its duplicate page configuration/login header removed
- `builders/cfb_builder.py` — CFB spread, moneyline and totals foundation model
- `builders/nfl_builder.py` — NFL spread, moneyline and totals foundation model
- `builders/cbb_builder.py` — college basketball rotation-aware foundation model
- `shared/` — authentication, Google Sheets storage, common UI and probability helpers
- `MLB_PRODUCTION_BACKUP_2026-07-13.py` — untouched uploaded MLB file

## Existing secrets

The new app uses the same secrets as the MLB app:

- `ADMIN_PASSWORD`
- `ADMIN_COOKIE_SECRET` (recommended)
- `GOOGLE_CREDENTIALS`
- `GOOGLE_SHEET_NAME`

Optional CFB sync:

- `CFBD_API_KEY`

## New Google Sheets tabs

The app creates these tabs when they are first used:

- `cfb_team_ratings`, `cfb_daily_slate`, `cfb_bet_tracker`
- `nfl_team_ratings`, `nfl_daily_slate`, `nfl_bet_tracker`, `nfl_schedule`
- `cbb_team_ratings`, `cbb_daily_slate`, `cbb_bet_tracker`

The original MLB tabs are unchanged.

## Important model status

MLB remains the production model. CFB, NFL and CBB are v0.1 foundation engines. They are functional for building matchups, saving projections and tracking shadow bets, but their coefficients and thresholds must be backtested and calibrated before publishing or staking real units.
