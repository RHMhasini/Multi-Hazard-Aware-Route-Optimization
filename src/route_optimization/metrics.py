"""Route evaluation metrics and performance comparisons for Component 3.

Computes:
- Total travel time (seconds and minutes)
- Total path distance (meters and kilometers)
- Cumulative hazard exposure (integrated length x hazard)
- Maximum edge hazard encountered along the route
- Threshold safety breach penalties
- Comparative summary tables across multiple candidate routes
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union
import numpy as np
import pandas as pd
import networkx as nx

from contracts.schemas import C3EvaluationMetricsContract
from src.route_optimization.config import RouteOptimizationConfig, DEFAULT_CONFIG


def calculate_route_metrics(
    graph: nx.DiGraph,
    node_path: List[Union[str, int]],
    config: Optional[RouteOptimizationConfig] = None,
) -> C3EvaluationMetricsContract:
    """Calculate comprehensive evaluation metrics for a single node path.

    Args:
        graph: Routing graph with length, travel_time, hazard_score edge attributes.
        node_path: Ordered list of node IDs.
        config: Optimization configuration containing hazard thresholds.

    Returns:
        C3EvaluationMetricsContract with verified metrics.
    """
    cfg = config or DEFAULT_CONFIG

    if not node_path or len(node_path) < 2:
        return C3EvaluationMetricsContract(
            total_travel_time_sec=0.0,
            total_distance_m=0.0,
            cumulative_hazard_exposure=0.0,
            max_edge_hazard=0.0,
            threshold_penalty=0.0,
            edge_count=0,
        )

    total_time = 0.0
    total_distance = 0.0
    cumulative_exposure = 0.0
    max_hazard = 0.0
    penalty = 0.0

    critical_thresh = cfg.hazard_critical_threshold

    for u, v in zip(node_path[:-1], node_path[1:]):
        if not graph.has_edge(u, v):
            raise KeyError(f"Graph does not contain directed edge ({u}, {v})")

        edge = graph[u][v]
        length = float(edge.get("length", 0.0))
        time = float(edge.get("travel_time", 0.0))
        h_score = float(edge.get("hazard_score", 0.0))

        total_distance += length
        total_time += time
        # Use attached edge hazard_exposure if present to ensure route metric equals exact sum of edge values (NFR2)
        edge_exposure = float(edge.get("hazard_exposure", length * h_score))
        cumulative_exposure += edge_exposure
        max_hazard = max(max_hazard, h_score)

        # Apply safety penalty if edge exceeds critical threshold
        if h_score >= critical_thresh:
            severity_excess = h_score - critical_thresh
            penalty += (severity_excess * length * cfg.threshold_penalty_multiplier)

    return C3EvaluationMetricsContract(
        total_travel_time_sec=round(total_time, 2),
        total_distance_m=round(total_distance, 2),
        cumulative_hazard_exposure=round(cumulative_exposure, 2),
        max_edge_hazard=round(max_hazard, 4),
        threshold_penalty=round(penalty, 2),
        edge_count=len(node_path) - 1,
    )


def compare_routes(
    graph: nx.DiGraph,
    named_routes: Dict[str, Optional[List[Union[str, int]]]],
    config: Optional[RouteOptimizationConfig] = None,
) -> pd.DataFrame:
    """Generate a side-by-side comparison table of multiple routing solutions.

    Args:
        graph: Routing graph.
        named_routes: Dict mapping strategy names to node paths.
        config: Optimization configuration.

    Returns:
        pd.DataFrame: Comparative metric table with normalized columns.
    """
    records: List[Dict[str, Any]] = []

    for name, path in named_routes.items():
        if path is None:
            continue

        metrics = calculate_route_metrics(graph, path, config=config)
        records.append({
            "route_name": name,
            "travel_time_sec": metrics.total_travel_time_sec,
            "travel_time_min": round(metrics.total_travel_time_sec / 60.0, 2),
            "distance_km": round(metrics.total_distance_m / 1000.0, 2),
            "cumulative_exposure": metrics.cumulative_hazard_exposure,
            "max_edge_hazard": metrics.max_edge_hazard,
            "threshold_penalty": metrics.threshold_penalty,
            "edge_count": metrics.edge_count,
        })

    df = pd.DataFrame(records)

    # Calculate percentage trade-offs relative to the quickest route if available
    if not df.empty and "shortest_time" in df["route_name"].values:
        baseline_time = df.loc[df["route_name"] == "shortest_time", "travel_time_sec"].iloc[0]
        if baseline_time > 0:
            df["time_detour_pct"] = (
                (df["travel_time_sec"] - baseline_time) / baseline_time * 100.0
            ).round(1)

    return df
