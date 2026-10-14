"""Tests for hazard attachment to road network edges."""

import numpy as np
import pytest
import networkx as nx

from src.route_optimization.attachment import (
    sample_raster_at_point,
    compute_edge_raster_hazard,
    attach_hazard_to_graph,
)
from src.route_optimization.synthetic import (
    generate_synthetic_grid_graph,
    generate_synthetic_hazard_raster,
)


def test_sample_raster_at_point():
    """Test sampling a 2D hazard raster at specific point coordinates."""
    raster = np.array([
        [0.1, 0.2],
        [0.8, 0.9],
    ], dtype=np.float64)
    bounds = (80.0, 6.0, 81.0, 7.0) # (min_lon, min_lat, max_lon, max_lat)

    # Sample inside top-right (high lat, high lon)
    val_tr = sample_raster_at_point(80.9, 6.9, raster, bounds)
    assert 0.8 <= val_tr <= 1.0

    # Sample inside bottom-left
    val_bl = sample_raster_at_point(80.1, 6.1, raster, bounds)
    assert 0.05 <= val_bl <= 0.3

    # Sample outside bounds returns 0.0
    val_out = sample_raster_at_point(82.0, 5.0, raster, bounds)
    assert val_out == 0.0


def test_compute_edge_raster_hazard():
    """Test that edge raster sampling returns maximum and mean hazard along edge."""
    hazard_data = generate_synthetic_hazard_raster(
        min_lat=6.0, min_lon=80.0, max_lat=7.0, max_lon=81.0, grid_size=(30, 30), seed=42
    )

    u_coord = (80.1, 6.1)
    v_coord = (80.9, 6.9)

    max_h, mean_h = compute_edge_raster_hazard(u_coord, v_coord, hazard_data, num_samples=10)
    assert 0.0 <= mean_h <= max_h <= 1.0


def test_attach_hazard_to_graph():
    """Test attaching hazard values to synthetic road network edges."""
    G = generate_synthetic_grid_graph(rows=4, cols=4, seed=42)
    hazard_data = generate_synthetic_hazard_raster(
        min_lat=6.65, min_lon=80.35, max_lat=6.75, max_lon=80.48, seed=42
    )

    attach_hazard_to_graph(G, hazard_data, num_samples_per_edge=8)

    # Verify edge attributes exist on every edge
    for u, v, data in G.edges(data=True):
        assert "hazard_score" in data
        assert "max_hazard" in data
        assert "mean_hazard" in data
        assert "hazard_exposure" in data
        assert 0.0 <= data["hazard_score"] <= 1.0
        assert data["hazard_score"] == data["max_hazard"]
        assert data["hazard_exposure"] == pytest.approx(data["length"] * data["hazard_score"], rel=1e-2)
