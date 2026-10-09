"""Focused tests for Component 2 Step 5 direction normalization."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import networkx as nx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_ROOT / "src" / "road_network" / "c2_direction_normalization.py"
MODULE_SPEC = importlib.util.spec_from_file_location("c2_direction_normalization", MODULE_PATH)
assert MODULE_SPEC and MODULE_SPEC.loader
MODULE = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(MODULE)
classify_direction = MODULE.classify_direction
normalize_graph = MODULE.normalize_graph
validate_preservation = MODULE.validate_preservation

def test_osm_direction_values_are_classified_without_topology_changes():
    cases = {
        "yes": "forward",
        "true": "forward",
        "1": "forward",
        "-1": "reverse",
        "no": "bidirectional",
    }
    for value, expected in cases.items():
        direction, _ = classify_direction({"oneway_osm": value})
        assert direction == expected


def test_missing_oneway_and_roundabout_defaults():
    assert classify_direction({})[0] == "bidirectional"
    assert classify_direction({"junction": "roundabout"})[0] == "forward"


def test_normalization_preserves_nodes_edges_and_semantics():
    graph = nx.MultiDiGraph()
    graph.add_node(1, x=80.1, y=6.1)
    graph.add_node(2, x=80.2, y=6.1)
    graph.add_edge(
        1, 2, key=4, oneway=False, oneway_osm="no", highway="primary",
        junction="", length="10", name="Road", geometry="LINESTRING (80.1 6.1, 80.2 6.1)",
        osmid=101,
    )
    graph.add_edge(
        2, 1, key=5, oneway=False, oneway_osm="no", highway="primary",
        junction="", length="10", name="Road", geometry="LINESTRING (80.2 6.1, 80.1 6.1)",
        osmid=101,
    )
    normalized, stats = normalize_graph(graph)
    assert isinstance(normalized, nx.MultiDiGraph)
    assert set(normalized.nodes) == {1, 2}
    assert set(normalized.edges(keys=True)) == {(1, 2, 4), (2, 1, 5)}
    assert normalized.edges[1, 2, 4]["oneway_osm"] == "no"
    assert normalized.edges[1, 2, 4]["oneway_normalized"] == "bidirectional"
    assert normalized.edges[1, 2, 4]["direction_source"] == "existing_extraction_representation"
    assert normalized.edges[1, 2, 4]["geometry"] == "LINESTRING (80.1 6.1, 80.2 6.1)"
    assert stats["directed_edge_records_bidirectional"] == 2
    assert stats["osm_way_statistics"]["unique_osm_way_count"] == 1
    assert stats["osm_way_statistics"]["bidirectional_osm_way_count"] == 1
    assert validate_preservation(graph, normalized)["topology_changed"] is False


def test_bidirectional_representation_has_both_directions():
    graph = nx.MultiDiGraph()
    graph.add_edge(1, 2, key=0, oneway_osm="no", osmid=1)
    graph.add_edge(2, 1, key=0, oneway_osm="no", osmid=1)
    normalized, _ = normalize_graph(graph)
    assert normalized.has_edge(1, 2)
    assert normalized.has_edge(2, 1)


def test_forward_oneway_does_not_introduce_reverse_edge():
    graph = nx.MultiDiGraph()
    graph.add_edge(1, 2, key=0, oneway_osm="yes", osmid=2)
    normalized, stats = normalize_graph(graph)
    assert normalized.has_edge(1, 2)
    assert not normalized.has_edge(2, 1)
    assert stats["directed_edge_records_one_way"] == 1


def test_reverse_oneway_classification_documents_missing_geometry_evidence():
    direction, source = classify_direction({"oneway_osm": "-1"})
    assert direction == "reverse"
    assert source == "existing_extraction_representation"
    graph = nx.MultiDiGraph()
    graph.add_edge(2, 1, key=0, oneway_osm="-1", osmid=3)
    _, stats = normalize_graph(graph)
    assert stats["reverse_direction_cases"] == 1
    assert stats["direction_semantics_independently_verified"] is False


def test_connected_roundabout_segments_preserve_existing_direction():
    graph = nx.MultiDiGraph()
    graph.add_edge(1, 2, key=0, oneway_osm="", junction="roundabout", osmid=4)
    graph.add_edge(2, 3, key=0, oneway_osm="", junction="roundabout", osmid=4)
    graph.add_edge(3, 1, key=0, oneway_osm="", junction="roundabout", osmid=4)
    normalized, stats = normalize_graph(graph)
    assert set(normalized.edges()) == {(1, 2), (2, 3), (3, 1)}
    assert all(data["oneway_normalized"] == "forward" for _, _, data in normalized.edges(data=True))
    assert stats["roundabout_records"] == 3


def test_unknown_oneway_uses_conservative_bidirectional_fallback():
    direction, source = classify_direction({"oneway_osm": "sometimes"})
    assert direction == "bidirectional"
    assert source == "unknown_oneway_conservative_fallback"


def test_real_script_has_unique_component_paths():
    script = PROJECT_ROOT / "scripts" / "c2_normalize_directions.py"
    assert script.is_file()
    spec = importlib.util.spec_from_file_location("c2_step5_script", script)
    assert spec and spec.loader
