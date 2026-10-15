"""Tests for Pareto dominance filtering, weighted-sum sweep, and bi-criteria routing."""

import pytest
import networkx as nx

from src.route_optimization.pareto import (
    is_dominated,
    filter_dominated_routes,
    pareto_sweep_weighted_sum,
    bi_criteria_label_setting,
)
from src.route_optimization.synthetic import (
    generate_synthetic_grid_graph,
    generate_synthetic_hazard_raster,
)
from src.route_optimization.attachment import attach_hazard_to_graph
from src.route_optimization.graph_builder import build_routing_graph


def test_is_dominated_logic():
    """Verify Pareto dominance logic in bi-objective minimization."""
    # (time, risk)
    ideal = (10.0, 5.0)
    worse = (15.0, 10.0)
    tradeoff = (8.0, 12.0)
    equal = (10.0, 5.0)

    # worse is dominated by ideal
    assert is_dominated(worse, ideal) is True
    # ideal is NOT dominated by worse
    assert is_dominated(ideal, worse) is False
    # tradeoff is NOT dominated by ideal
    assert is_dominated(tradeoff, ideal) is False
    # identical point does not strictly dominate itself
    assert is_dominated(ideal, equal) is False


def test_filter_dominated_routes():
    """Verify filtering out dominated route records."""
    candidates = [
        {"path": ["A", "B"], "travel_time": 100.0, "hazard_exposure": 50.0}, # Non-dominated (fast)
        {"path": ["A", "C", "B"], "travel_time": 120.0, "hazard_exposure": 20.0}, # Non-dominated (safe)
        {"path": ["A", "D", "B"], "travel_time": 130.0, "hazard_exposure": 60.0}, # Strictly dominated by both
    ]

    pareto_set = filter_dominated_routes(candidates, objective_keys=("travel_time", "hazard_exposure"))
    assert len(pareto_set) == 2
    paths = [p["path"] for p in pareto_set]
    assert ["A", "B"] in paths
    assert ["A", "C", "B"] in paths
    assert ["A", "D", "B"] not in paths


def test_pareto_sweep_and_label_setting():
    """Test Pareto sweep and bi-criteria label-setting on a synthetic graph."""
    G_raw = generate_synthetic_grid_graph(rows=4, cols=4, spacing_m=1000.0, seed=42)
    hazard_data = generate_synthetic_hazard_raster(seed=42)
    attach_hazard_to_graph(G_raw, hazard_data)
    G = build_routing_graph(G_raw)

    origin = "node_0_0"
    destination = "node_3_3"

    sweep_frontier = pareto_sweep_weighted_sum(G, origin, destination, steps=11)
    assert len(sweep_frontier) >= 1

    # Verify every returned solution in frontier has required attributes
    for item in sweep_frontier:
        assert "path" in item
        assert "travel_time" in item
        assert "hazard_exposure" in item
        assert item.get("is_pareto_optimal") is True

    # Test label setting
    label_frontier = bi_criteria_label_setting(G, origin, destination, max_labels_per_node=20)
    assert len(label_frontier) >= 1
    for sol in label_frontier:
        assert sol["path"][0] == origin
        assert sol["path"][-1] == destination


def test_label_setting_vs_brute_force_all_simple_paths():
    """Exhaustive ground-truth test: compare label setting against all simple paths on a 4x4 grid.

    Constructs a 4x4 grid with randomized non-unit edge times and hazards (approx. 184 corner-to-corner paths)
    to ensure tie-breaking artifacts do not conceal domination bugs.
    """
    import random
    rng = random.Random(42)

    # Build 4x4 directed grid graph
    G = nx.DiGraph()
    rows, cols = 4, 4
    for r in range(rows):
        for c in range(cols):
            node_id = f"node_{r}_{c}"
            G.add_node(node_id, x=80.35 + c * 0.01, y=6.65 + r * 0.01)

    # Add directed edges (right and up) with random heterogeneous times and hazard exposures
    for r in range(rows):
        for c in range(cols):
            u = f"node_{r}_{c}"
            if c + 1 < cols:
                v_right = f"node_{r}_{c+1}"
                t_r = round(rng.uniform(10.0, 50.0), 2)
                h_r = round(rng.uniform(5.0, 80.0), 2)
                l_r = round(t_r * 15.0, 1)
                G.add_edge(u, v_right, travel_time=t_r, hazard_exposure=h_r, hazard_score=min(1.0, h_r/100.0), length=l_r)
            if r + 1 < rows:
                v_up = f"node_{r+1}_{c}"
                t_u = round(rng.uniform(10.0, 50.0), 2)
                h_u = round(rng.uniform(5.0, 80.0), 2)
                l_u = round(t_u * 15.0, 1)
                G.add_edge(u, v_up, travel_time=t_u, hazard_exposure=h_u, hazard_score=min(1.0, h_u/100.0), length=l_u)

    o = "node_0_0"
    d = "node_3_3"

    # Enumerate all simple paths (in a 4x4 DAG with right/up edges, exactly 184 simple paths or comb(6,3) = 20 shortest DAG paths)
    # To have ~184 paths, also add some backwards/diagonal edges or bi-directional horizontal edges:
    for r in range(rows):
        for c in range(cols - 1, 0, -1):
            if rng.random() < 0.4:
                u = f"node_{r}_{c}"
                v_left = f"node_{r}_{c-1}"
                t_l = round(rng.uniform(15.0, 60.0), 2)
                h_l = round(rng.uniform(10.0, 90.0), 2)
                G.add_edge(u, v_left, travel_time=t_l, hazard_exposure=h_l, hazard_score=min(1.0, h_l/100.0), length=round(t_l * 12.0, 1))

    all_paths = list(nx.all_simple_paths(G, source=o, target=d))
    assert len(all_paths) >= 20, f"Expected at least 20 simple paths, got {len(all_paths)}"

    evaluated_all = []
    for p in all_paths:
        t = sum(float(G[u][v]["travel_time"]) for u, v in zip(p[:-1], p[1:]))
        r = sum(float(G[u][v]["hazard_exposure"]) for u, v in zip(p[:-1], p[1:]))
        evaluated_all.append({"path": p, "travel_time": round(t, 2), "hazard_exposure": round(r, 2)})

    ground_truth_frontier = filter_dominated_routes(evaluated_all, objective_keys=("travel_time", "hazard_exposure"))
    gt_objectives = {(r["travel_time"], r["hazard_exposure"]) for r in ground_truth_frontier}

    # Label-setting frontier
    ls_frontier = bi_criteria_label_setting(G, o, d, max_labels_per_node=200)
    ls_objectives = {(r["travel_time"], r["hazard_exposure"]) for r in ls_frontier}

    # Every label setting solution must be non-dominated and exactly match the true Pareto objective set
    assert ls_objectives == gt_objectives
