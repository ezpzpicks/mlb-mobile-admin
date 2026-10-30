"""Exercise audit accounting and the production's strict prior-game boundary."""
from pathlib import Path
import tempfile
from research.ncaaf_edge_audit import PriorSchedule, record, profit, result_value, canon, play
from builders.cfb_schedule_adjustment import _read_prior_games


def main():
    rows=[{"result":1,"profit":profit(1,-110)},{"result":-1,"profit":profit(-1,-110)},
          {"result":0,"profit":profit(0,-110)}]
    r=record(rows)
    assert (r["wins"],r["losses"],r["pushes"])==(1,1,1)
    assert abs(r["units"]+1/11)<1e-6
    assert result_value(48,47.5,1)==1 and result_value(48,47.5,-1)==-1
    assert result_value(48,48,1)==0
    games=[]
    for day,week,score in [("2026-10-16",7,20),("2026-10-17",7,99),("2026-10-24",8,77)]:
        games.append({"Season":2026,"Week":week,"Game Date":day,"Away Team":"A","Home Team":"B",
            "Away Score":score,"Home Score":10,"Completed":True,"Away Classification":"fbs","Home Classification":"fbs"})
    before=_read_prior_games(PriorSchedule(games),2026,7,"2026-10-17")
    assert list(before["Away Score"])==[20]
    assert canon("San José State")==canon("San Jose State")
    # The simulation's selected side can differ from the mean direction near zero.
    game={"id":"1","game":"A @ B","date":"2026-10-17","week":7,"version":"test","current":False,
        "market_total":50.,"saved_total":50.2,"actual_total":48.,"weather":0.,"total_personnel":0.,
        "slate":{"Total Pick":"Under 50","Total Under Odds":"-110"}}
    assert play(game,"Total")["side"]==-1 and play(game,"Total")["result"]==1
    assert play(game,"Total",50.2)["side"]==1 and play(game,"Total",50.2)["result"]==-1
    print("NCAAF audit: unit-risk accounting, pushes, identity, and no future/same-day scores passed")


if __name__=="__main__":main()
