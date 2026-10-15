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
    """Exhaustive ground-truth test: compare label setting against all simple paths on a small graph."""
    # Build small 3x3 grid where all simple paths can be enumerated
    G_raw = generate_synthetic_grid_graph(rows=3, cols=3, spacing_m=500.0, seed=42)
    hazard_data = generate_synthetic_hazard_raster(seed=42)
    attach_hazard_to_graph(G_raw, hazard_data)
    G = build_routing_graph(G_raw)

    o = "node_0_0"
    d = "node_2_2"

    # Enumerate all simple paths
    all_paths = list(nx.all_simple_paths(G, source=o, target=d))
    assert len(all_paths) > 0

    evaluated_all = []
    for p in all_paths:
        t = sum(float(G[u][v].get("travel_time", 1.0)) for u, v in zip(p[:-1], p[1:]))
        r = sum(float(G[u][v].get("hazard_exposure", 0.0)) for u, v in zip(p[:-1], p[1:]))
        evaluated_all.append({"path": p, "travel_time": round(t, 2), "hazard_exposure": round(r, 2)})

    ground_truth_frontier = filter_dominated_routes(evaluated_all, objective_keys=("travel_time", "hazard_exposure"))
    gt_objectives = {(r["travel_time"], r["hazard_exposure"]) for r in ground_truth_frontier}

    # Label-setting frontier
    ls_frontier = bi_criteria_label_setting(G, o, d, max_labels_per_node=100)
    ls_objectives = {(r["travel_time"], r["hazard_exposure"]) for r in ls_frontier}

    # Every label setting solution must be non-dominated and match ground truth
    assert ls_objectives == gt_objectives
