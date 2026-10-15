"""Tests for routing algorithms, baselines, metrics, and recommendations."""

import pytest
import networkx as nx

from src.route_optimization.config import RouteOptimizationConfig, RiskFormulaChoice
from src.route_optimization.synthetic import (
    generate_synthetic_grid_graph,
    generate_synthetic_hazard_raster,
)
from src.route_optimization.attachment import attach_hazard_to_graph
from src.route_optimization.graph_builder import build_routing_graph
from src.route_optimization.baselines import (
    compute_shortest_distance_route,
    compute_shortest_time_route,
    compute_risk_min_route,
    compute_weighted_sum_route,
    run_all_baselines,
)
from src.route_optimization.metrics import (
    calculate_route_metrics,
    compare_routes,
)
from src.route_optimization.recommend import recommend_optimal_route


@pytest.fixture
def routing_setup():
    """Build a standard 5x5 grid routing graph with attached hazard values."""
    G_raw = generate_synthetic_grid_graph(rows=5, cols=5, spacing_m=800.0, seed=42)
    hazard_data = generate_synthetic_hazard_raster(seed=42)
    attach_hazard_to_graph(G_raw, hazard_data)
    routing_G = build_routing_graph(G_raw)
    return routing_G


def test_baseline_routes(routing_setup):
    """Test standard baseline routes: distance, time, risk-min, and weighted-sum."""
    G = routing_setup
    origin = "node_0_0"
    destination = "node_4_4"

    dist_route = compute_shortest_distance_route(G, origin, destination)
    time_route = compute_shortest_time_route(G, origin, destination)
    risk_route = compute_risk_min_route(G, origin, destination)

    assert dist_route is not None
    assert time_route is not None
    assert risk_route is not None

    assert dist_route[0] == origin and dist_route[-1] == destination
    assert time_route[0] == origin and time_route[-1] == destination
    assert risk_route[0] == origin and risk_route[-1] == destination

    # Evaluate metrics
    metrics_time = calculate_route_metrics(G, time_route)
    metrics_risk = calculate_route_metrics(G, risk_route)

    # Time route should be faster or equal to risk-min route
    assert metrics_time.total_travel_time_sec <= metrics_risk.total_travel_time_sec
    # Risk-min route should have less or equal exposure than time route
    assert metrics_risk.cumulative_hazard_exposure <= metrics_time.cumulative_hazard_exposure


def test_run_all_baselines(routing_setup):
    """Test batch baseline runner and comparative dataframe generation."""
    G = routing_setup
    origin = "node_0_0"
    destination = "node_4_4"

    baselines = run_all_baselines(G, origin, destination, intermediate_alphas=[0.5])
    assert "shortest_distance" in baselines
    assert "shortest_time" in baselines
    assert "risk_minimal" in baselines
    assert "weighted_sum_alpha_50" in baselines

    comparison_df = compare_routes(G, baselines)
    assert not comparison_df.empty
    assert "route_name" in comparison_df.columns
    assert "travel_time_sec" in comparison_df.columns
    assert "cumulative_exposure" in comparison_df.columns


def test_recommend_optimal_route(routing_setup):
    """Test recommendation engine and audit explanation."""
    G = routing_setup
    origin = "node_0_0"
    destination = "node_4_4"

    baselines = run_all_baselines(G, origin, destination)
    candidate_list = [{"path": path, "algorithm": name} for name, path in baselines.items() if path]

    result = recommend_optimal_route(G, candidate_list)
    assert result.route_id is not None
    assert result.algorithm is not None
    assert len(result.node_path) >= 2
    assert result.metrics.total_travel_time_sec > 0
    assert "=== Component 3 Route Recommendation Audit ===" in result.audit_notes


def test_recommend_fallback_when_all_exceed_ceiling(routing_setup):
    """Test that when all candidates exceed hazard ceiling, the ceiling is relaxed across all candidates."""
    G = routing_setup
    origin = "node_0_0"
    destination = "node_4_4"

    baselines = run_all_baselines(G, origin, destination)
    candidate_list = [{"path": path, "algorithm": name} for name, path in baselines.items() if path]

    # Force user_max_hazard to 0.0001 so all candidates breach ceiling
    result = recommend_optimal_route(G, candidate_list, user_max_hazard=0.0001)
    assert "RELAXED - all paths exceed limit" in result.audit_notes
    assert result.node_path is not None


def test_no_path_handling():
    """Test graceful handling when no path exists between disconnected components."""
    G = nx.DiGraph()
    G.add_node("A", x=80.0, y=6.0)
    G.add_node("B", x=81.0, y=7.0)
    # No edges

    path = compute_shortest_time_route(G, "A", "B")
    assert path is None
