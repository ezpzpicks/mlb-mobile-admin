# RotoWire NBA availability

The default builder availability mode is `rotowire_html`. It reads only the
public NBA daily lineup HTML, once per pending slate. It does not call ESPN's
injury endpoint, a subscriber minutes endpoint, or RotoWire's keyed API. ESPN
continues to supply game identities/status, current rosters, checked past-game
minutes and final scores. ScoresAndOdds supplies the original spread/total.

The parser checks the visible slate date, ET tipoff, home/away team codes,
expected/confirmed lineup status, five full player names and RotoWire's own
namespaced IDs. An abbreviated display name is never used for fuzzy matching.
Starters and non-OUT entries must uniquely match the checked current roster.
Off-roster nonstarter OUT entries are retained in an explicit ignored audit;
they already receive zero minutes because roster membership takes precedence.
Ambiguous identities always block. The response hash,
observation time and normalized matchup are sealed with each prediction.

OUT and out-for-season players get zero minutes. DOUBTFUL is out in the primary
rotation; QUESTIONABLE/PROBABLE remain explicit conditional IN assumptions with
single-player sensitivity checks. The 'May Not Play' heading alone never means
OUT. Bench players stay in the rotation and unlisted availability stays UNKNOWN.
Minutes retain the existing prior-game/historical evidence and capped 240-minute
allocation. A listed starter without minute evidence requires manual review.

Expected and confirmed starters remain distinct. A hypothetical starter-OUT
scenario cannot claim that its replacement five is confirmed. These public
cards are not a complete official injury report, so report_complete stays false.

Stale or mismatched dates, changed tipoffs, bad player identities, incomplete
starting fives, login/subscriber walls, HTTP errors and unknown injury labels
block the affected new forecast. They never create an empty healthy report.
Existing forecasts and pending result settlement continue. No source failure
silently switches the forecast into an unchecked mode.

RotoWire automatic records use `nba-auto-rotowire-html-v1-early/pregame`, separate
from ESPN, manual research and expected-rotation bypass records. No historical
locks are overwritten or retroactively changed. NBA public posting remains off.

`python scripts/run_nba_builder.py --check-rotowire` checks the public source and
prints a bounded diagnostic without any database reads or writes. The temporary
`NBA_ROTOWIRE_DIAGNOSTIC=1` environment setting invokes the same check from the
existing Render cron, including outside the normal 9 AM–midnight build window;
reset it to `0` after runtime verification. Normal source reads occur only when
an eligible game still needs a forecast. The cron schedule is unchanged.
