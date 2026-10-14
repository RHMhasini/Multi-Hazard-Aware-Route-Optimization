"""Unit tests for Component 4 GeoJSON export engine."""

import json
from pathlib import Path
import pytest
import networkx as nx

from src.route_optimization.exporter import export_routes_to_geojson
from src.route_optimization.synthetic import (
    generate_synthetic_grid_graph,
    generate_synthetic_hazard_raster,
)
from src.route_optimization.attachment import attach_hazard_to_graph
from src.route_optimization.graph_builder import build_routing_graph
from src.route_optimization.baselines import run_all_baselines
from src.route_optimization.recommend import recommend_optimal_route


@pytest.fixture
def routing_results():
    """Run baseline and recommendation pipeline on a synthetic network."""
    raw_g = generate_synthetic_grid_graph(rows=4, cols=4, seed=42)
    hazard_data = generate_synthetic_hazard_raster(seed=42)
    attach_hazard_to_graph(raw_g, hazard_data)
    G = build_routing_graph(raw_g)

    origin = "node_0_0"
    destination = "node_3_3"

    baselines = run_all_baselines(G, origin, destination)
    candidates = [{"path": p, "algorithm": k} for k, p in baselines.items() if p]
    rec_result = recommend_optimal_route(G, candidates)

    return G, baselines, rec_result


def test_export_routes_to_geojson(routing_results, tmp_path):
    """Test full GeoJSON export with lines, markers, and properties."""
    G, baselines, rec_result = routing_results

    out_file = tmp_path / "test_routes.geojson"
    orig_coord = (80.40, 6.68)
    dest_coord = (80.44, 6.72)

    routes_dict = dict(baselines)
    routes_dict["recommended"] = rec_result

    geojson = export_routes_to_geojson(
        graph=G,
        routes=routes_dict,
        recommended_route_id="recommended",
        origin_coord=orig_coord,
        destination_coord=dest_coord,
        output_filepath=out_file,
    )

    # 1. Structure validation
    assert geojson["type"] == "FeatureCollection"
    assert "features" in geojson
    assert len(geojson["features"]) >= 3 # Origin point + Dest point + at least 1 route line

    # 2. Check Point markers
    points = [f for f in geojson["features"] if f["geometry"]["type"] == "Point"]
    assert len(points) == 2
    point_types = [p["properties"]["marker_type"] for p in points]
    assert "origin" in point_types
    assert "destination" in point_types

    # 3. Check LineString routes
    lines = [f for f in geojson["features"] if f["geometry"]["type"] == "LineString"]
    assert len(lines) >= 1

    # Check recommended route line
    rec_lines = [f for f in lines if f["properties"].get("is_recommended") is True]
    assert len(rec_lines) >= 1
    rec_props = rec_lines[0]["properties"]
    assert "travel_time_sec" in rec_props
    assert "distance_km" in rec_props
    assert "cumulative_hazard_exposure" in rec_props
    assert "max_edge_hazard" in rec_props
    assert rec_props["travel_time_sec"] > 0
    assert rec_props["distance_km"] > 0

    # 4. Check file written to disk
    assert out_file.exists()
    with open(out_file, "r", encoding="utf-8") as f:
        loaded = json.load(f)
    assert loaded["type"] == "FeatureCollection"
    assert len(loaded["features"]) == len(geojson["features"])
