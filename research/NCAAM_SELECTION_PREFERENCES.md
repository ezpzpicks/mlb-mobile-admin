# NCAAM selection preferences — Ryan's November 13, 2026 review

All 14 reviewed plays are recorded below. These are user-directed selection
preferences, not validated win probabilities. They apply to new pregame grades;
existing selections and records retain their original policy. No rejected
candidate automatically becomes an official EZPZ Pick on the opposite side.

| Reviewed play | Preference and general rule |
|---|---|
| North Alabama / Miami Under | Accept a weak attack facing the other team's strong defense at a high total. Miami spread may merit review, but needs its own projected cover edge. |
| TAMCC / FSU Under | Accept a slow, weak attack with solid defense facing another good defense. This specific execution case may survive an opposing fast tempo. |
| Rutgers −21.5 | A large favorite needs strong national defense, with a stronger requirement at very large spreads. Fast tempo alone is insufficient. |
| Pitt +3.5 | Avoid a near-even matchup when the opponent is much faster. Preserve Ryan's stated exception: +3.5 with matched tempo can be acceptable. |
| Tennessee State / Tennessee Under | Above-average opponent tempo is an Under concern. A mildly slow favorite alone is insufficient; require distinct suppression/execution support or a genuinely slow, strong-defense heavy favorite. |
| Purdue −18.5 | Avoid a large favorite against an above-average attack with above-average tempo, even when the favorite has strong offense and defense. |
| Ohio / Purdue Under | Accept the slow, strong-defense heavy-favorite profile when the point projection supports Under at the offered line. Reject the role-reversed profile. Pace control remains a hypothesis. |
| Charlotte −8.5 | Exclude a medium favorite in a slow matchup against a competent defense when the favorite lacks strong defense. Examine dog/Under separately; do not automatically reverse the pick. |
| Marist +2.5 | Near-even efficiencies without a preferred tempo fit need more cushion; +5 can be acceptable. |
| Columbia −5.5 | Accept relative advantages in both efficiencies at a modest spread. A team with below-average offense and weak defense may lay up to eight points; more is excluded. |
| FIU +2.5 | Retain a faster underdog with comparable efficiencies and no meaningful opponent advantage when the existing projection and grade qualify. Tempo adds no rating points or automatic grade. |
| Elon −1.5 | A small advantage in defense alone is insufficient. Require both relative efficiency advantages or at least one meaningful advantage. |
| LIU / Fordham Under | Generic or canceling labels provide no convincing Under case. Require a distinct suppression, defensive-execution or slow-favorite supporting path. |
| South Dakota / Drake Over | Exclude a high-total Over when the fast team has below-average offense and the more efficient opponent is slow. High-140s totals can remain eligible under the original profile/projection requirements. |

## Operational definitions

These configurable defaults translate the review into code. They were not
chosen by searching for the best historical profit and are not established
statistical facts.

| Setting | Initial definition |
|---|---|
| Above/below average | Compared with the saved dated Division I national mean |
| Meaningful relative efficiency advantage | At least 1.0 national SD between teams on that metric |
| Strong / very strong national defense | Defensive rating at least 1.0 / 1.5 SD below the mean |
| Solid defense / meaningfully weak attack | At least 0.5 SD below the relevant mean |
| Large / very large favorite | Laying at least 15 / 25 points |
| Slow-favorite Under exception | Laying at least 15, tempo at least 1 SD below average, defense at least 1 SD better than average |
| Near-even underdog cushion | +5 by default |
| Matched-tempo exception | At least +3.5 with tempos within 0.5 national SD |
| Preferred faster underdog | Tempo at least 1 national SD faster than opponent |
| Weak absolute favorite cap | Inclusive eight-point maximum |
| Slow medium-favorite concern | Laying at least 8, both tempos below average, opponent defense better than average, favorite lacks strong national defense |
| High-total execution screen | 150 or more; high-140s totals can remain eligible |

Adjusted offense and defense already measure efficiency per 100 possessions.
Equal efficiencies at different tempos do not make the slower team more
efficient. Tempo changes scoring opportunities for both teams. The preferred
faster-underdog style is a selection preference, not an efficiency upgrade.
A slow heavy favorite does not guarantee control of the game's tempo.

## Projection and publication requirements

Existing total-direction agreement and the spread edge window (at least 1,
strictly below 4 points) remain prerequisites. The preference layer removes
candidates or retains qualifying ones; it does not create a grade from tempo
alone or turn a rejected side into an approved opposite wager.

At the latest saved Purdue/Ohio capture inspected, projection 151.67 versus
total 149.5 supported Over, so Under did not qualify at that quote. Its preferred
Under matchup path remains available when the projection is below the offered
total. Miami's relative matchup likewise does not create a favorite spread edge
when the projected margin is smaller than the spread.

Total policy: `ncaam-matchup-grades-2026-11-13-v3`.
Spread policy: `ncaam-spread-grades-2026-11-13-v2`.
Preference layer: `ryan-matchup-review-2026-11-13-v1`.

Previous policies are retained in separate JSON files for reproducible replay.
New grades record the policy digest, original pre-review grade, supporting
paths, exclusions and notes. The website repeats quote-sensitive spread checks
using the same saved pregame factors.

The acceptance fixtures use saved pregame forecasts for 13 unique games and
omit outcome fields. Checks cover the reviewed exclusions, retained candidates,
stronger-defense variant, price boundaries, matched-tempo Pitt variant, +5 Marist
variant, Purdue role reversal, projection precedence, legacy policy behavior,
source immutability and independence from injected results. Python/TypeScript
parity checks cover changed spread quotes. These verify implementation of the
preferences, not predictive profitability.
