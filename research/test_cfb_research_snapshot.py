"""Research evidence must be timestamped honestly and leave model values intact."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import numpy as np
from builders.cfb_research_snapshot import research_snapshot


def main():
    before=datetime(2026,10,31,15,tzinfo=timezone.utc)
    result={"game":{"Game ID":"123","Game Time":"2026-10-31T12:00:00-04:00"},
        "projection":{"total":50.,"margin":7.,"away_points":21.5,"home_points":28.5,
            "possessions":12.,"total_regression_base":47.,"schedule_adjustment_total_points":3.,
            "total_regression_features":{"pace":np.float64(12.),"missing":float("nan")},
            "regime_2026_applied":False},
        "simulation":{"totals":np.array([40.,50.,60.]),"margins":np.array([0.,7.,14.]),"away_mean":21.5,"home_mean":28.5},
        "total_market":{"grade":"B Total","pick":"Under 53.5","odds":-110,"model_edge_points":3.5,"probability":.60}}
    original=deepcopy(result)
    payload=json.loads(research_snapshot(result,"version",before))
    assert payload["captureStatus"]=="PRE_GAME"
    assert payload["components"]["total_regression_features"]["missing"] is None
    assert payload["components"]["schedule_adjustment_total_points"]==3
    assert payload["simulation"]["totals_sd"]>0
    assert payload["markets"]["total"]["grade"]=="B Total"
    assert result["projection"]["total"]==original["projection"]["total"]
    assert np.array_equal(result["simulation"]["totals"],original["simulation"]["totals"])
    assert json.loads(research_snapshot(result,"version",datetime(2026,10,31,16,tzinfo=timezone.utc)))["captureStatus"]=="AFTER_START"
    for kickoff in ["", "2026-10-31T16:00:00", "bad"]:
        result["game"]["Game Time"]=kickoff
        assert json.loads(research_snapshot(result,"version",before))["captureStatus"]=="KICKOFF_UNKNOWN"
    try:
        research_snapshot(result,"version",datetime(2026,10,31,15))
        raise AssertionError("Naive capture time accepted")
    except ValueError:
        pass
    print("CFB research snapshots: timestamp, provenance, finite JSON, and prediction preservation passed")


if __name__=="__main__":main()
