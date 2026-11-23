"""Forward-only hold on new game bets; forecasts, props and prior bets survive."""
POLICY = "directional-market-tracking-2026-11-23-v1"
ACTIVATION = "market_projection_activation"


def game_grade_gate(sport):
    if sport not in {"NFL", "NCAAF"}:
        raise ValueError("Unsupported game grading sport")
    version = f"{sport.lower()}-market-tracking-2026-11-23-v1"
    return (f"NOT EXISTS(SELECT 1 FROM dataset_rows WHERE sport='{sport}' "
            f"AND dataset='{ACTIVATION}' AND date_key='policy' "
            f"AND json_extract(payload_json,'$.\"Details JSON\"') IS NOT NULL "
            f"AND json_extract(json_extract(payload_json,'$.\"Details JSON\"'),'$.policy')='{POLICY}' "
            f"AND json_extract(json_extract(payload_json,'$.\"Details JSON\"'),'$.modelVersion')='{version}' "
            "AND json_extract(json_extract(payload_json,'$.\"Details JSON\"'),'$.gradingStatus')='ungraded')")
