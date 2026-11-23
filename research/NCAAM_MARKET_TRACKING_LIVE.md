# NCAAM market tracking rollout

Ryan approved deploying the revised directional tracking model with the balanced weights demonstrated in the Nov 22 examples. New grade rules are deferred. No further historical betting replay or regrading is part of this rollout.

The production consumer is the existing NCAAM market collector in ezpzpicks/ezpz-public. The existing Python v0.16 builder continues to supply the unadjusted team baseline. The collector applies a checked TypeScript port of builders/ncaam_market_flow.py each time its normal market polling runs. Cross-language fixtures cover 90 rule and timing examples, including the eight approved patterns, both markets' basic states and the UConn timeline; these fixtures contain no betting outcomes.

The deployed configuration is config/ncaam_market_tracking_live.json, mirrored exactly in the public site's lib/config/ncaam-market-tracking.json. The live layer is ncaam-v0.17-market-tracking-2026-11-22. It uses fixed balanced weights, with ordinary flat-line split weight 25%, one-point confirmation 62.5%, and full weight at two points. Opposing money/ticket divergence retains the approved 75% starting multiplier. Independent line movement counts in full. Nonzero corrections retain the one-point floor and four-point spread / six-point total caps.

The first successful deployed collector tick saves an immutable activation time. Only future pregame forecasts are created. Each material baseline, line or split-history change appends a new row to ncaam_market_projection_updates. Its inputs include the baseline record ID and scoring proof, paired pregame history, sizing parameters, contributions, observed time and publication time. An unchanged heartbeat does not append another forecast. A final pregame capture stops further revisions, and in-game data cannot create a new forecast.

All new projections are Ungraded and cannot cast an A/B model vote or create a graded model play. Existing recorded A/B forecasts are frozen at the activation cutoff and settle under their original side and quote. Later baseline-builder A/B calculations remain baseline artifacts and are excluded from the forward graded ledger. Original forecast rows, original grade rows, official manual picks and historical records are not rewritten.

Current public reads remain read-only. Persistence happens only in the existing authenticated, paced collector. The usual schedule stays 30 minutes early and five minutes inside 90 minutes of tipoff. The full slate displays the updated projections and Ungraded badge; new grading can be established separately from the saved forward observations.

The earlier research aggregates and their negative historical findings remain unchanged. This is an implementation of the requested interpretation, not a claim that those exploratory returns demonstrated a future betting edge.
