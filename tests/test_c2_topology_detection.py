"""Focused tests for read-only topology candidate detection."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import networkx as nx
import pytest
from pyproj import Transformer

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_ROOT / "src" / "road_network" / "c2_topology_detection.py"
SPEC = importlib.util.spec_from_file_location("c2_topology_detection", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _attrs(**overrides):
    values = {
        "layer": "0",
        "bridge": "no",
        "tunnel": "no",
        "highway": "residential",
        "access": "yes",
        "motor_vehicle": "yes",
        "vehicle": "yes",
        "service": "none",
    }
    values.update(overrides)
    return values


def _graph(
    left=(80.0, 7.0),
    right=(80.00065, 7.0),
    left_edge_attrs=None,
    right_edge_attrs=None,
):
    graph = nx.MultiDiGraph()
    points = {
        1: left,
        2: (left[0] + 0.0003, left[1]),
        3: (right[0] - 0.0003, right[1]),
        4: right,
    }
    for node, (x, y) in points.items():
        graph.add_node(node, x=str(x), y=str(y))
    left_attrs = _attrs(**(left_edge_attrs or {}))
    right_attrs = _attrs(**(right_edge_attrs or {}))
    for u, v, attrs in ((1, 2, left_attrs), (3, 4, right_attrs)):
        graph.add_edge(u, v, **attrs)
        graph.add_edge(v, u, **attrs)
    graph.graph["crs"] = "EPSG:4326"
    return graph


def _graph_with_gap_m(gap_m):
    to_metric = Transformer.from_crs("EPSG:4326", "EPSG:5235", always_xy=True)
    to_geo = Transformer.from_crs("EPSG:5235", "EPSG:4326", always_xy=True)
    origin_x, origin_y = to_metric.transform(80.0, 7.0)
    points_m = {
        1: (origin_x, origin_y),
        2: (origin_x + 10.0, origin_y),
        3: (origin_x + 10.0 + gap_m, origin_y),
        4: (origin_x + 20.0 + gap_m, origin_y),
    }
    graph = nx.MultiDiGraph(crs="EPSG:4326")
    for node, (x, y) in points_m.items():
        lon, lat = to_geo.transform(x, y)
        graph.add_node(node, x=str(lon), y=str(lat))
    for u, v in ((1, 2), (3, 4)):
        graph.add_edge(u, v, **_attrs())
        graph.add_edge(v, u, **_attrs())
    return graph


def _graph_with_angular_pairs():
    to_metric = Transformer.from_crs("EPSG:4326", "EPSG:5235", always_xy=True)
    to_geo = Transformer.from_crs("EPSG:5235", "EPSG:4326", always_xy=True)
    origin_x, origin_y = to_metric.transform(80.0, 7.0)
    graph = nx.MultiDiGraph(crs="EPSG:4326")
    pairs = [
        ((0.0, 0.0), (14.0, 14.0)),
        ((100.0, 0.0), (114.0, 14.0)),
        ((0.0, 100.0), (14.0, 86.0)),
    ]
    node_id = 1
    for first, second in pairs:
        dx = second[0] - first[0]
        dy = second[1] - first[1]
        length = (dx * dx + dy * dy) ** 0.5
        points = (
            (first, (first[0] - 10.0 * dx / length, first[1] - 10.0 * dy / length)),
            (second, (second[0] + 10.0 * dx / length, second[1] + 10.0 * dy / length)),
        )
        for endpoint, neighbor in points:
            endpoint_id = node_id
            neighbor_id = node_id + 1
            for current_id, (x, y) in ((endpoint_id, endpoint), (neighbor_id, neighbor)):
                lon, lat = to_geo.transform(origin_x + x, origin_y + y)
                graph.add_node(current_id, x=str(lon), y=str(lat))
            graph.add_edge(endpoint_id, neighbor_id, **_attrs())
            graph.add_edge(neighbor_id, endpoint_id, **_attrs())
            node_id += 2
    return graph


def test_nearby_endpoint_pair_is_eligible_and_does_not_mutate_input():
    graph = _graph()
    before = (set(graph.nodes), list(graph.edges(keys=True, data=True)))
    report, geojson = MODULE.detect_candidates(graph, search_distance_m=20)
    assert report["counts"]["endpoint_count"] == 4
    assert report["counts"]["candidate_pair_count"] == 1
    assert report["counts"]["eligible_count"] == 1
    assert geojson["features"][0]["geometry"]["type"] == "LineString"
    assert (set(graph.nodes), list(graph.edges(keys=True, data=True))) == before


def test_pair_beyond_distance_is_not_a_candidate():
    report, _ = MODULE.detect_candidates(_graph(right=(80.001, 7.0)), search_distance_m=20)
    assert report["counts"]["candidate_pair_count"] == 0


def test_already_connected_pair_is_rejected_as_redundant():
    graph = nx.MultiDiGraph()
    graph.add_node(1, x="80.0", y="7.0")
    graph.add_node(2, x="80.0001", y="7.0")
    graph.add_edge(1, 2, **_attrs())
    graph.add_edge(2, 1, **_attrs())
    graph.graph["crs"] = "EPSG:4326"
    report, _ = MODULE.detect_candidates(graph, search_distance_m=20)
    assert report["counts"]["candidate_pair_count"] == 0


def test_layer_bridge_and_tunnel_conflicts_are_rejected():
    graph = _graph()
    for u, v, data in graph.edges(data=True):
        if u in {1, 2}:
            data["layer"] = "1"
            data["bridge"] = "yes"
            data["tunnel"] = ""
        else:
            data["layer"] = "0"
    report, _ = MODULE.detect_candidates(graph, search_distance_m=20)
    assert report["candidates"][0]["status"] == "rejected"
    assert any("layer_conflict" in reason for reason in report["candidates"][0]["reasons"])


def test_missing_attributes_require_review_and_restricted_access_is_rejected():
    report, _ = MODULE.detect_candidates(_graph(left_edge_attrs={"access": ""}), search_distance_m=20)
    assert report["candidates"][0]["status"] == "needs_review"
    assert "missing_semantic_attributes" in report["candidates"][0]["reasons"]
    report, _ = MODULE.detect_candidates(
        _graph(left_edge_attrs={"access": "no"}, right_edge_attrs={"access": "yes"}),
        search_distance_m=20,
    )
    assert report["candidates"][0]["status"] == "rejected"
    assert "access_restriction_conflict" in report["candidates"][0]["reasons"]


def test_single_genuine_dead_end_is_not_called_defective():
    graph = nx.MultiDiGraph()
    graph.add_node(1, x="80.0", y="7.0")
    graph.add_node(2, x="80.001", y="7.0")
    graph.add_edge(1, 2, **_attrs())
    graph.graph["crs"] = "EPSG:4326"
    report, _ = MODULE.detect_candidates(graph, search_distance_m=200)
    assert report["counts"]["endpoint_count"] == 2
    assert report["counts"]["candidate_pair_count"] == 0


def test_candidate_ids_and_runs_are_deterministic():
    graph = _graph()
    first, first_geojson = MODULE.detect_candidates(graph, search_distance_m=20)
    second, second_geojson = MODULE.detect_candidates(graph, search_distance_m=20)
    assert first == second
    assert first_geojson == second_geojson
    assert first["candidates"][0]["candidate_id"].startswith("candidate-")


def test_empty_graph_has_empty_outputs():
    report, geojson = MODULE.detect_candidates(nx.MultiDiGraph(crs="EPSG:4326"), search_distance_m=20)
    assert report["counts"] == {
        "endpoint_count": 0,
        "nearby_endpoint_pair_count": 0,
        "candidate_pair_count": 0,
        "redundant_pair_count": 0,
        "eligible_count": 0,
        "rejected_count": 0,
        "needs_review_count": 0,
    }
    assert geojson["features"] == []
    assert geojson["crs"]["properties"]["name"] == "EPSG:4326"


def test_directed_multigraph_endpoint_definition_collapses_directions():
    graph = nx.MultiDiGraph()
    graph.add_node(1, x="80.0", y="7.0")
    graph.add_node(2, x="80.0001", y="7.0")
    graph.add_edge(1, 2, **_attrs())
    graph.add_edge(2, 1, **_attrs())
    graph.graph["crs"] = "EPSG:4326"
    report, _ = MODULE.detect_candidates(graph, search_distance_m=20)
    assert report["counts"]["endpoint_count"] == 2
    assert report["configuration"]["degree_definition"].startswith("An endpoint is a node with exactly one unique undirected neighbor")


@pytest.mark.parametrize("gap_m", [20.0, 19.999, 19.0, 20.001])
def test_distance_boundary_and_near_boundary_pairs(gap_m):
    report, _ = MODULE.detect_candidates(_graph_with_gap_m(gap_m), search_distance_m=20)
    assert (report["counts"]["candidate_pair_count"] == 1) is (gap_m <= 20.0)


def test_candidate_ids_and_unordered_pairs_are_unique():
    report, _ = MODULE.detect_candidates(_graph_with_gap_m(10.0), search_distance_m=20)
    candidate_ids = [item["candidate_id"] for item in report["candidates"]]
    endpoint_pairs = [
        tuple(sorted((item["endpoint_u"], item["endpoint_v"])))
        for item in report["candidates"]
    ]
    assert len(candidate_ids) == len(set(candidate_ids))
    assert len(endpoint_pairs) == len(set(endpoint_pairs))


def test_spatial_query_considers_pairs_at_multiple_angular_positions():
    report, _ = MODULE.detect_candidates(_graph_with_angular_pairs(), search_distance_m=20)
    endpoint_pairs = {
        tuple(sorted((item["endpoint_u"], item["endpoint_v"])))
        for item in report["candidates"]
    }
    assert {(1, 3), (5, 7), (9, 11)} <= endpoint_pairs


def test_self_loop_and_isolated_node_do_not_create_endpoint_candidates():
    graph = nx.MultiDiGraph(crs="EPSG:4326")
    graph.add_node(1, x="80.0", y="7.0")
    graph.add_node(2, x="80.0001", y="7.0")
    graph.add_node(3, x="80.0002", y="7.0")
    graph.add_edge(1, 1, **_attrs())
    report, _ = MODULE.detect_candidates(graph, search_distance_m=20)
    assert report["counts"]["endpoint_count"] == 0
    assert report["counts"]["candidate_pair_count"] == 0


@pytest.mark.parametrize(
    "node_update",
    [
        lambda graph: graph.nodes[1].pop("x"),
        lambda graph: graph.nodes[1].update(x="not-a-number"),
        lambda graph: graph.nodes[1].update(x="nan"),
        lambda graph: graph.nodes[1].update(x="181.0"),
    ],
)
def test_invalid_coordinates_fail_with_actionable_error(node_update):
    graph = _graph()
    node_update(graph)
    with pytest.raises(ValueError, match="Node 1"):
        MODULE.detect_candidates(graph, search_distance_m=20)


@pytest.mark.parametrize("crs", [None, "EPSG:3857", "not-a-crs"])
def test_missing_or_incompatible_crs_fails_clearly(crs):
    graph = _graph()
    if crs is None:
        del graph.graph["crs"]
    else:
        graph.graph["crs"] = crs
    with pytest.raises(ValueError, match="CRS"):
        MODULE.detect_candidates(graph, search_distance_m=20)


def test_crs_validation_and_geojson_coordinates_are_longitude_latitude():
    report, geojson = MODULE.detect_candidates(_graph_with_gap_m(10.0), search_distance_m=20)
    assert report["input_graph"]["crs_validation"]["status"] == "validated"
    assert report["configuration"]["metric_crs"] == "EPSG:5235"
    assert report["configuration"]["geojson_crs"] == "EPSG:4326"
    line = next(
        feature for feature in geojson["features"]
        if feature["properties"]["feature_type"] == "candidate_connection"
    )
    for longitude, latitude in line["geometry"]["coordinates"]:
        assert 79.0 < longitude < 81.0
        assert 6.0 < latitude < 8.0


def test_status_counts_reconcile_and_all_graph_attributes_are_unchanged():
    graph = _graph()
    graph.graph["custom"] = {"source": "synthetic"}
    before_graph = dict(graph.graph)
    before_nodes = {node: dict(data) for node, data in graph.nodes(data=True)}
    before_edges = [
        (u, v, key, dict(data))
        for u, v, key, data in graph.edges(keys=True, data=True)
    ]
    report, _ = MODULE.detect_candidates(graph, search_distance_m=20)
    counts = report["counts"]
    assert (
        counts["eligible_count"]
        + counts["rejected_count"]
        + counts["needs_review_count"]
        == counts["candidate_pair_count"]
    )
    assert dict(graph.graph) == before_graph
    assert {node: dict(data) for node, data in graph.nodes(data=True)} == before_nodes
    assert [
        (u, v, key, dict(data))
        for u, v, key, data in graph.edges(keys=True, data=True)
    ] == before_edges


def test_detect_file_writes_required_outputs(tmp_path):
    graph_path = tmp_path / "input.graphml"
    nx.write_graphml(_graph(), graph_path)
    report_path = tmp_path / "report.json"
    markdown_path = tmp_path / "report.md"
    geojson_path = tmp_path / "candidates.geojson"
    report = MODULE.detect_file(graph_path, report_path, markdown_path, geojson_path, 20)
    assert report_path.is_file() and markdown_path.is_file() and geojson_path.is_file()
    assert json.loads(report_path.read_text(encoding="utf-8"))["counts"]["candidate_pair_count"] == 1
    output = json.loads(geojson_path.read_text(encoding="utf-8"))
    assert output["type"] == "FeatureCollection"
    assert {feature["geometry"]["type"] for feature in output["features"]} == {"Point", "LineString"}
