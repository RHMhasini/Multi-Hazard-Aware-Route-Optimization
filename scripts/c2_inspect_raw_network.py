#!/usr/bin/env python
"""Component 2 Step 3: reproducible, read-only raw-network inspection."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import geopandas as gpd
import networkx as nx
from shapely.geometry import LineString, Point

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GRAPH_PATH = PROJECT_ROOT / "data" / "raw" / "c2_sabaragamuwa_raw_drive_network.graphml"
BOUNDARY_PATH = PROJECT_ROOT / "data" / "raw" / "c2_sabaragamuwa_boundary.geojson"
METADATA_PATH = PROJECT_ROOT / "data" / "raw" / "c2_sabaragamuwa_osm_metadata.json"
JSON_PATH = PROJECT_ROOT / "outputs" / "component2_evaluation" / "c2_raw_network_baseline.json"
MARKDOWN_PATH = PROJECT_ROOT / "outputs" / "component2_evaluation" / "c2_raw_network_baseline.md"
STUDY_AREA = "Sabaragamuwa Province, Sri Lanka"
HIGHWAY_CLASSES = (
    "motorway", "motorway_link", "trunk", "trunk_link", "primary",
    "primary_link", "secondary", "secondary_link", "tertiary",
    "tertiary_link", "unclassified", "residential", "living_street",
    "service", "road",
)
SEMANTIC_ATTRIBUTES = (
    "oneway", "oneway_osm", "junction", "bridge", "tunnel", "layer",
    "access", "motor_vehicle", "vehicle", "service",
)


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _counts(graph: nx.MultiDiGraph, attribute: str) -> dict[str, int]:
    return dict(sorted(Counter(str(data.get(attribute, "")) for _, _, data in graph.edges(data=True)).items()))


def _percentages(counts: dict[str, int], total: int) -> dict[str, dict[str, float | int]]:
    return {
        key: {"count": value, "percentage": round(value * 100 / total, 6) if total else 0.0}
        for key, value in counts.items()
    }


def _numeric_values(graph: nx.MultiDiGraph, attribute: str) -> list[float]:
    values = []
    for _, _, data in graph.edges(data=True):
        try:
            value = float(data.get(attribute))
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            values.append(value)
    return values


def _stats(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "minimum": None, "maximum": None, "mean": None, "median": None}
    return {
        "count": len(values),
        "minimum": min(values),
        "maximum": max(values),
        "mean": statistics.fmean(values),
        "median": statistics.median(values),
    }


def _node_xy(graph: nx.MultiDiGraph, node: Any) -> tuple[float, float]:
    data = graph.nodes[node]
    return float(data["x"]), float(data["y"])


def _parallel_statistics(graph: nx.MultiDiGraph) -> dict[str, Any]:
    multiplicities = Counter(graph.number_of_edges(u, v) for u, v in graph.edges())
    pair_counts = {(u, v): graph.number_of_edges(u, v) for u, v in graph.edges()}
    groups = Counter(pair_counts.values())
    parallel_pairs = sum(1 for count in pair_counts.values() if count > 1)
    parallel_edges = sum(count for count in pair_counts.values() if count > 1)
    return {
        "node_pair_count": len(pair_counts),
        "parallel_node_pair_count": parallel_pairs,
        "edges_in_parallel_groups": parallel_edges,
        "maximum_edges_per_node_pair": max(pair_counts.values(), default=0),
        "multiplicity_distribution": {str(k): v for k, v in sorted(groups.items())},
        "directed_pair_multiplicity_distribution": {
            str(k): v for k, v in sorted(multiplicities.items())
        },
    }


def _spatial_validation(
    graph: nx.MultiDiGraph, boundary_path: Path
) -> dict[str, Any]:
    boundary = gpd.read_file(boundary_path)
    geometry = boundary.geometry.union_all()
    points = [Point(_node_xy(graph, node)) for node in graph.nodes]
    outside_nodes = sum(not geometry.covers(point) for point in points)
    edge_outside = 0
    edge_intersections = 0
    for u, v in graph.edges():
        line = LineString([_node_xy(graph, u), _node_xy(graph, v)])
        if line.intersects(geometry):
            edge_intersections += 1
        if not geometry.covers(line):
            edge_outside += 1
    xs = [point.x for point in points]
    ys = [point.y for point in points]
    negative_ids = [int(node) for node in graph.nodes if int(node) < 0]
    return {
        "boundary_crs": str(boundary.crs),
        "boundary_feature_count": len(boundary),
        "boundary_geometry_valid": bool(geometry.is_valid),
        "boundary_bounds": list(geometry.bounds),
        "graph_node_bounds": [min(xs), min(ys), max(xs), max(ys)],
        "nodes_outside_boundary": outside_nodes,
        "edges_intersecting_boundary": edge_intersections,
        "edges_not_covered_by_boundary": edge_outside,
        "negative_node_count": len(negative_ids),
        "negative_node_ids_all_boundary_clipping_candidates": all(
            node < 0 for node in negative_ids
        ),
    }


def _endpoint_candidates(graph: nx.MultiDiGraph, radius_m: float = 20.0) -> dict[str, Any]:
    """Count nearby degree-one candidates without changing graph topology."""
    endpoints = [node for node in graph if graph.degree(node) == 1]
    try:
        from scipy.spatial import cKDTree
    except ImportError:
        return {"available": False, "reason": "scipy is not installed"}
    if len(endpoints) < 2:
        return {"available": True, "radius_m": radius_m, "endpoint_count": len(endpoints), "candidate_pair_count": 0}
    coords = []
    for node in endpoints:
        lon, lat = _node_xy(graph, node)
        coords.append((lon * 111320.0 * math.cos(math.radians(lat)), lat * 110540.0))
    pairs = cKDTree(coords).query_pairs(radius_m)
    return {
        "available": True,
        "radius_m": radius_m,
        "endpoint_count": len(endpoints),
        "candidate_pair_count": len(pairs),
    }


def inspect_raw_network(
    graph_path: Path = GRAPH_PATH,
    boundary_path: Path = BOUNDARY_PATH,
    metadata_path: Path = METADATA_PATH,
    validate_osmnx: bool = True,
) -> dict[str, Any]:
    graph = nx.read_graphml(graph_path, force_multigraph=True, node_type=int)
    edge_count = graph.number_of_edges()
    degrees = [degree for _, degree in graph.degree()]
    degree_counts = Counter(degrees)
    observed_highway_counts = _counts(graph, "highway")
    highway_counts = {highway: observed_highway_counts.get(highway, 0) for highway in HIGHWAY_CLASSES} | {
        highway: count
        for highway, count in observed_highway_counts.items()
        if highway not in HIGHWAY_CLASSES
    }
    osmnx_validation: dict[str, Any] = {"performed": False}
    if validate_osmnx:
        import osmnx as ox

        osmnx_graph = ox.load_graphml(graph_path)
        osmnx_validation = {
            "performed": True,
            "graph_type": type(osmnx_graph).__name__,
            "node_count": osmnx_graph.number_of_nodes(),
            "edge_count": osmnx_graph.number_of_edges(),
            "is_directed": osmnx_graph.is_directed(),
        }
        del osmnx_graph

    metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {}
    result = {
        "inspection": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "study_area": STUDY_AREA,
            "graph_path": _display_path(graph_path),
            "graph_size_bytes": graph_path.stat().st_size,
            "boundary_path": _display_path(boundary_path),
            "metadata_path": _display_path(metadata_path),
            "raw_metadata_status": metadata.get("status"),
        },
        "graph_structure": {
            "graph_class": type(graph).__name__,
            "is_directed": graph.is_directed(),
            "is_multigraph": graph.is_multigraph(),
            "node_count": graph.number_of_nodes(),
            "edge_count": edge_count,
            "weakly_connected_components": nx.number_weakly_connected_components(graph),
            "strongly_connected_components": nx.number_strongly_connected_components(graph),
            "osmnx_validation": osmnx_validation,
        },
        "node_statistics": {
            "total": len(degrees),
            "minimum_degree": min(degrees, default=0),
            "maximum_degree": max(degrees, default=0),
            "mean_degree": statistics.fmean(degrees) if degrees else 0.0,
            "median_degree": statistics.median(degrees) if degrees else 0.0,
            "degree_0": degree_counts[0],
            "degree_1": degree_counts[1],
            "degree_2": degree_counts[2],
            "degree_3": degree_counts[3],
            "degree_4": degree_counts[4],
            "degree_greater_than_4": sum(count for degree, count in degree_counts.items() if degree > 4),
            "degree_distribution": {str(k): v for k, v in sorted(degree_counts.items())},
        },
        "edge_statistics": {
            "total_directed_edges": edge_count,
            "self_loop_count": nx.number_of_selfloops(graph),
            "parallel_edges": _parallel_statistics(graph),
            "length": _stats(_numeric_values(graph, "length")),
        },
        "road_class_distribution": {
            "classes_requested": list(HIGHWAY_CLASSES),
            "all_classes": _percentages(highway_counts, edge_count),
            "additional_classes": sorted(set(highway_counts) - set(HIGHWAY_CLASSES)),
        },
        "semantic_attributes": {
            attribute: _counts(graph, attribute) for attribute in SEMANTIC_ATTRIBUTES
        },
        "spatial_validation": _spatial_validation(graph, boundary_path),
        "candidate_topology_indicators": {
            "degree_1_nodes": degree_counts[1],
            "isolated_nodes": degree_counts[0],
            "self_loops": nx.number_of_selfloops(graph),
            "very_short_edges_below_1m": sum(value < 1.0 for value in _numeric_values(graph, "length")),
            "high_degree_threshold": 20,
            "nodes_degree_at_least_20": sum(degree >= 20 for degree in degrees),
            "component_size_summary": {
                "weak_min": min((len(c) for c in nx.weakly_connected_components(graph)), default=0),
                "weak_max": max((len(c) for c in nx.weakly_connected_components(graph)), default=0),
                "weak_components_at_most_10_nodes": sum(
                    len(c) <= 10 for c in nx.weakly_connected_components(graph)
                ),
            },
            "nearby_endpoint_candidates": _endpoint_candidates(graph),
            "interpretation": "These are candidate indicators only; no defect or repair decision is made.",
        },
        "limitations": [
            "Degree-one nodes may be legitimate endpoints, cul-de-sacs, access roads, or digitization candidates.",
            "No Dmerge or angular thresholds were selected.",
            "No topology modification, snapping, bridging, merging, deletion, or simplification was performed.",
        ],
        "operations": {
            "topology_modified": False,
            "risk_calculation_performed": False,
            "route_optimization_performed": False,
            "component_3_processing_performed": False,
        },
    }
    return result


def _markdown(result: dict[str, Any]) -> str:
    s = result["graph_structure"]
    n = result["node_statistics"]
    e = result["edge_statistics"]
    spatial = result["spatial_validation"]
    candidate = result["candidate_topology_indicators"]
    lines = [
        "# Component 2 Step 3 Raw Network Baseline",
        "",
        "## 1. Dataset information",
        f"- Study area: {result['inspection']['study_area']}",
        f"- Raw graph: `{result['inspection']['graph_path']}` ({result['inspection']['graph_size_bytes']} bytes)",
        f"- Raw extraction metadata status: `{result['inspection']['raw_metadata_status']}`",
        "",
        "## 2. Graph structure",
        f"- Class: `{s['graph_class']}`; directed: `{s['is_directed']}`; multigraph: `{s['is_multigraph']}`",
        f"- Nodes: {s['node_count']}; directed edges: {s['edge_count']}",
        f"- Weak components: {s['weakly_connected_components']}; strong components: {s['strongly_connected_components']}",
        f"- OSMnx validation: `{s['osmnx_validation']}`",
        "",
        "## 3. Node statistics",
        f"- Degree min/max/mean/median: {n['minimum_degree']} / {n['maximum_degree']} / {n['mean_degree']:.6f} / {n['median_degree']}",
        f"- Degree 0/1/2/3/4/>4: {n['degree_0']} / {n['degree_1']} / {n['degree_2']} / {n['degree_3']} / {n['degree_4']} / {n['degree_greater_than_4']}",
        "",
        "## 4. Edge statistics",
        f"- Self-loops: {e['self_loop_count']}; parallel statistics: `{e['parallel_edges']}`",
        f"- Length statistics: `{e['length']}`",
        "",
        "## 5. Road-class distribution",
    ]
    for highway, values in result["road_class_distribution"]["all_classes"].items():
        lines.append(f"- {highway}: {values['count']} ({values['percentage']}%)")
    lines += [
        f"- Additional highway values: `{result['road_class_distribution']['additional_classes']}`",
        "",
        "## 6. Direction and semantic attributes",
    ]
    for attribute, values in result["semantic_attributes"].items():
        lines.append(f"- {attribute}: `{values}`")
    lines += [
        "",
        "## 7. Spatial validation",
        f"- Boundary CRS: `{spatial['boundary_crs']}`; valid: `{spatial['boundary_geometry_valid']}`; features: {spatial['boundary_feature_count']}",
        f"- Node bounds: `{spatial['graph_node_bounds']}`; nodes outside boundary: {spatial['nodes_outside_boundary']}",
        f"- Edges intersecting boundary: {spatial['edges_intersecting_boundary']}; edges not covered: {spatial['edges_not_covered_by_boundary']}",
        f"- Negative node IDs: {spatial['negative_node_count']}; all are clipping candidates: {spatial['negative_node_ids_all_boundary_clipping_candidates']}",
        "",
        "## 8. Connectivity statistics",
        f"- Weak components: {s['weakly_connected_components']}; strong components: {s['strongly_connected_components']}",
        f"- Component-size summary: `{candidate['component_size_summary']}`",
        "",
        "## 9. Candidate topology indicators",
        f"- Degree-one candidates: {candidate['degree_1_nodes']}; isolated candidates: {candidate['isolated_nodes']}",
        f"- Very short edges (<1 m): {candidate['very_short_edges_below_1m']}; high-degree (>=20) nodes: {candidate['nodes_degree_at_least_20']}",
        f"- Nearby endpoint candidates: `{candidate['nearby_endpoint_candidates']}`",
        "- These are baseline observations, not confirmed topology defects.",
        "",
        "## 10. Known limitations",
    ]
    lines.extend(f"- {item}" for item in result["limitations"])
    lines += [
        "",
        "## 11. Processing statement",
        "No topology modification was performed. The raw GraphML, PBF, and boundary geometry were read only.",
        "",
    ]
    return "\n".join(lines)


def write_baseline(result: dict[str, Any], json_path: Path = JSON_PATH, markdown_path: Path = MARKDOWN_PATH) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(_markdown(result), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph", type=Path, default=GRAPH_PATH)
    parser.add_argument("--boundary", type=Path, default=BOUNDARY_PATH)
    parser.add_argument("--metadata", type=Path, default=METADATA_PATH)
    parser.add_argument("--skip-osmnx", action="store_true")
    args = parser.parse_args()
    result = inspect_raw_network(
        args.graph, args.boundary, args.metadata, validate_osmnx=not args.skip_osmnx
    )
    write_baseline(result)
    print(json.dumps(result["graph_structure"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
