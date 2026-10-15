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

    # Sample outside bounds returns nan (or nodata if specified), not treating unknown as safe
    val_out = sample_raster_at_point(82.0, 5.0, raster, bounds)
    assert np.isnan(val_out)

    val_nodata = sample_raster_at_point(82.0, 5.0, raster, bounds, nodata=0.0)
    assert val_nodata == 0.0


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


def test_tiny_geotiff_north_up_orientation(tmp_path):
    """Test creating and reading a tiny GeoTIFF with known north-up orientation."""
    try:
        import rasterio
        from rasterio.transform import from_bounds
    except ImportError:
        pytest.skip("rasterio not installed")

    from src.route_optimization.loaders import load_c1_hazard_raster

    # Create a 2x2 raster:
    # Row 0 (North): [0.99, 0.88]
    # Row 1 (South): [0.11, 0.22]
    data = np.array([[0.99, 0.88], [0.11, 0.22]], dtype=np.float32)
    bounds = (80.0, 6.0, 81.0, 7.0)
    transform = from_bounds(80.0, 6.0, 81.0, 7.0, 2, 2)

    tif_path = tmp_path / "test_hazard.tif"
    with rasterio.open(
        tif_path,
        "w",
        driver="GTiff",
        height=2,
        width=2,
        count=1,
        dtype=data.dtype,
        crs="EPSG:4326",
        transform=transform,
        nodata=-9999.0,
    ) as dst:
        dst.write(data, 1)

    loaded = load_c1_hazard_raster(tif_path)
    assert loaded["origin_upper"] is True
    assert loaded["raster"].shape == (2, 2)

    # Sample north point (lat=6.8) -> should sample row 0 (approx 0.99 or 0.88)
    val_north = sample_raster_at_point(
        80.2, 6.8, loaded["raster"], loaded["bounds"], origin_upper=True, transform=loaded.get("transform")
    )
    assert val_north > 0.8

    # Sample south point (lat=6.2) -> should sample row 1 (approx 0.11 or 0.22)
    val_south = sample_raster_at_point(
        80.2, 6.2, loaded["raster"], loaded["bounds"], origin_upper=True, transform=loaded.get("transform")
    )
    assert val_south < 0.3


def test_wkt_edge_geometry_attachment():
    """Test that GraphML/OSMnx WKT string geometries are parsed and sampled correctly."""
    G = nx.DiGraph()
    G.add_node("U", x=80.1, y=6.1)
    G.add_node("V", x=80.9, y=6.1)

    # Curved detour geometry in WKT string format bowing north towards (80.5, 6.8)
    wkt_geom = "LINESTRING (80.1 6.1, 80.5 6.8, 80.9 6.1)"
    G.add_edge("U", "V", geometry=wkt_geom, length=1500.0)

    # Hazard raster has hot cell at north (lat=6.8) and cold at south (lat=6.1)
    hazard_raster = np.array([[0.95, 0.95], [0.05, 0.05]], dtype=np.float64)
    hazard_data = {
        "raster": hazard_raster,
        "bounds": (80.0, 6.0, 81.0, 7.0),
        "origin_upper": True,
    }

    attach_hazard_to_graph(G, hazard_data)
    edge_data = G["U"]["V"]

    # Since the curve went through the north (lat 6.8), max_hazard should pick up ~0.95
    assert edge_data["max_hazard"] > 0.8
    assert edge_data["hazard_score"] == edge_data["max_hazard"]
    assert edge_data["hazard_exposure"] == pytest.approx(edge_data["length"] * edge_data["hazard_score"], rel=1e-2)
