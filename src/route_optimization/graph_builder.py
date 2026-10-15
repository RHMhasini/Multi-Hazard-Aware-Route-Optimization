"""Routing graph builder for Component 3.

Transforms a raw or multigraph network into a routing-ready directed graph annotated with:
- Metric length (meters)
- Travel speed (km/h) and free-flow travel time (seconds)
- Attached hazard attributes (max hazard, exposure)
- Edge impedance weights under various risk formulations
"""

from __future__ import annotations

import math
from typing import Optional, Union
import numpy as np
import networkx as nx

from src.route_optimization.config import (
    RiskFormulaChoice,
    RouteOptimizationConfig,
    DEFAULT_CONFIG,
)


def compute_edge_impedance(
    length: float,
    travel_time: float,
    hazard_score: float,
    config: RouteOptimizationConfig,
    custom_alpha: Optional[float] = None,
) -> float:
    """Calculate the routing impedance (cost) for an edge under the configured risk formula.

    Args:
        length: Segment length in meters.
        travel_time: Free-flow travel time in seconds.
        hazard_score: Normalized hazard intensity in [0.0, 1.0].
        config: Optimization configuration parameters.
        custom_alpha: Optional override for tradeoff weight alpha in [0.0, 1.0].

    Returns:
        float: Non-negative routing cost for Dijkstra / shortest-path search.
    """
    alpha = custom_alpha if custom_alpha is not None else config.alpha_tradeoff
    formula = config.risk_formula

    if formula == RiskFormulaChoice.EXPONENTIAL:
        # Exponential penalty rapidly inflates cost as hazard approaches 1.0
        # cost = time * exp(beta * hazard)
        cost = travel_time * math.exp(config.exponential_beta * hazard_score)

    elif formula == RiskFormulaChoice.THRESHOLD_PENALTY:
        # Step-penalty when hazard crosses safety threshold
        if hazard_score >= config.hazard_impassable_threshold:
            cost = 1e9 # Effectively impassable
        elif hazard_score >= config.hazard_critical_threshold:
            cost = travel_time * config.threshold_penalty_multiplier
        else:
            cost = travel_time

    elif formula == RiskFormulaChoice.TIME_WEIGHTED:
        # Risk integrated over time spent on segment
        time_exposure = travel_time * hazard_score
        cost = (1.0 - alpha) * travel_time + alpha * (time_exposure * 10.0)

    elif formula == RiskFormulaChoice.MAX_EDGE_HAZARD:
        # Emphasizes high hazard reduction, with small tie-breaker for time
        cost = (hazard_score ** 2) * 1000.0 + travel_time

    else: # Default LINEAR
        # Normalized weighted sum of travel time and length-weighted hazard exposure
        # Normalize exposure relative to nominal distance/time scale
        exposure = length * hazard_score
        cost = (1.0 - alpha) * travel_time + alpha * (exposure * 0.1)

    return max(0.001, float(cost))


def build_routing_graph(
    input_graph: Union[nx.Graph, nx.DiGraph, nx.MultiDiGraph],
    config: Optional[RouteOptimizationConfig] = None,
    simplify_multigraph: bool = True,
) -> nx.DiGraph:
    """Construct a clean, single-edge directed graph optimized for shortest path & Pareto routing.

    Args:
        input_graph: Source graph (NetworkX DiGraph or MultiDiGraph).
        config: Configuration instance with speed profiles and risk formulas.
        simplify_multigraph: If True, condenses parallel edges between (u, v) into the lowest-time edge.

    Returns:
        nx.DiGraph: Routing-ready directed graph.
    """
    cfg = config or DEFAULT_CONFIG
    routing_graph = nx.DiGraph()

    # Copy nodes and their coordinates
    for node_id, data in input_graph.nodes(data=True):
        routing_graph.add_node(
            node_id,
            x=data.get("x", 0.0),
            y=data.get("y", 0.0),
            **{k: v for k, v in data.items() if k not in ["x", "y"]}
        )

    is_multigraph = isinstance(input_graph, (nx.MultiGraph, nx.MultiDiGraph))
    edge_iter = input_graph.edges(keys=True, data=True) if is_multigraph else input_graph.edges(data=True)

    for edge in edge_iter:
        if is_multigraph:
            u, v, key, data = edge
        else:
            u, v, data = edge

        # Ensure valid length
        length_m = float(data.get("length", 100.0))
        if length_m <= 0:
            length_m = 1.0

        highway = data.get("highway", "unclassified")
        speed_kph = float(data.get("speed_kph") or cfg.get_speed_for_highway(highway))
        speed_mps = max(1.0, speed_kph * 1000.0 / 3600.0)

        travel_time_sec = float(data.get("travel_time") or (length_m / speed_mps))
        hazard_score = float(data.get("hazard_score", data.get("max_hazard", 0.0)))
        hazard_score = float(np.clip(hazard_score, 0.0, 1.0))
        hazard_exposure = float(data.get("hazard_exposure", length_m * hazard_score))

        impedance = compute_edge_impedance(
            length=length_m,
            travel_time=travel_time_sec,
            hazard_score=hazard_score,
            config=cfg,
        )

        edge_attrs = {
            "length": round(length_m, 2),
            "speed_kph": speed_kph,
            "travel_time": round(travel_time_sec, 2),
            "hazard_score": round(hazard_score, 4),
            "hazard_exposure": round(hazard_exposure, 2),
            "impedance": round(impedance, 4),
            "highway": str(highway),
        }
        if "geometry" in data:
            edge_attrs["geometry"] = data["geometry"]

        # Handle parallel edges
        if routing_graph.has_edge(u, v) and simplify_multigraph:
            existing = routing_graph[u][v]
            # Retain edge with lower travel time
            if travel_time_sec < existing["travel_time"]:
                routing_graph[u][v].update(edge_attrs)
        else:
            routing_graph.add_edge(u, v, **edge_attrs)

        # If original graph was undirected, add reverse directed edge
        if not input_graph.is_directed():
            if routing_graph.has_edge(v, u) and simplify_multigraph:
                existing = routing_graph[v][u]
                if travel_time_sec < existing["travel_time"]:
                    routing_graph[v][u].update(edge_attrs)
            else:
                routing_graph.add_edge(v, u, **edge_attrs)

    return routing_graph
