"""Pareto multi-objective optimization algorithms for Component 3.

Provides:
- Weighted-sum parameter sweep over alpha in [0.0, 1.0]
- Bi-criteria label-setting shortest path algorithm (Martins' style) for time vs. risk
- Pareto dominance filtering and non-dominated set extraction
"""

from __future__ import annotations

import heapq
from typing import Dict, List, Optional, Set, Tuple, Union, Any
import networkx as nx

from src.route_optimization.baselines import compute_weighted_sum_route


def is_dominated(
    candidate: Tuple[float, float],
    reference: Tuple[float, float],
    tolerance: float = 1e-6,
) -> bool:
    """Check if 'candidate' is dominated by 'reference' in a bi-objective minimization context.

    Candidate is dominated if reference is less than or equal in both objectives,
    and strictly less in at least one objective.

    Args:
        candidate: (obj1, obj2) for candidate solution.
        reference: (obj1, obj2) for reference solution.
        tolerance: Numerical tolerance for equality check.

    Returns:
        bool: True if candidate is dominated by reference.
    """
    c1, c2 = candidate
    r1, r2 = reference

    le1 = (r1 <= c1 + tolerance)
    le2 = (r2 <= c2 + tolerance)
    lt1 = (r1 < c1 - tolerance)
    lt2 = (r2 < c2 - tolerance)

    return le1 and le2 and (lt1 or lt2)


def filter_dominated_routes(
    routes_with_metrics: List[Dict[str, Any]],
    objective_keys: Tuple[str, str] = ("travel_time", "hazard_exposure"),
) -> List[Dict[str, Any]]:
    """Filter a list of route records to retain only non-dominated (Pareto-optimal) routes.

    Args:
        routes_with_metrics: List of dicts, each containing path and evaluation metrics.
        objective_keys: Pair of metric keys to minimize (default: travel_time, hazard_exposure).

    Returns:
        List of non-dominated route records sorted by the first objective.
    """
    if not routes_with_metrics:
        return []

    k1, k2 = objective_keys

    # Remove topological duplicates first
    unique_candidates: List[Dict[str, Any]] = []
    seen_paths: Set[Tuple[Union[str, int], ...]] = set()

    for item in routes_with_metrics:
        path = tuple(item["path"])
        if path not in seen_paths:
            seen_paths.add(path)
            unique_candidates.append(item)

    non_dominated: List[Dict[str, Any]] = []

    for i, item_i in enumerate(unique_candidates):
        pt_i = (float(item_i[k1]), float(item_i[k2]))
        dominated = False

        for j, item_j in enumerate(unique_candidates):
            if i == j:
                continue
            pt_j = (float(item_j[k1]), float(item_j[k2]))

            if is_dominated(pt_i, pt_j):
                dominated = True
                break

        if not dominated:
            item_copy = dict(item_i)
            item_copy["is_pareto_optimal"] = True
            non_dominated.append(item_copy)

    # Sort Pareto front by primary objective (e.g. travel time ascending)
    non_dominated.sort(key=lambda r: float(r[k1]))
    return non_dominated


def pareto_sweep_weighted_sum(
    graph: nx.DiGraph,
    origin: Union[str, int],
    destination: Union[str, int],
    steps: int = 21,
    time_key: str = "travel_time",
    risk_key: str = "hazard_exposure",
) -> List[Dict[str, Any]]:
    """Discover Pareto frontier routes using a systematic weighted-sum alpha sweep.

    Sweeps alpha from 0.0 (quickest) to 1.0 (safest) in discrete steps.

    Args:
        graph: Routing graph.
        origin: Source node.
        destination: Target node.
        steps: Number of sweep steps between 0.0 and 1.0.
        time_key: Travel time edge attribute name.
        risk_key: Risk/hazard edge attribute name.

    Returns:
        List of unique, non-dominated route records with metrics.
    """
    alphas = [i / max(1, steps - 1) for i in range(steps)]
    candidate_records: List[Dict[str, Any]] = []

    for alpha in alphas:
        path = compute_weighted_sum_route(graph, origin, destination, alpha=alpha)
        if not path:
            continue

        # Evaluate path objectives
        total_time = 0.0
        total_risk = 0.0
        total_dist = 0.0
        max_h = 0.0

        for u, v in zip(path[:-1], path[1:]):
            edge = graph[u][v]
            total_time += float(edge.get(time_key, 0.0))
            total_risk += float(edge.get(risk_key, 0.0))
            total_dist += float(edge.get("length", 0.0))
            max_h = max(max_h, float(edge.get("hazard_score", 0.0)))

        candidate_records.append({
            "path": path,
            "alpha": round(alpha, 3),
            "travel_time": round(total_time, 2),
            "hazard_exposure": round(total_risk, 2),
            "distance_m": round(total_dist, 2),
            "max_hazard": round(max_h, 4),
            "edge_count": len(path) - 1,
            "algorithm": f"sweep_alpha_{int(alpha * 100):02d}",
        })

    return filter_dominated_routes(candidate_records, objective_keys=(time_key, risk_key))


def bi_criteria_label_setting(
    graph: nx.DiGraph,
    origin: Union[str, int],
    destination: Union[str, int],
    time_key: str = "travel_time",
    risk_key: str = "hazard_exposure",
    max_labels_per_node: int = 50,
) -> List[Dict[str, Any]]:
    """Compute exact or fine-grained bi-criteria Pareto frontier using label setting.

    Minimizes:
    - Objective 1: Travel time
    - Objective 2: Hazard exposure

    Args:
        graph: Routing graph.
        origin: Source node.
        destination: Target node.
        time_key: Travel time edge key.
        risk_key: Hazard exposure edge key.
        max_labels_per_node: Limit to prevent exponential state explosion on dense networks.

    Returns:
        List of non-dominated route records reaching destination.
    """
    if origin not in graph or destination not in graph:
        return []

    # Priority queue stores tuples: (priority_cost, time, risk, node, path)
    # Using weighted sum or primary objective as priority queue key
    heap: List[Tuple[float, float, float, Union[str, int], Tuple[Union[str, int], ...]]] = []

    # Labels map: node -> list of (time, risk)
    node_labels: Dict[Union[str, int], List[Tuple[float, float]]] = {
        node: [] for node in graph.nodes()
    }

    initial_path = (origin,)
    heapq.heappush(heap, (0.0, 0.0, 0.0, origin, initial_path))
    node_labels[origin].append((0.0, 0.0))

    destination_solutions: List[Dict[str, Any]] = []

    while heap:
        priority, cur_time, cur_risk, cur_node, cur_path = heapq.heappop(heap)

        # If reached destination, collect
        if cur_node == destination:
            destination_solutions.append({
                "path": list(cur_path),
                "travel_time": round(cur_time, 2),
                "hazard_exposure": round(cur_risk, 2),
                "algorithm": "label_setting",
            })
            continue

        # Prune if already dominated by an existing label at cur_node
        cur_pt = (cur_time, cur_risk)
        dominated = False
        for ex_time, ex_risk in node_labels[cur_node]:
            if (ex_time < cur_time and ex_risk <= cur_risk) or (ex_time <= cur_time and ex_risk < cur_risk):
                dominated = True
                break

        if dominated and len(node_labels[cur_node]) > 1:
            continue

        for neighbor in graph.successors(cur_node):
            if neighbor in cur_path:
                continue  # Prevent cycles

            edge = graph[cur_node][neighbor]
            edge_time = float(edge.get(time_key, 1.0))
            edge_risk = float(edge.get(risk_key, 0.0))

            new_time = cur_time + edge_time
            new_risk = cur_risk + edge_risk
            new_pt = (new_time, new_risk)

            # Dominance check against neighbor's existing labels
            is_neighbor_dominated = False
            for n_time, n_risk in node_labels[neighbor]:
                if is_dominated(new_pt, (n_time, n_risk)):
                    is_neighbor_dominated = True
                    break

            if not is_neighbor_dominated:
                # Remove neighbor labels that are dominated by new_pt
                node_labels[neighbor] = [
                    lbl for lbl in node_labels[neighbor]
                    if not is_dominated(lbl, new_pt)
                ]

                if len(node_labels[neighbor]) < max_labels_per_node:
                    node_labels[neighbor].append(new_pt)
                    new_path = cur_path + (neighbor,)
                    # Combined priority heuristic
                    priority_val = new_time + new_risk * 0.1
                    heapq.heappush(heap, (priority_val, new_time, new_risk, neighbor, new_path))

    # Add extra path metadata for destination solutions
    for sol in destination_solutions:
        p = sol["path"]
        dist = sum(float(graph[u][v].get("length", 0.0)) for u, v in zip(p[:-1], p[1:]))
        max_h = max(float(graph[u][v].get("hazard_score", 0.0)) for u, v in zip(p[:-1], p[1:]))
        sol["distance_m"] = round(dist, 2)
        sol["max_hazard"] = round(max_h, 4)
        sol["edge_count"] = len(p) - 1

    return filter_dominated_routes(destination_solutions, objective_keys=(time_key, risk_key))
