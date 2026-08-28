# 2025 CFB current-production point-edge grade backtest

Current production point projection; leakage-safe weekly ratings; closing lines; no postgame QB/injury/weather hindsight; Reliability analyzed separately.

Spread rows: **928** (FBS-FBS 807)
Total rows: **931** (FBS-FBS 805)

## Conservative threshold screens

These screens are not automatically promoted to production. B requires >=80 bets, >=52.4% wins and positive ROI. A requires >=45 bets, >=54.5% wins and >=4% ROI. Reliability remains a separate live-data gate.

### Spread
- B_screen: >= **0.5 points** — 388-347-11, 52.79% wins, 0.78% ROI, n=746
- A_screen: >= **4.5 points** — 173-144-5, 54.57% wins, 4.19% ROI, n=322

### Over
- B_screen: >= **2.0 points** — 174-153-0, 53.21% wins, 1.58% ROI, n=327
- A_screen: no 2025 cutoff passed the screen

### Under
- B_screen: >= **0.5 points** — 165-119-0, 58.10% wins, 10.92% ROI, n=284
- A_screen: >= **0.5 points** — 165-119-0, 58.10% wins, 10.92% ROI, n=284

## Files

- `cfb_2025_current_production_game_edges.csv`: every model-vs-closing-line decision.
- `cfb_2025_current_production_edge_thresholds.csv`: every 0.5-point cutoff, split by market and FBS-FBS/all games.
- `cfb_2025_current_production_grade_backtest.json`: machine-readable summary.
