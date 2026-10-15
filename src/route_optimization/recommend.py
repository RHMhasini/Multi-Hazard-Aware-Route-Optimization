"""Transparent route recommendation engine for Component 3.

Applies an auditable, rule-based decision protocol to recommend the optimal compromise route
from candidate Pareto and baseline paths, providing clear human-readable explanations.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Union
import numpy as np
import networkx as nx

from contracts.schemas import C3RouteResultContract, C3EvaluationMetricsContract
from src.route_optimization.config import RouteOptimizationConfig, DEFAULT_CONFIG
from src.route_optimization.metrics import calculate_route_metrics


def recommend_optimal_route(
    graph: nx.DiGraph,
    candidate_routes: List[Dict[str, Any]],
    config: Optional[RouteOptimizationConfig] = None,
    user_max_hazard: Optional[float] = None,
    user_max_detour_ratio: Optional[float] = None,
) -> C3RouteResultContract:
    """Recommend the optimal compromise route based on transparent, deterministic multi-criteria rules.

    Decision Protocol:
    1. Establish Reference Baselines: Identify quickest route time (T_min) and safest route exposure (R_min).
    2. Safety Screening: Filter out routes with max edge hazard exceeding safety ceiling (user-specified or config.hazard_critical_threshold), unless no alternative exists.
    3. Detour Screening: Exclude routes with travel time > max_detour_ratio * T_min.
    4. Compromise Knee-Point Selection: On admissible routes, find the knee point minimizing normalized Euclidean distance to the Utopian Ideal Point (T_min, R_min).
    5. Audit Trail Generation: Construct an explainable justification log for the decision.

    Args:
        graph: Routing graph.
        candidate_routes: List of dicts, each with 'path' and metrics or algorithm labels.
        config: Optimization configuration.
        user_max_hazard: Optional user-specified maximum acceptable edge hazard.
        user_max_detour_ratio: Optional user-specified maximum travel time multiplier.

    Returns:
        C3RouteResultContract containing recommended path, metrics, and audit explanation.
    """
    cfg = config or DEFAULT_CONFIG
    max_hazard_limit = user_max_hazard if user_max_hazard is not None else cfg.hazard_critical_threshold
    detour_limit = user_max_detour_ratio if user_max_detour_ratio is not None else cfg.max_detour_ratio

    if not candidate_routes:
        raise ValueError("Cannot recommend route: candidate routes list is empty.")

    # Calculate metrics for all candidates
    evaluated_candidates = []
    for idx, c in enumerate(candidate_routes):
        path = c["path"]
        metrics = calculate_route_metrics(graph, path, config=cfg)
        evaluated_candidates.append({
            "candidate_id": f"route_{idx}",
            "algorithm": c.get("algorithm", f"candidate_{idx}"),
            "path": path,
            "metrics": metrics,
            "time": metrics.total_travel_time_sec,
            "risk": metrics.cumulative_hazard_exposure,
            "max_h": metrics.max_edge_hazard,
        })

    # Step 1: Baselines
    min_time = min(c["time"] for c in evaluated_candidates)
    max_time = max(c["time"] for c in evaluated_candidates)
    min_risk = min(c["risk"] for c in evaluated_candidates)
    max_risk = max(c["risk"] for c in evaluated_candidates)

    quickest = min(evaluated_candidates, key=lambda c: c["time"])
    safest = min(evaluated_candidates, key=lambda c: c["risk"])

    # Step 2: Safety filtering
    safe_candidates = [c for c in evaluated_candidates if c["max_h"] <= max_hazard_limit]
    safety_fallback = False
    if not safe_candidates:
        # If no route is below threshold, relax ceiling to keep all candidate routes
        # allowing the knee-point compromise selection to evaluate the full trade-off spectrum
        safety_fallback = True
        safe_candidates = list(evaluated_candidates)

    # Step 3: Detour filtering
    max_allowed_time = min_time * detour_limit
    admissible = [c for c in safe_candidates if c["time"] <= max_allowed_time]
    detour_fallback = False
    if not admissible:
        detour_fallback = True
        admissible = safe_candidates

    # Step 4: Knee-Point Compromise Selection
    # Normalize objectives to [0, 1]
    time_span = max(1e-4, max_time - min_time)
    risk_span = max(1e-4, max_risk - min_risk)

    best_candidate = None
    min_distance_to_ideal = float("inf")

    for c in admissible:
        norm_t = (c["time"] - min_time) / time_span
        norm_r = (c["risk"] - min_risk) / risk_span
        # L2 Euclidean distance to ideal point (0.0, 0.0)
        dist_to_ideal = math.hypot(norm_t, norm_r)

        if dist_to_ideal < min_distance_to_ideal:
            min_distance_to_ideal = dist_to_ideal
            best_candidate = c

    if best_candidate is None:
        best_candidate = admissible[0]

    # Step 5: Construct transparent audit trail
    audit_lines = [
        "=== Component 3 Route Recommendation Audit ===",
        "NOTE: Route recommendation is an auditable, multi-criteria decision rule (Utopian Knee-Point).",
        "The selection depends on the candidate pool and normalization bounds, pending final supervisor policy alignment.",
        f"1. Baseline Quickest: Time={quickest['time']:.1f}s, MaxHazard={quickest['max_h']:.3f}, Exposure={quickest['risk']:.1f} hazard·metres",
        f"2. Baseline Safest: Time={safest['time']:.1f}s, MaxHazard={safest['max_h']:.3f}, Exposure={safest['risk']:.1f} hazard·metres",
        f"3. Safety Ceiling: Hazard Threshold <= {max_hazard_limit:.2f} ({'ENFORCED' if not safety_fallback else 'RELAXED - all paths exceed limit'})",
        f"4. Detour Budget: Time <= {max_allowed_time:.1f}s ({detour_limit * 100:.0f}% of fastest route)",
    ]

    time_diff_pct = ((best_candidate["time"] - min_time) / max(1e-4, min_time)) * 100.0
    risk_reduction_pct = ((quickest["risk"] - best_candidate["risk"]) / max(1e-4, quickest["risk"])) * 100.0 if quickest["risk"] > 0 else 0.0

    audit_lines.append(
        f"5. Recommendation Decision: Selected '{best_candidate['algorithm']}'. "
        f"Achieves {risk_reduction_pct:.1f}% risk reduction over fastest route with only "
        f"{time_diff_pct:.1f}% time detour (Normalized L2 Distance to Utopian Point: {min_distance_to_ideal:.3f})."
    )

    return C3RouteResultContract(
        route_id=best_candidate["candidate_id"],
        algorithm=best_candidate["algorithm"],
        node_path=best_candidate["path"],
        metrics=best_candidate["metrics"],
        audit_notes="\n".join(audit_lines),
    )
