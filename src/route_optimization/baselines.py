"""Baseline routing algorithms for Component 3.

Implements benchmark routing strategies:
- Shortest-distance route (minimizing total distance in meters)
- Shortest-time route (free-flow quickest travel time)
- Risk-minimal route (minimizing cumulative hazard exposure)
- Weighted-sum multi-objective routes (linear combination of travel time and hazard exposure)
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple, Union, Any
import networkx as nx

from src.route_optimization.config import RouteOptimizationConfig, DEFAULT_CONFIG


def compute_shortest_distance_route(
    graph: nx.DiGraph,
    origin: Union[str, int],
    destination: Union[str, int],
) -> Optional[List[Union[str, int]]]:
    """Compute shortest path by geometric length (meters).

    Args:
        graph: Routing graph with 'length' edge attribute.
        origin: Source node ID.
        destination: Target node ID.

    Returns:
        Ordered list of node IDs along path, or None if no path exists.
    """
    try:
        return nx.shortest_path(graph, source=origin, target=destination, weight="length")
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return None


def compute_shortest_time_route(
    graph: nx.DiGraph,
    origin: Union[str, int],
    destination: Union[str, int],
) -> Optional[List[Union[str, int]]]:
    """Compute quickest route by free-flow travel time (seconds).

    Args:
        graph: Routing graph with 'travel_time' edge attribute.
        origin: Source node ID.
        destination: Target node ID.

    Returns:
        Ordered list of node IDs along path, or None if no path exists.
    """
    try:
        return nx.shortest_path(graph, source=origin, target=destination, weight="travel_time")
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return None


def compute_risk_min_route(
    graph: nx.DiGraph,
    origin: Union[str, int],
    destination: Union[str, int],
    weight_attribute: str = "hazard_exposure",
) -> Optional[List[Union[str, int]]]:
    """Compute the safest route minimizing cumulative hazard exposure.

    Args:
        graph: Routing graph with hazard weight attributes.
        origin: Source node ID.
        destination: Target node ID.
        weight_attribute: Attribute to minimize ('hazard_exposure' or 'hazard_score').

    Returns:
        Ordered list of node IDs along path, or None if no path exists.
    """
    # Define custom weight function to break ties with travel time
    def risk_weight(u, v, data):
        primary_risk = float(data.get(weight_attribute, 0.0))
        # Small epsilon on travel time ensures simple acyclic path in zero-risk zones
        tie_breaker = float(data.get("travel_time", 1.0)) * 1e-4
        return primary_risk + tie_breaker

    try:
        return nx.shortest_path(graph, source=origin, target=destination, weight=risk_weight)
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return None


def compute_weighted_sum_route(
    graph: nx.DiGraph,
    origin: Union[str, int],
    destination: Union[str, int],
    alpha: float = 0.5,
    t_ref: Optional[float] = None,
    r_ref: Optional[float] = None,
) -> Optional[List[Union[str, int]]]:
    """Compute multi-objective route minimizing normalized linear combination of time and hazard.

    Consistent with the proposal formulation:
        Cost = alpha * (travel_time / t_ref) + (1 - alpha) * (hazard_exposure / r_ref)
    where alpha in [0.0, 1.0]:
        - alpha = 1.0 corresponds to shortest time only
        - alpha = 0.0 corresponds to risk-minimal only

    Args:
        graph: Routing graph with 'travel_time' and 'hazard_exposure'.
        origin: Source node ID.
        destination: Target node ID.
        alpha: Weight parameter in [0.0, 1.0] placed on travel time.
        t_ref: Normalization baseline for travel time (e.g. from shortest time route).
        r_ref: Normalization baseline for hazard exposure (e.g. from shortest time or safest route).

    Returns:
        Ordered list of node IDs along path, or None if no path exists.
    """
    clamped_alpha = max(0.0, min(1.0, float(alpha)))

    # If alpha is 1.0, directly compute shortest time
    if clamped_alpha >= 0.9999:
        return compute_shortest_time_route(graph, origin, destination)
    # If alpha is 0.0, directly compute risk minimal
    if clamped_alpha <= 0.0001:
        return compute_risk_min_route(graph, origin, destination)

    # Automatically derive reference scales per OD pair if not provided
    if t_ref is None or r_ref is None:
        fast_path = compute_shortest_time_route(graph, origin, destination)
        if fast_path is None:
            return None
        calc_t = sum(float(graph[u][v].get("travel_time", 1.0)) for u, v in zip(fast_path[:-1], fast_path[1:]))
        calc_r = sum(float(graph[u][v].get("hazard_exposure", 0.0)) for u, v in zip(fast_path[:-1], fast_path[1:]))

        if t_ref is None:
            t_ref = max(1e-4, calc_t)
        if r_ref is None:
            # If fastest path has zero exposure, check safest path exposure or default
            if calc_r <= 1e-4:
                safe_p = compute_risk_min_route(graph, origin, destination)
                calc_r = sum(float(graph[u][v].get("hazard_exposure", 0.0)) for u, v in zip(safe_p[:-1], safe_p[1:])) if safe_p else 0.0
            r_ref = max(1e-4, calc_r if calc_r > 1e-4 else 100.0)
    else:
        # User or caller supplied references: guard against zero division
        t_ref = max(1e-4, float(t_ref))
        r_ref = max(1e-4, float(r_ref))

    def cost_func(u, v, data):
        time = float(data.get("travel_time", 1.0))
        exposure = float(data.get("hazard_exposure", 0.0))
        # Non-dimensional normalized combination
        return clamped_alpha * (time / t_ref) + (1.0 - clamped_alpha) * (exposure / r_ref)

    try:
        return nx.shortest_path(graph, source=origin, target=destination, weight=cost_func)
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return None


def run_all_baselines(
    graph: nx.DiGraph,
    origin: Union[str, int],
    destination: Union[str, int],
    intermediate_alphas: Optional[List[float]] = None,
) -> Dict[str, Optional[List[Union[str, int]]]]:
    """Execute all standard baseline routing algorithms for a given OD pair.

    Args:
        graph: Routing graph.
        origin: Source node ID.
        destination: Target node ID.
        intermediate_alphas: List of trade-off alphas for weighted sums (e.g. [0.75, 0.50, 0.25]).

    Returns:
        Dict mapping algorithm name to node path list.
    """
    if intermediate_alphas is None:
        intermediate_alphas = [0.75, 0.50, 0.25]

    fastest_path = compute_shortest_time_route(graph, origin, destination)
    safest_path = compute_risk_min_route(graph, origin, destination)

    t_ref = 1.0
    r_ref = 1.0
    if fastest_path:
        t_ref = max(1.0, sum(float(graph[u][v].get("travel_time", 1.0)) for u, v in zip(fastest_path[:-1], fastest_path[1:])))
        r_ref = max(1.0, sum(float(graph[u][v].get("hazard_exposure", 0.0)) for u, v in zip(fastest_path[:-1], fastest_path[1:])))

    results: Dict[str, Optional[List[Union[str, int]]]] = {
        "shortest_distance": compute_shortest_distance_route(graph, origin, destination),
        "shortest_time": fastest_path,
        "risk_minimal": safest_path,
    }

    for a in intermediate_alphas:
        label = f"weighted_sum_alpha_{int(a * 100):02d}"
        results[label] = compute_weighted_sum_route(graph, origin, destination, alpha=a, t_ref=t_ref, r_ref=r_ref)

    return results
