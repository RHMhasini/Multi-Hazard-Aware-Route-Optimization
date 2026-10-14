"""Unit tests for disaster scenario simulation and edge exclusion."""

import pytest
import networkx as nx

from src.route_optimization.scenarios import (
    apply_hazard_exclusion,
    apply_hazard_penalty_scenario,
    check_scenario_reachability,
    evaluate_network_resilience,
)
from src.route_optimization.synthetic import (
    generate_synthetic_grid_graph,
    generate_synthetic_hazard_raster,
)
from src.route_optimization.attachment import attach_hazard_to_graph
from src.route_optimization.graph_builder import build_routing_graph


@pytest.fixture
def hazardous_network():
    """Build a routing network with attached hazard values."""
    raw_g = generate_synthetic_grid_graph(rows=4, cols=4, spacing_m=1000.0, seed=42)
    hazard_data = generate_synthetic_hazard_raster(seed=42)
    attach_hazard_to_graph(raw_g, hazard_data)
    return build_routing_graph(raw_g)


def test_hazard_edge_exclusion(hazardous_network):
    """Test severing edges with hazard >= threshold."""
    initial_edge_count = len(hazardous_network.edges())
    # Exclude edges with hazard >= 0.70
    pruned_g = apply_hazard_exclusion(hazardous_network, hazard_threshold=0.70, inplace=False)

    assert len(pruned_g.edges()) < initial_edge_count
    # Verify no edge in pruned graph exceeds threshold
    for u, v, data in pruned_g.edges(data=True):
        assert data.get("hazard_score", 0.0) < 0.70

    report = pruned_g.graph["exclusion_report"]
    assert report["severed_edges_count"] > 0
    assert report["active_edges_count"] == len(pruned_g.edges())


def test_hazard_penalty_scenario(hazardous_network):
    """Test applying transit penalties to cautionary hazard edges."""
    penalized_g = apply_hazard_penalty_scenario(
        hazardous_network, warning_threshold=0.50, penalty_multiplier=5.0, inplace=False
    )

    penalized_found = False
    for u, v, data in penalized_g.edges(data=True):
        orig_data = hazardous_network[u][v]
        if orig_data["hazard_score"] >= 0.50:
            penalized_found = True
            assert data["travel_time"] == pytest.approx(orig_data["travel_time"] * 5.0, rel=1e-2)
            assert data["is_penalized"] is True

    assert penalized_found is True


def test_scenario_reachability(hazardous_network):
    """Test connectivity and reachability testing under disaster closures."""
    origin = "node_0_0"
    destination = "node_3_3"

    # Initially reachable
    assert check_scenario_reachability(hazardous_network, origin, destination) is True

    # Artificially sever all outgoing edges from origin
    disconnected_g = hazardous_network.copy()
    for succ in list(disconnected_g.successors(origin)):
        disconnected_g.remove_edge(origin, succ)

    assert check_scenario_reachability(disconnected_g, origin, destination) is False


def test_evaluate_network_resilience(hazardous_network):
    """Test resilience assessment across multiple hazard cutoff thresholds."""
    resilience = evaluate_network_resilience(hazardous_network, thresholds=[0.95, 0.80, 0.60])
    assert len(resilience) == 3

    # Lower threshold should sever more edges
    severed_at_95 = resilience[0]["severed_edges"]
    severed_at_60 = resilience[2]["severed_edges"]
    assert severed_at_60 >= severed_at_95
