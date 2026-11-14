# NCAAF selection preferences — Ryan's November 13, 2026 review

These four reviews define prospective selection preferences. The three saved
pregame matchups provide acceptance fixtures, not independent performance
validation. Previous selections, outcomes, numerical score projections and
official manual EZPZ approvals are preserved.

| Reviewed play | Preference | Rule |
|---|---|---|
| Pitt −3.5 | Avoid a favorite whose average attack lacks a clear mismatch against an average defense, while its good defense offsets the opposing strong attack. Leaning FSU is a separate decision. | A favorite needs either a clear attacking pairing or clear suppression of the opposing attack. Fast tempo alone is insufficient. Never automatically reverse the rejected favorite. |
| FSU / Pitt Over 52.5 | Reject the high-tempo Over when strong offense is canceled by good defense and the other offense/defense pairing is ordinary. | Require at least one clearly favorable offense-versus-opposing-defense pairing for an Over. Two merely positive pairings do not suffice. An Under must independently agree with its saved point projection and price. |
| Illinois / UCLA Under 47.5 | Retain the slow Illinois attack versus UCLA's good defense, with UCLA favored and playing at moderate tempo. Illinois's strong attack does not cancel the combined pace/defense concern. | Annotate the slow-underdog/good-favorite-defense Under path when the favorite's scoring pairing is not extreme. Preserve the existing direction and price gates. |
| Houston −8.5 | Retain strong offense versus average defense, with average Houston defense facing weak Colorado offense. Fast Colorado tempo is not by itself a meaningful attacking advantage. Accept the matchup profile up to −11.5. | Inclusive 11.5-point ceiling for strong-attack favorite versus weak-attack opponent with both defenses near average. More than 11.5 is excluded even if the projected margin would cover. The ceiling does not guarantee positive value at every cheaper quote. |

## Operational definitions

SP+ offense and defensive points allowed use their captured FBS population;
lower defense is better. Pace uses the already-captured real play-by-play
population. The board's existing category boundary is 0.5 population SD.

For selection review, an offense-versus-defense pairing index is the average
of the offense z-score and its opposing defense z-score. This is a qualitative
support index, not a new score projection or estimated betting probability.

| Setting | Default |
|---|---|
| Clearly favorable attacking pairing | Pairing index at least +0.5 |
| Clear opposing-attack suppression | Opposing pairing index at most −0.5 |
| Strong/weak offense | At least +0.5 / at most −0.5 national SD |
| Near-average defense or tempo | Strictly within ±0.5 national SD |
| Slow tempo / good defense | At most −0.5 national SD |
| Decent-sized favorite for the preferred Under path | At least seven points |
| Strong-attack/weak-opponent spread ceiling | Inclusive 11.5 points |

The preferred Under path requires the market underdog to be slow and face the
favorite's good defense; the favorite has near-average tempo; the underdog's
attack pairing is suppressed; and the favorite's pairing is at most +0.5.
It records supporting context without upgrading or creating an otherwise
unqualified grade.

The captured Pitt/FSU pairing indices were approximately +0.057 and +0.388.
The previous rule counted both as favorable for Over; neither meets the new
clear-support threshold. Pitt likewise lacks both clear attacking support and
clear suppression at this captured matchup. The Under preference cannot flip
the saved model's Over projection of 54.57 against 52.5 into an approved Under.

Ryan's illustrative UCLA 21–28 / Illinois 7–10 score range is retained as review
context, not imposed on the point model. The saved projection was UCLA 23.65,
Illinois 18.62, total 42.27 against 47.5. It supports Under without predicting
Illinois would necessarily be held to 7–10.

## Versioning and checks

Policy `ncaaf-sp-matchup-2026-11-13-v2` adds preference layer
`ryan-ncaaf-review-2026-11-13-v1`. The prior policy is retained in
`config/ncaaf_matchup_grades_v1.json`. New captures include preference thresholds,
base grades, exclusions, supporting paths and notes. Historical v1 captures
remain readable; v2 performance is measured separately.

The website rechecks the ceiling and preference rules at a changed quote using
the frozen factors. SP+ decisions retain their factor-based grade when repriced;
they do not revert to the former point-edge A/B ladder. Publication clocks,
direction, 20-point spread restriction and positive-price-value requirements
remain prerequisites. Missing preference metadata fails closed for new-policy
model signals. Test fixtures omit game outcomes and verify outcome independence,
price boundaries, no automatic side flips, unchanged forecasts and legacy reads.
