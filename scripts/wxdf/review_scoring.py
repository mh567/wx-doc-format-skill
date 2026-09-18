from __future__ import annotations

from typing import Any, Mapping

DIMENSION_WEIGHTS: dict[str, float] = {
    "format_conformance": 16.0,
    "heading_hierarchy": 15.0,
    "list_structure": 14.0,
    "table_format": 14.0,
    "toc_structure": 10.0,
    "appendix_structure": 10.0,
    "caption": 7.0,
    "note_structure": 6.0,
    "numbering_page": 8.0,
    # Present only when the semantic checks ran.  The script dimensions keep their
    # original shares, so a plain review scores exactly as it did before the AI
    # dimension existed, and adding AI extends the total rather than diluting it.
    "ai_review": 6.0,
}

# Each finding costs its severity's points, so a dimension's score is
# ``100 - sum(points)`` floored at zero.  The cost is absolute rather than a
# fraction of the check surface: a dimension that examined one item and a
# dimension that examined a thousand both lose the same amount for the same
# defect, which keeps the published score explainable ("one high issue costs 15
# points") instead of depending on how large the document happens to be.
SEVERITY_POINTS: dict[str, int] = {
    "critical": 25,
    "high": 15,
    "medium": 8,
    "low": 3,
}

GRADE_THRESHOLDS: list[tuple[int, str]] = [
    (90, "优"),
    (80, "良"),
    (70, "中"),
    (60, "及格"),
    (0, "差"),
]


def calculate_dimension_score(issues: list[Mapping[str, Any]]) -> float:
    penalty = sum(SEVERITY_POINTS.get(str(issue.get("level", "low")), 3) for issue in issues)
    return max(0.0, 100.0 - penalty)


def calculate_total_score(dimension_results: Mapping[str, Mapping[str, Any]]) -> tuple[float, dict[str, float]]:
    dimension_scores: dict[str, float] = {}
    total_weight = 0.0
    weighted_sum = 0.0

    for dimension, weight in DIMENSION_WEIGHTS.items():
        # A dimension that did not run is left out entirely rather than counted as
        # a clean pass: the review's AI checks only exist when they were asked
        # for, and their absence must not move the score either way.
        if dimension not in dimension_results:
            continue
        result = dimension_results[dimension]
        issues = result.get("issues", [])
        score = calculate_dimension_score(issues)
        dimension_scores[dimension] = round(score, 1)
        weighted_sum += score * weight
        total_weight += weight

    if total_weight <= 0:
        return 0.0, dimension_scores

    total = round(weighted_sum / total_weight, 1)
    return total, dimension_scores


def score_to_grade(score: float) -> str:
    for threshold, grade in GRADE_THRESHOLDS:
        if score >= threshold:
            return grade
    return "差"


def build_scoring_result(dimension_results: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    total_score, dimension_scores = calculate_total_score(dimension_results)
    return {
        "score": total_score,
        "grade": score_to_grade(total_score),
        "dimension_scores": dimension_scores,
        "passed": total_score >= 60,
    }
