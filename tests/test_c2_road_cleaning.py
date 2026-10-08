"""Focused tests for conservative Component 2 Step 4 cleaning."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import networkx as nx

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _module():
    path = PROJECT_ROOT / "scripts" / "c2_clean_validate_raw_network.py"
    spec = importlib.util.spec_from_file_location("c2_clean", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _fixture(tmp_path: Path):
    graph = nx.MultiDiGraph()
    graph.add_node(10, x=80.1, y=6.1)
    graph.add_node(-20, x=80.2, y=6.1)
    graph.add_edge(
        10, -20, key=3, highway="service", length="0.5", oneway=False,
        oneway_osm="no", junction="", bridge="", tunnel="", layer="",
        access="", motor_vehicle="", vehicle="", service="driveway",
        maxspeed="", lanes="", ref="", name="",
    )
    graph.graph["crs"] = "EPSG:4326"
    raw = tmp_path / "raw.graphml"
    nx.write_graphml(graph, raw)
    boundary = tmp_path / "boundary.geojson"
    boundary.write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "properties": {}, "geometry": {
            "type": "Polygon",
            "coordinates": [[[80, 6], [81, 6], [81, 7], [80, 7], [80, 6]]],
        }}],
    }), encoding="utf-8")
    metadata = tmp_path / "metadata.json"
    metadata.write_text(json.dumps({"status": "success"}), encoding="utf-8")
    return raw, boundary, metadata


def test_cleaning_preserves_raw_and_multidigraph(tmp_path):
    module = _module()
    raw, boundary, metadata = _fixture(tmp_path)
    before = raw.read_bytes()
    cleaned = tmp_path / "cleaned.graphml"
    report = module.validate_and_clean(raw, boundary, metadata, cleaned)
    result = nx.read_graphml(cleaned, force_multigraph=True, node_type=int)
    assert raw.read_bytes() == before
    assert isinstance(result, nx.MultiDiGraph)
    assert result.number_of_nodes() == 2
    assert result.number_of_edges() == 1
    assert report["records"]["nodes_removed"] == 0
    assert report["records"]["edges_removed"] == 0
    assert result.edges[10, -20, 3]["oneway_osm"] == "no"


def test_invalid_length_categories_are_classified_without_removal():
    module = _module()
    assert module._length_kind({}) == "missing"
    assert module._length_kind({"length": "bad"}) == "non_numeric"
    assert module._length_kind({"length": "0"}) == "zero"
    assert module._length_kind({"length": "-1"}) == "negative"
    assert module._length_kind({"length": "NaN"}) == "non_finite"
    assert module._length_kind({"length": "0.5"}) == "very_short"
