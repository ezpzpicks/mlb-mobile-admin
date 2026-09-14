from pathlib import Path


def require_replace(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"missing anchor: {label}")
    return text.replace(old, new, 1)


def replace_between(text: str, start: str, end: str, replacement: str) -> str:
    a = text.find(start)
    if a < 0:
        raise SystemExit(f"missing start marker: {start}")
    b = text.find(end, a)
    if b < 0:
        raise SystemExit(f"missing end marker: {end}")
    return text[:a] + replacement + text[b:]


def update_slot_model() -> None:
    path = Path("builders/nfl_slot_matchups.py")
    text = path.read_text(encoding="utf-8")

    text = require_replace(
        text,
        'MODEL_VERSION = "nfl-v4.6-slot-defense-matchups-2026-09-13"',
        'MODEL_VERSION = "nfl-v4.7-touchdown-slot-matchups-2026-09-13"',
        "slot model version",
    )
    text = require_replace(
        text,
        '    "Receiving Yards": "receiving_yards",\n}',
        '    "Receiving Yards": "receiving_yards",\n    "Anytime TD": "anytime_tds",\n}',
        "touchdown market stat",
    )
    text = require_replace(
        text,
        '    "Receiving Yards": {"RB1", "RB2", "WR1", "WR2", "WR3", "TE"},\n}',
        '    "Receiving Yards": {"RB1", "RB2", "WR1", "WR2", "WR3", "TE"},\n    "Anytime TD": {"QB", "RB1", "RB2", "WR1", "WR2", "WR3", "TE"},\n}',
        "touchdown market slots",
    )
    text = require_replace(
        text,
        '    "Receiving Yards": 0.52,\n}',
        '    "Receiving Yards": 0.52,\n    "Anytime TD": 0.72,\n}',
        "touchdown matchup strength",
    )
    text = require_replace(
        text,
        '    "Receiving Yards": 0.12,\n}',
        '    "Receiving Yards": 0.12,\n    "Anytime TD": 0.18,\n}',
        "touchdown matchup cap",
    )

    text = require_replace(
        text,
        '    for stat in set(MARKET_STATS.values()):\n        frame[stat] = nfl_builder._numeric_frame_column(frame, stat)\n\n    aggregate = frame.groupby(["_week", "_team", "_opponent", "_player"], as_index=False).agg(\n',
        '    base_stats = set(MARKET_STATS.values()) - {"anytime_tds"}\n    base_stats.update({"rushing_tds", "receiving_tds"})\n    for stat in base_stats:\n        frame[stat] = nfl_builder._numeric_frame_column(frame, stat)\n    frame["anytime_tds"] = (\n        pd.to_numeric(frame["rushing_tds"], errors="coerce").fillna(0.0)\n        + pd.to_numeric(frame["receiving_tds"], errors="coerce").fillna(0.0)\n    )\n\n    aggregate = frame.groupby(["_week", "_team", "_opponent", "_player"], as_index=False).agg(\n',
        "derive actual anytime touchdowns",
    )

    td_profile_helper = '''\ndef _touchdown_profile_from_history(history: pd.DataFrame, opponent: str, slot: str) -> dict[str, float]:
    """Learn where a defense funnels TDs after removing its general TD allowance."""
    slot = _slot(slot)
    opponent = str(opponent or "").strip().upper()
    if history is None or history.empty or slot not in TRACKED_SLOTS:
        return {"adjustment_pct": 0.0, "sample": 0.0}

    market_rows = history[history["market"].astype(str) == "Anytime TD"].copy()
    slot_rows = market_rows[market_rows["slot"].astype(str) == slot].copy()
    defense_slot = slot_rows[slot_rows["opponent"].astype(str) == opponent].copy()
    if market_rows.empty or slot_rows.empty or defense_slot.empty:
        return {"adjustment_pct": 0.0, "sample": 0.0}

    league_slot = float(pd.to_numeric(slot_rows["actual"], errors="coerce").mean())
    league_slot_n = int(pd.to_numeric(slot_rows["actual"], errors="coerce").notna().sum())
    defense_slot_values = pd.to_numeric(defense_slot["actual"], errors="coerce").dropna()
    defense_slot_avg = float(defense_slot_values.mean()) if not defense_slot_values.empty else 0.0
    sample = int(defense_slot[["week", "team"]].drop_duplicates().shape[0])
    if not (math.isfinite(league_slot) and league_slot > 0.04 and math.isfinite(defense_slot_avg)) or league_slot_n < 8:
        return {"adjustment_pct": 0.0, "sample": float(sample)}

    game_totals = market_rows.groupby(
        ["season", "week", "team", "opponent"], as_index=False
    )["actual"].sum()
    league_total = float(pd.to_numeric(game_totals["actual"], errors="coerce").mean()) if not game_totals.empty else 0.0
    defense_totals = game_totals[game_totals["opponent"].astype(str) == opponent].copy()
    defense_total_values = pd.to_numeric(defense_totals["actual"], errors="coerce").dropna()
    defense_total_avg = float(defense_total_values.mean()) if not defense_total_values.empty else league_total
    defense_games = int(defense_totals[["week", "team"]].drop_duplicates().shape[0]) if not defense_totals.empty else sample
    if not (math.isfinite(league_total) and league_total > 0.10 and math.isfinite(defense_total_avg)):
        return {"adjustment_pct": 0.0, "sample": float(sample)}

    # Touchdowns are sparse. Four league-average games act as a Bayesian prior
    # before the observed defense-specific rates can move the model materially.
    prior_games = 4.0
    shrunk_slot_rate = (
        float(defense_slot_values.sum()) + prior_games * league_slot
    ) / max(sample + prior_games, 1.0)
    shrunk_total_rate = (
        float(defense_total_values.sum()) + prior_games * league_total
    ) / max(defense_games + prior_games, 1.0)

    slot_index = shrunk_slot_rate / max(league_slot, 0.04)
    general_td_index = shrunk_total_rate / max(league_total, 0.10)
    outlier_index = slot_index / max(general_td_index, 0.35)
    raw_outlier = float(np.clip(outlier_index - 1.0, -1.0, 1.0))
    sample_weight = float(sample / (sample + 3.5)) if sample > 0 else 0.0
    league_coverage = float(np.clip(league_slot_n / 24.0, 0.40, 1.0))
    adjustment_pct = float(np.clip(
        raw_outlier * sample_weight * league_coverage * MARKET_STRENGTH["Anytime TD"],
        -MARKET_CAP["Anytime TD"], MARKET_CAP["Anytime TD"],
    ))

    absolute_index = defense_slot_avg / max(league_slot, 0.04)
    general_index_raw = defense_total_avg / max(league_total, 0.10)
    return {
        "adjustment_pct": adjustment_pct,
        "sample": float(sample),
        "league_sample": float(league_slot_n),
        "defense_slot_avg": defense_slot_avg,
        "league_slot_avg": league_slot,
        "defense_total_avg": defense_total_avg,
        "league_total_avg": league_total,
        "absolute_edge_pct": absolute_index - 1.0,
        "family_edge_pct": general_index_raw - 1.0,
        "slot_outlier_pct": outlier_index - 1.0,
        "sample_weight": sample_weight,
    }


'''
    if "def _touchdown_profile_from_history(" not in text:
        text = text.replace("\ndef _profile_from_history(", td_profile_helper + "def _profile_from_history(", 1)
    text = require_replace(
        text,
        '    opponent = str(opponent or "").strip().upper()\n    if history is None or history.empty or slot not in TRACKED_SLOTS or not _market_allowed_for_slot(market, slot):\n',
        '    opponent = str(opponent or "").strip().upper()\n    if market == "Anytime TD":\n        return _touchdown_profile_from_history(history, opponent, slot)\n    if history is None or history.empty or slot not in TRACKED_SLOTS or not _market_allowed_for_slot(market, slot):\n',
        "route anytime touchdowns to TD-specific profile",
    )

    usage_helpers = '''\ndef _weighted_profile_share(profile: dict[str, Any], weights: tuple[tuple[str, float], ...]) -> float:
    values: list[tuple[float, float]] = []
    for key, weight in weights:
        value = _num(profile.get(key, np.nan), np.nan)
        if math.isfinite(value) and value >= 0:
            values.append((value, weight))
    if not values:
        return math.nan
    total_weight = sum(weight for _, weight in values)
    return sum(value * weight for value, weight in values) / max(total_weight, 1e-9)


def _touchdown_usage_multiplier(profile: dict[str, Any], slot: str) -> dict[str, float]:
    """Scale only the matchup edge by how concentrated this player's red-zone role is."""
    slot = _slot(slot)
    target_share = _num(profile.get("target_share", np.nan), np.nan)
    carry_share = _num(profile.get("carry_share", np.nan), np.nan)

    receiving_rz_share = _weighted_profile_share(profile, (
        ("redzone_target_share", 0.45),
        ("inside_10_target_share", 0.30),
        ("endzone_target_share", 0.25),
    ))
    rushing_rz_share = _weighted_profile_share(profile, (
        ("goal_line_carry_share", 0.55),
        ("inside_10_carry_share", 0.45),
    ))

    receiving_ratio = (
        receiving_rz_share / target_share
        if math.isfinite(receiving_rz_share) and math.isfinite(target_share) and target_share > 0.015
        else 1.0
    )
    rushing_ratio = (
        rushing_rz_share / carry_share
        if math.isfinite(rushing_rz_share) and math.isfinite(carry_share) and carry_share > 0.015
        else 1.0
    )

    if slot.startswith("RB"):
        usage_ratio = 0.60 * rushing_ratio + 0.40 * receiving_ratio
    elif slot == "QB":
        usage_ratio = rushing_ratio
    else:
        usage_ratio = receiving_ratio

    usage_ratio = float(np.clip(usage_ratio, 0.35, 2.50))
    multiplier = float(np.clip(1.0 + 0.35 * (usage_ratio - 1.0), 0.75, 1.35))
    return {
        "usage_ratio": usage_ratio,
        "multiplier": multiplier,
        "receiving_ratio": float(np.clip(receiving_ratio, 0.20, 3.0)),
        "rushing_ratio": float(np.clip(rushing_ratio, 0.20, 3.0)),
    }


'''
    if "def _touchdown_usage_multiplier(" not in text:
        text = text.replace("\ndef _factor(profile: dict[str, float] | None) -> float:\n", usage_helpers + "def _factor(profile: dict[str, float] | None) -> float:\n", 1)

    new_append_reason = '''def _append_reason(row: dict[str, Any], opponent: str, slot: str, market: str, profile: dict[str, float]) -> None:
    adjustment = _num(profile.get("adjustment_pct"), 0.0)
    if abs(adjustment) < 0.0005:
        return
    sample = int(_num(profile.get("sample"), 0.0))
    defense_avg = _num(profile.get("defense_slot_avg"), 0.0)
    league_avg = _num(profile.get("league_slot_avg"), 0.0)
    absolute = _num(profile.get("absolute_edge_pct"), 0.0)
    outlier = _num(profile.get("slot_outlier_pct"), 0.0)
    if market == "Anytime TD":
        team_edge = _num(profile.get("family_edge_pct"), 0.0)
        usage_ratio = _num(profile.get("usage_ratio"), 1.0)
        usage_multiplier = _num(profile.get("usage_multiplier"), 1.0)
        base_adjustment = _num(profile.get("base_adjustment_pct"), adjustment)
        note = (
            f"TD matchup {opponent} vs {slot}: {defense_avg:.2f} TD/g vs {league_avg:.2f} league "
            f"({absolute:+.0%}); general TD environment {team_edge:+.0%}; slot concentration {outlier:+.0%}; "
            f"{sample}g; red-zone role {usage_ratio:.2f}x (matchup amp {usage_multiplier:.2f}x); "
            f"base {base_adjustment:+.1%}, applied {adjustment:+.1%}"
        )
    else:
        note = (
            f"slot matchup {opponent} vs {slot} {market}: {defense_avg:.1f} vs {league_avg:.1f} league "
            f"({absolute:+.0%}); slot outlier {outlier:+.0%}; {sample}g; applied {adjustment:+.1%}"
        )
    current = str(row.get("Confluence", "") or "").strip()
    row["Confluence"] = f"{current} • {note}".strip(" •")


'''
    text = replace_between(text, "def _append_reason(", "def _apply_slot_overlay(", new_append_reason)

    text = require_replace(
        text,
        '    opponent: str,\n    slot: str,\n) -> list[dict[str, Any]]:\n',
        '    opponent: str,\n    slot: str,\n    player_profile: dict[str, Any] | None = None,\n) -> list[dict[str, Any]]:\n',
        "slot overlay player profile parameter",
    )
    text = require_replace(
        text,
        '    factors = {market: _factor(profile) for market, profile in profiles.items()}\n\n    pass_attempt_factor = factors.get("Passing Attempts", 1.0)\n',
        '    td_profile = profiles.get("Anytime TD")\n    if td_profile is not None:\n        usage = _touchdown_usage_multiplier(player_profile or {}, slot)\n        base_adjustment = _num(td_profile.get("adjustment_pct"), 0.0)\n        final_adjustment = float(np.clip(\n            base_adjustment * usage["multiplier"],\n            -MARKET_CAP["Anytime TD"], MARKET_CAP["Anytime TD"],\n        ))\n        td_profile = dict(td_profile)\n        td_profile["base_adjustment_pct"] = base_adjustment\n        td_profile["adjustment_pct"] = final_adjustment\n        td_profile["usage_ratio"] = usage["usage_ratio"]\n        td_profile["usage_multiplier"] = usage["multiplier"]\n        profiles["Anytime TD"] = td_profile\n\n    factors = {market: _factor(profile) for market, profile in profiles.items()}\n\n    pass_attempt_factor = factors.get("Passing Attempts", 1.0)\n',
        "red-zone amplify touchdown matchup",
    )

    old_install = '''def install_slot_matchup_layer(nfl_builder: Any) -> None:
    """Install the final current-season slot-vs-defense projection layer."""
    if getattr(nfl_builder, "_SLOT_MATCHUP_LAYER_INSTALLED", False):
        nfl_builder.MODEL_VERSION = MODEL_VERSION
        return

    original = nfl_builder._project_player_markets

    def wrapped(*args, **kwargs):
        rows = original(*args, **kwargs)
        slot = str(args[2] if len(args) > 2 else kwargs.get("slot", "") or "")
        opponent = str(args[4] if len(args) > 4 else kwargs.get("opponent", "") or "")
        team_rating = args[9] if len(args) > 9 else kwargs.get("team_rating", {})
        if not isinstance(team_rating, dict):
            team_rating = {}
        season = int(_num(team_rating.get("Season"), 0.0))
        projection_week = int(_num(team_rating.get("Projection Week"), 0.0))
        if season <= 0 or projection_week <= 0:
            return rows
        return _apply_slot_overlay(nfl_builder, rows, season, projection_week, opponent, slot)

    nfl_builder._project_player_markets = wrapped
    nfl_builder.MODEL_VERSION = MODEL_VERSION
    nfl_builder._SLOT_MATCHUP_LAYER_INSTALLED = True
'''
    new_install = '''def install_slot_matchup_layer(nfl_builder: Any) -> None:
    """Install current-season slot-vs-defense learning, including TD distribution outliers."""
    if getattr(nfl_builder, "_SLOT_MATCHUP_LAYER_INSTALLED", False):
        nfl_builder.MODEL_VERSION = MODEL_VERSION
        return

    original = nfl_builder._project_player_markets

    def wrapped(*args, **kwargs):
        rows = original(*args, **kwargs)
        player = str(args[0] if len(args) > 0 else kwargs.get("player", "") or "")
        slot = str(args[2] if len(args) > 2 else kwargs.get("slot", "") or "")
        opponent = str(args[4] if len(args) > 4 else kwargs.get("opponent", "") or "")
        profiles_frame = args[7] if len(args) > 7 else kwargs.get("profiles", pd.DataFrame())
        team_rating = args[9] if len(args) > 9 else kwargs.get("team_rating", {})
        if not isinstance(team_rating, dict):
            team_rating = {}
        season = int(_num(team_rating.get("Season"), 0.0))
        projection_week = int(_num(team_rating.get("Projection Week"), 0.0))
        if season <= 0 or projection_week <= 0:
            return rows
        try:
            player_profile = nfl_builder._profile_lookup(profiles_frame, player)
        except Exception:
            player_profile = {}
        return _apply_slot_overlay(
            nfl_builder, rows, season, projection_week, opponent, slot, player_profile
        )

    nfl_builder._project_player_markets = wrapped
    nfl_builder.MODEL_VERSION = MODEL_VERSION
    nfl_builder._SLOT_MATCHUP_LAYER_INSTALLED = True
'''
    text = require_replace(text, old_install, new_install, "slot layer installer")
    path.write_text(text, encoding="utf-8")


def update_admin_version() -> None:
    path = Path("app_mobile_admin.py")
    text = path.read_text(encoding="utf-8")
    text = require_replace(
        text,
        '    "NFL": "nfl-v4.6-slot-defense-matchups-2026-09-13",',
        '    "NFL": "nfl-v4.7-touchdown-slot-matchups-2026-09-13",',
        "admin NFL version label",
    )
    path.write_text(text, encoding="utf-8")


def update_tests() -> None:
    path = Path("research/test_nfl_slot_matchups.py")
    text = path.read_text(encoding="utf-8")

    td_tests = '''\n\ndef build_td_history() -> pd.DataFrame:
    rows = []
    defenses = ["NYG", "DAL", "PHI", "WAS", "GB", "MIN", "DET", "CHI"]
    for week in [1, 2, 3, 4]:
        for index, defense in enumerate(defenses):
            team = f"TD{week}{index}"
            # Keep total tracked anytime TDs near 2.0/game for every defense,
            # but make NYG distribute a much larger share of those TDs to TE.
            te_td = 1.00 if defense == "NYG" else 0.25
            wr1_td = 0.20 if defense == "NYG" else 0.45
            wr2_td = 0.20 if defense == "NYG" else 0.35
            wr3_td = 0.10 if defense == "NYG" else 0.20
            rb1_td = 0.30 if defense == "NYG" else 0.45
            rb2_td = 0.10 if defense == "NYG" else 0.15
            qb_td = 0.10 if defense == "NYG" else 0.15
            rows.extend([
                _row(week, defense, "TE", "Anytime TD", te_td, team),
                _row(week, defense, "WR1", "Anytime TD", wr1_td, team),
                _row(week, defense, "WR2", "Anytime TD", wr2_td, team),
                _row(week, defense, "WR3", "Anytime TD", wr3_td, team),
                _row(week, defense, "RB1", "Anytime TD", rb1_td, team),
                _row(week, defense, "RB2", "Anytime TD", rb2_td, team),
                _row(week, defense, "QB", "Anytime TD", qb_td, team),
            ])
    return pd.DataFrame(rows)
'''
    if "def build_td_history()" not in text:
        text = text.replace("\ndef main() -> None:\n", td_tests + "\n\ndef main() -> None:\n", 1)

    assertions = '''\n\n    td_history = build_td_history()
    te_td = slot_model._profile_from_history(td_history, "NYG", "TE", "Anytime TD")
    assert int(te_td["sample"]) == 4
    assert te_td["defense_slot_avg"] > te_td["league_slot_avg"]
    assert te_td["slot_outlier_pct"] > 0.20
    assert 0.02 < te_td["adjustment_pct"] <= slot_model.MARKET_CAP["Anytime TD"]

    # A generally high-TD defense should not create a false slot signal if its
    # TD distribution by slot remains proportional to league expectations.
    proportional = td_history.copy()
    nyg = proportional["opponent"] == "NYG"
    proportional.loc[nyg, "actual"] = proportional.loc[nyg, "actual"] * 1.8
    proportional_te = slot_model._profile_from_history(proportional, "NYG", "TE", "Anytime TD")
    assert abs(proportional_te["adjustment_pct"]) < te_td["adjustment_pct"]

    high_rz_te = {
        "target_share": 0.16,
        "redzone_target_share": 0.30,
        "inside_10_target_share": 0.34,
        "endzone_target_share": 0.38,
    }
    low_rz_te = {
        "target_share": 0.16,
        "redzone_target_share": 0.08,
        "inside_10_target_share": 0.07,
        "endzone_target_share": 0.06,
    }
    high_usage = slot_model._touchdown_usage_multiplier(high_rz_te, "TE")
    low_usage = slot_model._touchdown_usage_multiplier(low_rz_te, "TE")
    assert high_usage["usage_ratio"] > 1.5
    assert high_usage["multiplier"] > 1.0
    assert low_usage["usage_ratio"] < 0.7
    assert low_usage["multiplier"] < 1.0
'''
    if "high_rz_te" not in text:
        text = text.replace('\n    print("NFL slot matchup tests passed")\n', assertions + '\n    print("NFL slot matchup tests passed")\n', 1)
    path.write_text(text, encoding="utf-8")


def main() -> None:
    update_slot_model()
    update_admin_version()
    update_tests()


if __name__ == "__main__":
    main()
