# ESPN NBA injury source — verified 2026-10-22

ESPN's live NBA injury feed provides injury statuses and estimated return dates.
The implementation uses that source in the manual NBA builder. The live check at
12:26 PM ET found 71 entries, including 36 OUT and 35 day-to-day entries; all 71
had an estimated return date. These are observations at that time, not a guarantee
that every future entry will include one.

- [ESPN NBA injuries](https://www.espn.com/nba/injuries)
- [ESPN league injury JSON](https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries?limit=1000)
- [Machine-readable verification](espn_injury_source_check.json)

The HTML page required a JavaScript check in this environment. ESPN's public JSON
endpoint returned HTTP 200 and supplied the data directly. No login, paid feed,
third-party injury aggregator or challenge-solving was used. This is a public,
unversioned ESPN endpoint; unexpected responses produce an explicit source error.

## Verified fields

| Source field | Use |
| --- | --- |
| `timestamp` | Source-feed timestamp; retained separately from retrieval time |
| `season.year` | Prevent applying injuries to a different NBA season |
| `injuries[].displayName` | Team grouping, reconciled with athlete team metadata |
| `injuries[].injuries[].status` | Reported status; OUT or unresolved DAY_TO_DAY in this check |
| `date` on each injury | Last news update, retained separately from feed freshness |
| `details.returnDate` | Estimated calendar return date; unknown when missing |
| `details.type`, `side`, `detail` | Injury description |
| Athlete player-card URL | ESPN athlete identity when the response omits athlete.id |

The injury record's own `id` is not an athlete ID. ESPN athlete IDs are also not
NBA Stats person IDs. The importer uses an existing verified ESPN mapping, a
unique normalized full name, or an explicit reviewed override. No fuzzy name
matching or numeric-ID substitution is used. New rookies without a matching NBA
profile are listed for review; add their NBA roster profile and reapply.

One observed entry had conflicting injury-group and athlete-profile teams.
It remains visible and is withheld from automatic application for either team.
Consistent team evidence can identify a traded player, reusing his dated rating
with zero reference minutes on the new team. Historical minutes are removed from
the former team; replacement minutes still require explicit assignment.

## Model behavior

1. Fetch once for the league using **Fetch latest ESPN injuries**. The manual
   session reuses that response across matchups and widget reruns.
2. Review the selected teams' statuses, injury updates and estimated return dates.
3. **Use ESPN injuries in this matchup and update cutoff** applies the snapshot,
   advances the cutoff when needed, and invalidates the prior projection.
4. OUT players receive zero minutes. Vacated minutes are not redistributed by the
   importer; the model blocks comparison until each rotation again totals 240.
5. DAY_TO_DAY, questionable, doubtful, probable and unknown remain explicit IN/OUT
   scenarios. An estimated date never establishes current game availability.
6. Unlisted players remain UNKNOWN. Prior stale ESPN status is removed rather than
   converted to AVAILABLE. Team-report completeness and starters stay unconfirmed.
7. Return estimates on/before game day, stale feed timestamps, source disagreements
   and manual edits that differ from the ESPN status are flagged. A current injury
   snapshot cannot enter a prediction whose cutoff predates its observation.

Both the raw-response SHA-256 fingerprint and dated, normalized source facts for
the matchup are retained with the research inputs. Player status, update dates and
estimated returns participate in snapshot identity and survive rotation editing.
A failed refresh leaves old timestamps intact and clears any displayed result;
it does not fabricate a current healthy report.

## Validation and scope

The implemented fetch succeeded against ESPN. A technical Atlanta/Boston input
check applied the live source through the headless builder with database access
disabled and zero additional network requests on application. It was not a real
scheduled matchup or betting pick. Behavioral tests cover date/ID preservation,
missing estimates, unresolved statuses, roster conflicts, explicit replacements,
future-data rejection, errors, reruns and snapshot persistence.

This adds current ESPN inputs. It supplies no historical injury archive, does not
change the saved 335-game backtest, and does not establish improved betting accuracy.
The NBA model remains a research draft. Scheduling, public picks and deployment
are unchanged.
