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
    normalize_exposure_scale: float = 0.1,
) -> Optional[List[Union[str, int]]]:
    """Compute multi-objective route minimizing linear combination of time and hazard.

    Cost = (1 - alpha) * travel_time + alpha * (hazard_exposure * normalize_scale)

    Args:
        graph: Routing graph with 'travel_time' and 'hazard_exposure'.
        origin: Source node ID.
        destination: Target node ID.
        alpha: Weight parameter in [0.0, 1.0].
            - alpha = 0.0 corresponds to shortest time
            - alpha = 1.0 corresponds to risk-minimal
        normalize_exposure_scale: Scale factor to align dimensional units.

    Returns:
        Ordered list of node IDs along path, or None if no path exists.
    """
    clamped_alpha = max(0.0, min(1.0, float(alpha)))

    def cost_func(u, v, data):
        time = float(data.get("travel_time", 1.0))
        exposure = float(data.get("hazard_exposure", 0.0)) * normalize_exposure_scale
        return (1.0 - clamped_alpha) * time + clamped_alpha * exposure

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
        intermediate_alphas: List of trade-off alphas for weighted sums (e.g. [0.25, 0.5, 0.75]).

    Returns:
        Dict mapping algorithm name to node path list.
    """
    if intermediate_alphas is None:
        intermediate_alphas = [0.25, 0.5, 0.75]

    results: Dict[str, Optional[List[Union[str, int]]]] = {
        "shortest_distance": compute_shortest_distance_route(graph, origin, destination),
        "shortest_time": compute_shortest_time_route(graph, origin, destination),
        "risk_minimal": compute_risk_min_route(graph, origin, destination),
    }

    for a in intermediate_alphas:
        label = f"weighted_sum_alpha_{int(a * 100):02d}"
        results[label] = compute_weighted_sum_route(graph, origin, destination, alpha=a)

    return results
