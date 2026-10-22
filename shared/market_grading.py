"""Pure shared spread/total grading thresholds, without UI imports."""


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
