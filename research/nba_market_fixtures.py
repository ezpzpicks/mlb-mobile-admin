from datetime import datetime
from zoneinfo import ZoneInfo


def market_rows(game, now):
    day = datetime.fromisoformat(game["tipoff"]).astimezone(ZoneInfo("America/New_York")).date().isoformat()
    base = {"Date": day, "Game ID": game["game_id"], "Game Key": day + "|nba|" + game["game_id"],
            "Game Time": game["tipoff"], "Away Team": game["away"]["name"], "Home Team": game["home"]["name"],
            "Updated At": now.isoformat(), "Source": "ScoresAndOdds", "Snapshot Status": "LIVE",
            "Market Policy": "nba-scoresandodds-v1", "Source URL": "https://www.scoresandodds.com/nba/consensus-picks"}
    return [{**base, "Market": market, "Selection": selection, "Side": side, "Line": str(line), "Odds": "-110"}
            for market, selection, side, line in [("Spread", game["away"]["name"], "", 3),
                ("Spread", game["home"]["name"], "", -3), ("Total", "Over", "Over", 230), ("Total", "Under", "Under", 230)]]
