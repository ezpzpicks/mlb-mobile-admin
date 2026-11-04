#!/usr/bin/env python3
"""Build a compact prior-season D1 venue corpus from explicit source files."""
import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from builders.ncaam_home_court import POLICY, parse_game_history, school_key
from builders.ncaam_model import parse_ratings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gamelog", action="append", required=True, help="SEASON=CSV_PATH")
    parser.add_argument("--ratings", action="append", required=True, help="SEASON=CSV_PATH")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    logs = {int(y): Path(p) for y, p in (x.split("=", 1) for x in args.gamelog)}
    ratings = {int(y): Path(p) for y, p in (x.split("=", 1) for x in args.ratings)}
    if logs.keys() != ratings.keys():
        raise ValueError("Every season needs its own verified Division I rating group.")
    observed = datetime.now(timezone.utc).isoformat()
    games, sources = [], []
    for year, path in sorted(logs.items()):
        raw = path.read_bytes()
        rating_bytes = ratings[year].read_bytes()
        members = {school_key(k) for k in parse_ratings(rating_bytes.decode(), year, observed)["teams"]}
        rows, audit = parse_game_history(raw.decode(), year, f"{max(logs)}-07-01", members)
        if len(rows) < 4000:
            raise ValueError(f"Incomplete prior-season source for {year}.")
        games.extend(rows)
        sources.append({"season": year, "games_url": f"https://barttorvik.com/getgamestats.php?year={year}&csv=1",
                        "games_sha256": hashlib.sha256(raw).hexdigest(), "audit": audit,
                        "membership_url": f"https://barttorvik.com/{year}_team_results.csv",
                        "membership_sha256": hashlib.sha256(rating_bytes).hexdigest()})
    columns = ["game_id", "season", "date", "home_team", "away_team", "neutral_site", "margin"]
    result = {"policy": POLICY, "columns": columns, "sources": sources, "created_at": observed,
              "games": [[g[k] for k in columns] for g in games]}
    raw = gzip.compress(json.dumps(result, separators=(",", ":")).encode(), mtime=0)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(raw)
    print(json.dumps({"games": len(games), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "sources": sources}, indent=2))


if __name__ == "__main__":
    main()
