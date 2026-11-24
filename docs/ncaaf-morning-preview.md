# NCAAF morning preview lifecycle

The existing Render :07/:37 cadence starts NCAAF work at **7 a.m. Eastern**.
Runs before 7 return before storage/provider/model work. From 7 to 9, a valid
same-day matchup can get one ungraded `game_previews` baseline. From 9 onward,
the normal insert-once authoritative save runs independently. NCAAM currently
has no hour gate; 7 a.m. is the explicit NCAAF daytime policy, not an inferred
NCAAM setting. No overnight exception is introduced.

Both preview and authoritative slate rows publish verified `Game Time` and
actual `Build At` timestamps. The original slate schema uses `Date`; the
schedule schema uses `Game Date`. Persistence explicitly indexes output rows
by ISO `date_key` and `Game ID` as `game_key`. Legacy schedule imports do not
index `Game Date` as `date_key`, so the public reader resolves missing legacy
slate kickoff clocks by indexed game ID and verifies date/team identity.

The public collector records baseline provenance as `game_previews` or
`daily_slate`. Only authoritative baselines captured by the frozen quote time
can create FINAL_PREGAME overlays; a preview never can. Among eligible
final candidates, the earliest authoritative capture wins. SQL insertion also
requires a matching saved authoritative row. Live previews continue to receive
valid market changes until normal authority takes over. Existing final locks
remain immutable. Missing model inputs or a failed authoritative build do not
promote a preview into a final lock.

The cron requires both stored classifications to be explicitly `fbs` (case
insensitive). This is a saved-schema eligibility check, not proof of historical
source provenance: `_parse_games` can default missing raw classifications to
`fbs`. ESPN's `_espn_games_payload` does supply membership-derived classes, but
saved rows do not record which importer supplied them. This change does not
alter that importer or historical saves and cannot certify their provenance.

Release public PR ezpzpicks/ezpz-public#191 first, then builder PR
 ezpzpicks/mlb-mobile-admin#71. No new job, model formula, grade policy, or
manual-pick workflow is introduced.
