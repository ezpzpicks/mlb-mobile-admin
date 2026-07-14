import math
from dataclasses import dataclass
from typing import Optional


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(float(value) / math.sqrt(2.0)))


def american_implied_probability(odds: Optional[float]) -> float:
    try:
        odds = float(odds)
    except Exception:
        return 0.0
    if odds < 0:
        return abs(odds) / (abs(odds) + 100.0)
    if odds > 0:
        return 100.0 / (odds + 100.0)
    return 0.0


def fair_american_odds(probability: float) -> int:
    probability = clamp(probability, 0.001, 0.999)
    if probability >= 0.5:
        return int(round(-100 * probability / (1 - probability)))
    return int(round(100 * (1 - probability) / probability))


def probability_edge(model_probability: float, odds: Optional[float]) -> float:
    implied = american_implied_probability(odds)
    return model_probability - implied if implied else 0.0


def expected_value_per_unit(model_probability: float, odds: Optional[float]) -> float:
    try:
        odds = float(odds)
    except Exception:
        return 0.0
    profit = odds / 100.0 if odds > 0 else 100.0 / abs(odds)
    return model_probability * profit - (1.0 - model_probability)


def market_grade(point_edge: float, probability: float, probability_edge_value: float = 0.0) -> str:
    edge = abs(float(point_edge))
    probability = float(probability)
    probability_edge_value = float(probability_edge_value)
    if edge >= 4.5 and probability >= 0.60:
        return "A"
    if edge >= 3.0 and probability >= 0.565:
        return "B"
    if edge >= 1.75 and probability >= 0.535:
        return "Lean"
    if probability_edge_value >= 0.075 and probability >= 0.57:
        return "B"
    if probability_edge_value >= 0.045 and probability >= 0.54:
        return "Lean"
    return "Pass"


def reliability_score(data_confidence: float, personnel_confidence: float, edge_strength: float, volatility_penalty: float = 0.0) -> float:
    score = (
        0.36 * clamp(data_confidence, 0, 100)
        + 0.34 * clamp(personnel_confidence, 0, 100)
        + 0.30 * clamp(edge_strength, 0, 100)
        - clamp(volatility_penalty, 0, 25)
    )
    return round(clamp(score, 0, 100), 1)


@dataclass
class MarketProjection:
    projected_home_score: float
    projected_away_score: float
    projected_margin: float
    projected_total: float
    home_win_probability: float
    home_cover_probability: float
    over_probability: float
