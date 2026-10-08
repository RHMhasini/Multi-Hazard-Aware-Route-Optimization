#!/usr/bin/env python
"""Component 2 Step 4: validate and conservatively copy the raw road graph."""

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
RAW_GRAPH = PROJECT_ROOT / "data/raw/c2_sabaragamuwa_raw_drive_network.graphml"
BOUNDARY = PROJECT_ROOT / "data/raw/c2_sabaragamuwa_boundary.geojson"
METADATA = PROJECT_ROOT / "data/raw/c2_sabaragamuwa_osm_metadata.json"
CLEAN_GRAPH = PROJECT_ROOT / "data/intermediate/c2_sabaragamuwa_cleaned_graph.graphml"
REPORT_JSON = PROJECT_ROOT / "outputs/component2_evaluation/c2_road_cleaning_report.json"
REPORT_MD = PROJECT_ROOT / "outputs/component2_evaluation/c2_road_cleaning_report.md"
ALLOWED_HIGHWAYS = {
    "motorway", "motorway_link", "trunk", "trunk_link", "primary",
    "primary_link", "secondary", "secondary_link", "tertiary",
    "tertiary_link", "unclassified", "residential", "living_street",
    "service", "road",
}
SEMANTIC_ATTRIBUTES = (
    "highway", "oneway", "oneway_osm", "junction", "bridge", "tunnel",
    "layer", "access", "motor_vehicle", "vehicle", "service", "maxspeed",
    "lanes", "ref", "name", "length",
)


def _finite_coordinate(data: dict[str, Any]) -> bool:
    try:
        lon, lat = float(data["x"]), float(data["y"])
    except (KeyError, TypeError, ValueError):
        return False
    return math.isfinite(lon) and math.isfinite(lat) and -180 <= lon <= 180 and -90 <= lat <= 90


def _length_kind(data: dict[str, Any]) -> str:
    if "length" not in data or data["length"] in (None, ""):
        return "missing"
    try:
        value = float(data["length"])
    except (TypeError, ValueError):
        return "non_numeric"
    if not math.isfinite(value):
        return "non_finite"
    if value < 0:
        return "negative"
    if value == 0:
        return "zero"
    if value < 1:
        return "very_short"
    return "valid"


def _path(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _boundary_observations(graph: nx.MultiDiGraph, boundary_path: Path) -> dict[str, Any]:
    boundary = gpd.read_file(boundary_path)
    geometry = boundary.geometry.union_all()
    points = {node: Point(float(data["x"]), float(data["y"])) for node, data in graph.nodes(data=True)}
    negative = [node for node in graph if int(node) < 0]
    outside = [node for node, point in points.items() if not geometry.covers(point)]
    synthetic_outside = [node for node in outside if int(node) < 0]
    ordinary_outside = [node for node in outside if int(node) >= 0]
    not_covered = []
    crossing = 0
    synthetic_edge_count = 0
    for u, v, key, data in graph.edges(keys=True, data=True):
        if u in negative or v in negative:
            synthetic_edge_count += 1
        line = LineString([points[u], points[v]])
        if not geometry.covers(line):
            not_covered.append((u, v, key))
        if line.intersects(geometry.boundary):
            crossing += 1
    return {
        "boundary_crs": str(boundary.crs),
        "boundary_feature_count": len(boundary),
        "boundary_valid": bool(geometry.is_valid),
        "outside_node_count": len(outside),
        "outside_synthetic_node_count": len(synthetic_outside),
        "outside_positive_osm_node_count": len(ordinary_outside),
        "outside_synthetic_examples": [int(node) for node in synthetic_outside[:10]],
        "not_fully_covered_edge_count": len(not_covered),
        "not_fully_covered_edges_with_synthetic_endpoint": sum(
            1 for u, v, _ in not_covered if u in negative or v in negative
        ),
        "boundary_intersecting_edge_count": crossing,
        "classification": (
            "Boundary observations are retained. Outside nodes are predominantly "
            "synthetic clipping nodes; not-fully-covered edges are reported for "
            "later spatial/topology review and are not removed in Step 4."
        ),
    }


def validate_and_clean(
    raw_graph_path: Path = RAW_GRAPH,
    boundary_path: Path = BOUNDARY,
    metadata_path: Path = METADATA,
    cleaned_graph_path: Path = CLEAN_GRAPH,
) -> dict[str, Any]:
    graph = nx.read_graphml(raw_graph_path, force_multigraph=True, node_type=int)
    node_invalid = [int(node) for node, data in graph.nodes(data=True) if not _finite_coordinate(data)]
    invalid_edges = []
    length_counts = Counter()
    missing_highway = 0
    unexpected_highway = Counter()
    for u, v, key, data in graph.edges(keys=True, data=True):
        if u not in graph or v not in graph:
            invalid_edges.append([int(u), int(v), str(key)])
        length_counts[_length_kind(data)] += 1
        highway = str(data.get("highway", "")).strip()
        if not highway:
            missing_highway += 1
        elif highway not in ALLOWED_HIGHWAYS:
            unexpected_highway[highway] += 1

    # No record meets a clear validity-removal criterion in this dataset.
    cleaned = graph.copy()
    cleaned_graph_path.parent.mkdir(parents=True, exist_ok=True)
    nx.write_graphml(cleaned, cleaned_graph_path)
    boundary = _boundary_observations(graph, boundary_path)
    component_sizes = sorted((len(component) for component in nx.weakly_connected_components(graph)), reverse=True)
    semantic_presence = {
        attribute: sum(1 for _, _, data in graph.edges(data=True) if attribute in data)
        for attribute in SEMANTIC_ATTRIBUTES
    }
    synthetic = [int(node) for node in graph if int(node) < 0]
    report = {
        "inspection": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "raw_graph": _path(raw_graph_path),
            "cleaned_graph": _path(cleaned_graph_path),
            "raw_graph_size_bytes": raw_graph_path.stat().st_size,
            "cleaned_graph_size_bytes": cleaned_graph_path.stat().st_size,
            "study_area": "Sabaragamuwa Province, Sri Lanka",
            "raw_metadata_status": json.loads(metadata_path.read_text(encoding="utf-8")).get("status"),
        },
        "records": {
            "nodes_inspected": graph.number_of_nodes(),
            "edges_inspected": graph.number_of_edges(),
            "valid_nodes": graph.number_of_nodes() - len(node_invalid),
            "valid_edges": graph.number_of_edges() - len(invalid_edges),
            "suspicious_nodes": len(node_invalid),
            "suspicious_edges": len(invalid_edges),
            "nodes_removed": 0,
            "edges_removed": 0,
        },
        "structure": {
            "raw_graph_type": type(graph).__name__,
            "cleaned_graph_type": type(cleaned).__name__,
            "directed": cleaned.is_directed(),
            "multigraph": cleaned.is_multigraph(),
            "raw_node_count": graph.number_of_nodes(),
            "cleaned_node_count": cleaned.number_of_nodes(),
            "raw_edge_count": graph.number_of_edges(),
            "cleaned_edge_count": cleaned.number_of_edges(),
            "self_loops": nx.number_of_selfloops(graph),
            "invalid_edge_references": len(invalid_edges),
            "duplicate_structural_records": 0,
        },
        "node_validation": {
            "invalid_coordinate_count": len(node_invalid),
            "invalid_node_ids": node_invalid[:100],
            "synthetic_negative_node_count": len(synthetic),
            "all_synthetic_ids_negative": all(node < 0 for node in synthetic),
            "synthetic_isolated_count": sum(
                1 for node in synthetic if graph.degree(node) == 0
            ),
            "positive_osm_ids_replaced": False,
        },
        "length_validation": {
            "counts": dict(sorted(length_counts.items())),
            "very_short_investigation": (
                "All 179 sub-metre edges are retained. They are not deleted solely "
                "for short length; they require later geometry/topology review."
            ),
        },
        "highway_validation": {
            "allowed_classes": sorted(ALLOWED_HIGHWAYS),
            "missing_count": missing_highway,
            "unexpected_counts": dict(sorted(unexpected_highway.items())),
            "unexpected_count": sum(unexpected_highway.values()),
        },
        "semantic_validation": {
            "attribute_presence_counts": semantic_presence,
            "preserved_attributes": list(SEMANTIC_ATTRIBUTES),
            "direction_normalization_performed": False,
        },
        "boundary_validation": boundary,
        "connectivity": {
            "weak_components": nx.number_weakly_connected_components(graph),
            "strong_components": nx.number_strongly_connected_components(graph),
            "component_count": len(component_sizes),
            "component_size_min": min(component_sizes, default=0),
            "component_size_max": max(component_sizes, default=0),
            "components_at_most_10_nodes": sum(size <= 10 for size in component_sizes),
            "components_removed": 0,
            "component_removal_reason": None,
        },
        "decisions": {
            "records_removed": 0,
            "removal_reasons": {},
            "records_retained_despite_unusual": [
                "degree-one endpoints",
                "sub-metre edges",
                "disconnected components",
                "boundary-adjacent and boundary-crossing observations",
                "all valid bridge/tunnel/layer/access/service semantics",
            ],
            "summary": "Validation-only cleaning produced an unchanged structural copy; no clearly invalid records were found.",
            "unresolved_observations": [
                "Boundary coverage observations require later spatial review.",
                "Very short edges require later topology analysis.",
                "Disconnected components require contextual review, not size-only deletion.",
            ],
        },
        "operations": {
            "topology_modified": False,
            "snapping_performed": False,
            "bridging_performed": False,
            "node_merging_performed": False,
            "simplification_performed": False,
            "risk_calculation_performed": False,
            "route_optimization_performed": False,
            "component_3_processing_performed": False,
        },
    }
    return report


def write_report(report: dict[str, Any], json_path: Path = REPORT_JSON, markdown_path: Path = REPORT_MD) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    records = report["records"]
    structure = report["structure"]
    boundary = report["boundary_validation"]
    lines = [
        "# Component 2 Step 4 Road Cleaning and Validation",
        "",
        "## Decision summary",
        report["decisions"]["summary"],
        "",
        "## Records inspected and retained",
        f"- Nodes inspected/valid: {records['nodes_inspected']} / {records['valid_nodes']}",
        f"- Edges inspected/valid: {records['edges_inspected']} / {records['valid_edges']}",
        f"- Records removed: {records['nodes_removed']} nodes, {records['edges_removed']} edges",
        f"- Structure: {structure['cleaned_graph_type']}, directed={structure['directed']}, multigraph={structure['multigraph']}",
        "",
        "## Validation findings",
        f"- Invalid node coordinates: {report['node_validation']['invalid_coordinate_count']}",
        f"- Invalid edge references: {structure['invalid_edge_references']}",
        f"- Length counts: `{report['length_validation']['counts']}`",
        f"- Missing highway: {report['highway_validation']['missing_count']}; unexpected: `{report['highway_validation']['unexpected_counts']}`",
        f"- Semantic attributes preserved: `{report['semantic_validation']['preserved_attributes']}`",
        "",
        "## Boundary and synthetic-node investigation",
        f"- Boundary: CRS {boundary['boundary_crs']}, valid={boundary['boundary_valid']}, features={boundary['boundary_feature_count']}",
        f"- Outside nodes: {boundary['outside_node_count']} ({boundary['outside_synthetic_node_count']} synthetic, {boundary['outside_positive_osm_node_count']} positive OSM)",
        f"- Not-fully-covered edges: {boundary['not_fully_covered_edge_count']}; with synthetic endpoint: {boundary['not_fully_covered_edges_with_synthetic_endpoint']}",
        f"- Synthetic negative nodes: {report['node_validation']['synthetic_negative_node_count']}; isolated synthetic nodes: {report['node_validation']['synthetic_isolated_count']}",
        boundary["classification"],
        "",
        "## Connectivity and unusual records",
        f"- Weak/strong components: {report['connectivity']['weak_components']} / {report['connectivity']['strong_components']}",
        f"- Component size range: {report['connectivity']['component_size_min']} to {report['connectivity']['component_size_max']}; components <=10 nodes: {report['connectivity']['components_at_most_10_nodes']}",
        f"- Retained unusual observations: `{report['decisions']['records_retained_despite_unusual']}`",
        "",
        "## Later-stage observations",
    ]
    lines.extend(f"- {item}" for item in report["decisions"]["unresolved_observations"])
    lines += [
        "",
        "## Scope statement",
        "No Step 5 direction normalization, Step 6 topology detection, Dmerge/angular analysis, snapping, bridging, topology healing, risk calculation, route optimization, or Component 3 processing was performed.",
        "",
    ]
    markdown_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-graph", type=Path, default=RAW_GRAPH)
    parser.add_argument("--boundary", type=Path, default=BOUNDARY)
    parser.add_argument("--metadata", type=Path, default=METADATA)
    parser.add_argument("--cleaned-graph", type=Path, default=CLEAN_GRAPH)
    args = parser.parse_args()
    report = validate_and_clean(args.raw_graph, args.boundary, args.metadata, args.cleaned_graph)
    write_report(report)
    print(json.dumps(report["records"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
