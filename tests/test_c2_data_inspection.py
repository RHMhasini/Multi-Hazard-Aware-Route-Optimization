"""Focused tests for the read-only Component 2 Step 3 baseline inspection."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import networkx as nx
from shapely.geometry import mapping, Polygon

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_module():
    path = PROJECT_ROOT / "scripts" / "c2_inspect_raw_network.py"
    spec = importlib.util.spec_from_file_location("c2_inspect", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _fixture(tmp_path: Path):
    graph = nx.MultiDiGraph()
    graph.add_node(1, x=0.0, y=0.0)
    graph.add_node(2, x=0.001, y=0.0)
    graph.add_node(3, x=0.002, y=0.0)
    graph.add_edge(1, 2, highway="primary", length="10", oneway=True, oneway_osm="yes")
    graph.add_edge(1, 2, highway="primary", length="12", oneway=True, oneway_osm="yes")
    graph.add_edge(2, 3, highway="service", length="0.5", oneway=False, oneway_osm="no")
    graph.graph["crs"] = "EPSG:4326"
    graph_path = tmp_path / "graph.graphml"
    nx.write_graphml(graph, graph_path)
    boundary_path = tmp_path / "boundary.geojson"
    boundary_path.write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "properties": {}, "geometry": mapping(Polygon([(-1, -1), (1, -1), (1, 1), (-1, 1), (-1, -1)]))}],
    }), encoding="utf-8")
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text(json.dumps({"status": "success"}), encoding="utf-8")
    return graph_path, boundary_path, metadata_path


def test_inspection_is_read_only_and_reports_structure(tmp_path):
    module = _load_module()
    graph_path, boundary_path, metadata_path = _fixture(tmp_path)
    before = graph_path.read_bytes()
    result = module.inspect_raw_network(graph_path, boundary_path, metadata_path, validate_osmnx=False)
    assert graph_path.read_bytes() == before
    assert result["graph_structure"]["graph_class"] == "MultiDiGraph"
    assert result["graph_structure"]["node_count"] == 3
    assert result["edge_statistics"]["self_loop_count"] == 0
    assert result["edge_statistics"]["parallel_edges"]["parallel_node_pair_count"] == 1
    assert result["candidate_topology_indicators"]["very_short_edges_below_1m"] == 1


def test_baseline_writer_creates_json_and_markdown(tmp_path):
    module = _load_module()
    graph_path, boundary_path, metadata_path = _fixture(tmp_path)
    result = module.inspect_raw_network(graph_path, boundary_path, metadata_path, validate_osmnx=False)
    json_path = tmp_path / "baseline.json"
    markdown_path = tmp_path / "baseline.md"
    module.write_baseline(result, json_path, markdown_path)
    assert json.loads(json_path.read_text(encoding="utf-8"))["operations"]["topology_modified"] is False
    assert "No topology modification was performed" in markdown_path.read_text(encoding="utf-8")
