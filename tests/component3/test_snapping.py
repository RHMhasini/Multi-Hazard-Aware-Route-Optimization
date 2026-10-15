"""Unit tests for coordinate snapping (KD-Tree & Euclidean)."""

import pytest
import networkx as nx

from src.route_optimization.snapping import (
    CoordinateSnapper,
    snap_coordinate_to_node,
    snap_od_coordinates,
)
from src.route_optimization.synthetic import generate_synthetic_grid_graph


@pytest.fixture
def sample_graph():
    """Create a sample 4x4 grid road network."""
    # base_lat=6.68, base_lon=80.40, spacing_m=1000m
    return generate_synthetic_grid_graph(rows=4, cols=4, spacing_m=1000.0, seed=42)


def test_snap_exact_coordinate(sample_graph):
    """Snapping an exact node coordinate should return that node with near-zero distance."""
    node_data = sample_graph.nodes["node_0_0"]
    lon = node_data["x"]
    lat = node_data["y"]

    snapped_node, dist_m = snap_coordinate_to_node(sample_graph, lon, lat)
    assert snapped_node == "node_0_0"
    assert dist_m < 1.0


def test_coordinate_snapper_kdtree(sample_graph):
    """Test fast spatial KD-Tree snapper class."""
    snapper = CoordinateSnapper(sample_graph)
    node_data = sample_graph.nodes["node_2_2"]
    query_lon = node_data["x"] + 0.0005  # slight offset ~50m
    query_lat = node_data["y"] + 0.0005

    node_id, dist_m = snapper.snap_coordinate(query_lon, query_lat)
    assert node_id == "node_2_2"
    assert dist_m < 200.0


def test_snap_od_pair(sample_graph):
    """Test snapping origin and destination coordinates simultaneously."""
    orig_coord = (80.401, 6.681)
    dest_coord = (80.425, 6.705)

    res = snap_od_coordinates(sample_graph, orig_coord, dest_coord, max_distance_m=5000.0)
    assert "origin_node" in res
    assert "destination_node" in res
    assert res["origin_dist_m"] < 1500.0
    assert res["destination_dist_m"] < 1500.0
    assert res["origin_node"] != res["destination_node"]


def test_max_distance_exceeded(sample_graph):
    """Test that querying far outside the graph bounds raises ValueError."""
    far_lon = 85.0
    far_lat = 10.0

    with pytest.raises(ValueError, match="exceeds the maximum snapping threshold"):
        snap_coordinate_to_node(sample_graph, far_lon, far_lat, max_distance_m=2000.0)

    snapper = CoordinateSnapper(sample_graph)
    with pytest.raises(ValueError, match="exceeds the maximum snapping threshold"):
        snapper.snap_coordinate(far_lon, far_lat, max_distance_m=2000.0)
