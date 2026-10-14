"""Disaster scenario analysis and high-risk edge exclusion engine for Component 3.

Simulates extreme disaster conditions by:
- Dynamically severing impassable road edges where hazard R_e >= critical threshold
- Applying severe transit penalties to cautionary hazard zones
- Evaluating network reachability, topological degradation, and resilience
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple, Union
import networkx as nx


def apply_hazard_exclusion(
    graph: nx.DiGraph,
    hazard_threshold: float = 0.80,
    inplace: bool = False,
) -> nx.DiGraph:
    """Filter out or remove directed edges where hazard_score >= hazard_threshold.

    Simulates road closures due to localized washouts, severe flooding, or debris flows.

    Args:
        graph: Routing graph with 'hazard_score' on edges.
        hazard_threshold: Cut-off threshold above which edges are completely severed (default: 0.80).
        inplace: If True, mutates the input graph; otherwise operates on a copy.

    Returns:
        nx.DiGraph: Pruned graph with impassable edges removed and exclusion metadata attached in .graph.
    """
    g = graph if inplace else graph.copy()

    edges_to_remove: List[Tuple[Union[str, int], Union[str, int]]] = []
    severed_records: List[Dict[str, Any]] = []

    for u, v, data in g.edges(data=True):
        hazard_val = float(data.get("hazard_score", data.get("max_hazard", 0.0)))
        if hazard_val >= hazard_threshold:
            edges_to_remove.append((u, v))
            severed_records.append({
                "u": u,
                "v": v,
                "length_m": float(data.get("length", 0.0)),
                "hazard_score": hazard_val,
                "highway": data.get("highway", "unknown"),
            })

    for u, v in edges_to_remove:
        g.remove_edge(u, v)

    # Attach audit metadata to the graph
    g.graph["exclusion_report"] = {
        "hazard_threshold": hazard_threshold,
        "total_initial_edges": len(graph.edges()),
        "severed_edges_count": len(edges_to_remove),
        "active_edges_count": len(g.edges()),
        "severed_percentage": round(len(edges_to_remove) / max(1, len(graph.edges())) * 100.0, 2),
        "severed_details": severed_records,
    }

    return g


def apply_hazard_penalty_scenario(
    graph: nx.DiGraph,
    warning_threshold: float = 0.50,
    penalty_multiplier: float = 5.0,
    inplace: bool = False,
) -> nx.DiGraph:
    """Apply severe transit penalties to cautionary hazard zones without completely severing them.

    Multiplies travel time and routing impedance by penalty_multiplier for edges exceeding warning_threshold.

    Args:
        graph: Routing graph.
        warning_threshold: Hazard level where speed is reduced and risk penalty is imposed (default: 0.50).
        penalty_multiplier: Factor by which travel time / impedance increases.
        inplace: If True, modifies input graph directly.

    Returns:
        nx.DiGraph: Graph with penalized edge costs.
    """
    g = graph if inplace else graph.copy()

    penalized_count = 0
    for u, v, data in g.edges(data=True):
        hazard_val = float(data.get("hazard_score", data.get("max_hazard", 0.0)))
        if hazard_val >= warning_threshold:
            penalized_count += 1
            data["travel_time"] = round(float(data.get("travel_time", 1.0)) * penalty_multiplier, 2)
            data["impedance"] = round(float(data.get("impedance", 1.0)) * penalty_multiplier, 2)
            data["is_penalized"] = True

    g.graph["penalty_report"] = {
        "warning_threshold": warning_threshold,
        "penalty_multiplier": penalty_multiplier,
        "penalized_edges_count": penalized_count,
    }

    return g


def check_scenario_reachability(
    graph: nx.DiGraph,
    origin: Union[str, int],
    destination: Union[str, int],
) -> bool:
    """Check whether a valid directed path exists between origin and destination.

    Args:
        graph: Routing graph (potentially post-hazard-exclusion).
        origin: Source node ID.
        destination: Target node ID.

    Returns:
        bool: True if destination is reachable from origin, False otherwise.
    """
    if origin not in graph or destination not in graph:
        return False
    return nx.has_path(graph, source=origin, target=destination)


def evaluate_network_resilience(
    graph: nx.DiGraph,
    thresholds: Optional[List[float]] = None,
) -> List[Dict[str, Any]]:
    """Evaluate network resilience by measuring topological degradation across hazard thresholds.

    Args:
        graph: Original routing graph with hazard scores attached.
        thresholds: List of hazard exclusion thresholds to test (default: [0.95, 0.85, 0.70, 0.50]).

    Returns:
        List of dicts documenting network status under each hazard scenario.
    """
    if thresholds is None:
        thresholds = [0.95, 0.85, 0.70, 0.50]

    resilience_results: List[Dict[str, Any]] = []
    initial_edges = len(graph.edges())
    initial_nodes = len(graph.nodes())

    for thresh in thresholds:
        pruned_g = apply_hazard_exclusion(graph, hazard_threshold=thresh, inplace=False)
        active_edges = len(pruned_g.edges())
        severed_edges = initial_edges - active_edges

        # Analyze connectivity
        num_components = nx.number_weakly_connected_components(pruned_g)

        resilience_results.append({
            "threshold": thresh,
            "initial_edges": initial_edges,
            "active_edges": active_edges,
            "severed_edges": severed_edges,
            "severed_pct": round(severed_edges / max(1, initial_edges) * 100.0, 2),
            "weakly_connected_components": num_components,
        })

    return resilience_results
