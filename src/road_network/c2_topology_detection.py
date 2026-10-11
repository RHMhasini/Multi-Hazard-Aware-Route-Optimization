"""Read-only candidate detection for possible road-network topology defects."""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

import networkx as nx
from pyproj import CRS, Transformer
from pyproj.exceptions import CRSError
from shapely.geometry import LineString, Point, mapping
from shapely.ops import transform
from shapely.strtree import STRtree

DEFAULT_SEARCH_DISTANCE_M = 20.0
DEFAULT_MAX_HEADING_DEVIATION_DEGREES = 45.0
SOURCE_CRS = "EPSG:4326"
METRIC_CRS = "EPSG:32644"
GEOJSON_CRS = "EPSG:4326"
SEMANTIC_ATTRIBUTES = (
    "layer",
    "bridge",
    "tunnel",
    "highway",
    "access",
    "motor_vehicle",
    "vehicle",
    "service",
)
RESTRICTED_VALUES = {"no", "private", "customers", "delivery", "agricultural", "forestry"}


def _node_key(node: Any) -> str:
    return str(node)


def _node_xy(graph: nx.MultiDiGraph, node: Any) -> tuple[float, float]:
    try:
        x = float(graph.nodes[node]["x"])
        y = float(graph.nodes[node]["y"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            f"Node {node!r} has missing or non-numeric x/y coordinates."
        ) from exc
    if not math.isfinite(x) or not math.isfinite(y):
        raise ValueError(f"Node {node!r} has non-finite x/y coordinates.")
    if not -180.0 <= x <= 180.0 or not -90.0 <= y <= 90.0:
        raise ValueError(
            f"Node {node!r} has invalid EPSG:4326 coordinates: x={x}, y={y}."
        )
    return x, y


def _validate_graph_crs(graph: nx.MultiDiGraph) -> str:
    raw_crs = graph.graph.get("crs")
    if raw_crs in (None, ""):
        raise ValueError(
            "Input graph is missing graph-level CRS metadata; expected EPSG:4326."
        )
    try:
        parsed = CRS.from_user_input(raw_crs)
    except (CRSError, TypeError, ValueError) as exc:
        raise ValueError(
            f"Input graph CRS {raw_crs!r} is not valid; expected EPSG:4326."
        ) from exc
    expected = CRS.from_user_input(SOURCE_CRS)
    if not parsed.equals(expected):
        raise ValueError(
            f"Input graph CRS {raw_crs!r} is incompatible; expected EPSG:4326."
        )
    return parsed.to_string()


def _validate_coordinates(graph: nx.MultiDiGraph) -> None:
    for node in graph.nodes:
        _node_xy(graph, node)


def _clean_values(values: list[Any]) -> tuple[set[str], bool]:
    cleaned = {str(value).strip().lower() for value in values if value not in (None, "")}
    return cleaned, not cleaned or len(cleaned) > 1


def _incident_attributes(graph: nx.MultiDiGraph, node: Any) -> dict[str, Any]:
    values: dict[str, list[Any]] = {attribute: [] for attribute in SEMANTIC_ATTRIBUTES}
    for _, _, data in graph.in_edges(node, data=True):
        for attribute in SEMANTIC_ATTRIBUTES:
            values[attribute].append(data.get(attribute, ""))
    for _, _, data in graph.out_edges(node, data=True):
        for attribute in SEMANTIC_ATTRIBUTES:
            values[attribute].append(data.get(attribute, ""))
    summary: dict[str, Any] = {}
    for attribute, raw_values in values.items():
        normalized, ambiguous = _clean_values(raw_values)
        summary[attribute] = {
            "values": sorted(normalized),
            "missing": not normalized,
            "ambiguous": ambiguous and bool(normalized),
        }
    return summary


def _undirected_neighbors(graph: nx.MultiDiGraph) -> dict[Any, set[Any]]:
    neighbors = {node: set() for node in graph.nodes}
    for u, v in graph.edges():
        if u != v:
            neighbors[u].add(v)
            neighbors[v].add(u)
    return neighbors


def _endpoint_heading(
    graph: nx.MultiDiGraph, node: Any, neighbors: dict[Any, set[Any]]
) -> float | None:
    if len(neighbors[node]) != 1:
        return None
    neighbor = next(iter(neighbors[node]))
    x1, y1 = _node_xy(graph, node)
    x2, y2 = _node_xy(graph, neighbor)
    if x1 == x2 and y1 == y2:
        return None
    return math.degrees(math.atan2(y2 - y1, x2 - x1)) % 360.0


def _heading_deviation(first: float, second: float) -> float:
    difference = abs((first - second) % 360.0)
    difference = min(difference, 360.0 - difference)
    return abs(180.0 - difference)


def _candidate_id(first: Any, second: Any) -> str:
    ordered = sorted((_node_key(first), _node_key(second)))
    token = f"{ordered[0]}:{ordered[1]}"
    return f"candidate-{hashlib.sha256(token.encode('utf-8')).hexdigest()[:16]}"


def _semantic_assessment(
    first: dict[str, Any], second: dict[str, Any]
) -> tuple[str, list[str], list[str]]:
    conflicts: list[str] = []
    missing: list[str] = []
    review_reasons: list[str] = []
    for attribute in SEMANTIC_ATTRIBUTES:
        left = first[attribute]
        right = second[attribute]
        if left["missing"] or right["missing"]:
            missing.append(attribute)
        if left["ambiguous"] or right["ambiguous"]:
            review_reasons.append(f"ambiguous_{attribute}")

    for attribute in ("layer", "bridge", "tunnel"):
        left_values = set(first[attribute]["values"])
        right_values = set(second[attribute]["values"])
        if left_values and right_values and left_values.isdisjoint(right_values):
            conflicts.append(f"{attribute}_conflict")
        elif left_values != right_values:
            review_reasons.append(f"{attribute}_context_requires_review")

    for attribute in ("access", "motor_vehicle", "vehicle"):
        left_values = set(first[attribute]["values"])
        right_values = set(second[attribute]["values"])
        if "no" in left_values and right_values - {"no"}:
            conflicts.append(f"{attribute}_restriction_conflict")
        elif "no" in right_values and left_values - {"no"}:
            conflicts.append(f"{attribute}_restriction_conflict")
        elif left_values & RESTRICTED_VALUES or right_values & RESTRICTED_VALUES:
            review_reasons.append(f"{attribute}_restriction_requires_review")

    if missing:
        review_reasons.append("missing_semantic_attributes")
    if conflicts:
        return "rejected", conflicts, sorted(set(missing + review_reasons))
    if review_reasons:
        return "needs_review", [], sorted(set(missing + review_reasons))
    return "eligible", [], []


def _empty_geojson() -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": [], "crs": {"type": "name", "properties": {"name": GEOJSON_CRS}}}


def detect_candidates(
    graph: nx.MultiDiGraph,
    search_distance_m: float = DEFAULT_SEARCH_DISTANCE_M,
    max_heading_deviation_degrees: float = DEFAULT_MAX_HEADING_DEVIATION_DEGREES,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Detect possible endpoint connections without mutating ``graph``."""
    if not isinstance(graph, nx.MultiDiGraph):
        raise TypeError("Topology detection requires a networkx.MultiDiGraph.")
    if search_distance_m <= 0:
        raise ValueError("search_distance_m must be positive.")
    if not 0 <= max_heading_deviation_degrees <= 180:
        raise ValueError("max_heading_deviation_degrees must be between 0 and 180.")

    validated_crs = _validate_graph_crs(graph)
    _validate_coordinates(graph)
    neighbors = _undirected_neighbors(graph)
    endpoints = sorted((node for node, values in neighbors.items() if len(values) == 1), key=_node_key)
    transformer = Transformer.from_crs(SOURCE_CRS, METRIC_CRS, always_xy=True).transform
    projected_points = [transform(transformer, Point(_node_xy(graph, node))) for node in endpoints]
    tree = STRtree(projected_points)
    candidates: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    semantic_missing_counts: Counter[str] = Counter()
    redundant_pair_count = 0

    for index, first_geometry in enumerate(projected_points):
        for match in tree.query(
            first_geometry,
            predicate="dwithin",
            distance=search_distance_m,
        ):
            second_index = int(match)
            if second_index <= index:
                continue
            first = endpoints[index]
            second = endpoints[second_index]
            first_xy = _node_xy(graph, first)
            second_xy = _node_xy(graph, second)
            projected_second = projected_points[second_index]
            distance_m = float(first_geometry.distance(projected_second))
            if distance_m > search_distance_m:
                continue
            already_connected = graph.has_edge(first, second) or graph.has_edge(second, first)
            if already_connected:
                redundant_pair_count += 1
                continue
            first_heading = _endpoint_heading(graph, first, neighbors)
            second_heading = _endpoint_heading(graph, second, neighbors)
            heading_deviation = (
                _heading_deviation(first_heading, second_heading)
                if first_heading is not None and second_heading is not None
                else None
            )
            first_semantics = _incident_attributes(graph, first)
            second_semantics = _incident_attributes(graph, second)
            semantic_status, conflicts, semantic_reasons = _semantic_assessment(
                first_semantics, second_semantics
            )
            reasons = list(semantic_reasons)
            if heading_deviation is None:
                status = "needs_review"
                reasons.append("heading_unavailable")
            elif heading_deviation > max_heading_deviation_degrees:
                status = "rejected"
                reasons.append("heading_incompatible")
            else:
                status = semantic_status
            if conflicts:
                status = "rejected"
                reasons.extend(conflicts)
            reasons = sorted(set(reasons)) or ["possible_endpoint_gap_requires_validation"]
            candidate = {
                "candidate_id": _candidate_id(first, second),
                "endpoint_u": first,
                "endpoint_v": second,
                "distance_m": round(distance_m, 6),
                "heading_u_degrees": None if first_heading is None else round(first_heading, 6),
                "heading_v_degrees": None if second_heading is None else round(second_heading, 6),
                "heading_deviation_degrees": None if heading_deviation is None else round(heading_deviation, 6),
                "heading_compatible": heading_deviation is not None and heading_deviation <= max_heading_deviation_degrees,
                "already_connected": already_connected,
                "status": status,
                "reasons": reasons,
                "semantic_conflicts": conflicts,
                "semantic_missing_attributes": sorted(
                    attribute for attribute in SEMANTIC_ATTRIBUTES
                    if first_semantics[attribute]["missing"]
                    or second_semantics[attribute]["missing"]
                ),
                "endpoint_semantics": {
                    "u": first_semantics,
                    "v": second_semantics,
                },
                "geometry": {
                    "u": {"x": first_xy[0], "y": first_xy[1]},
                    "v": {"x": second_xy[0], "y": second_xy[1]},
                },
            }
            candidates.append(candidate)
            status_counts[status] += 1
            reason_counts.update(reasons)
            semantic_missing_counts.update(candidate["semantic_missing_attributes"])

    candidates.sort(key=lambda item: item["candidate_id"])
    distances = [item["distance_m"] for item in candidates]
    report = {
        "input_graph": {
            "graph_type": type(graph).__name__,
            "directed": graph.is_directed(),
            "multigraph": graph.is_multigraph(),
            "node_count": graph.number_of_nodes(),
            "edge_count": graph.number_of_edges(),
            "crs": graph.graph.get("crs", SOURCE_CRS),
            "crs_validation": {
                "status": "validated",
                "interpreted_as": validated_crs,
            },
        },
        "configuration": {
            "search_distance_m": search_distance_m,
            "max_heading_deviation_degrees": max_heading_deviation_degrees,
            "source_crs": SOURCE_CRS,
            "metric_crs": METRIC_CRS,
            "geojson_crs": GEOJSON_CRS,
            "coordinate_validation": {
                "status": "validated",
                "unprocessable_node_count": 0,
            },
            "threshold_status": "exploratory; not empirically calibrated",
            "degree_definition": "An endpoint is a node with exactly one unique undirected neighbor after collapsing directed and parallel edge records.",
        },
        "counts": {
            "endpoint_count": len(endpoints),
            "nearby_endpoint_pair_count": len(candidates) + redundant_pair_count,
            "candidate_pair_count": len(candidates),
            "redundant_pair_count": redundant_pair_count,
            "eligible_count": status_counts["eligible"],
            "rejected_count": status_counts["rejected"],
            "needs_review_count": status_counts["needs_review"],
        },
        "distance_statistics_m": {
            "count": len(distances),
            "minimum": min(distances) if distances else None,
            "maximum": max(distances) if distances else None,
            "mean": sum(distances) / len(distances) if distances else None,
        },
        "reason_counts": dict(sorted(reason_counts.items())),
        "semantic_missing_counts": dict(sorted(semantic_missing_counts.items())),
        "limitations": [
            "Candidates are possible topology issues, not confirmed defects.",
            "A degree-one endpoint may be a legitimate cul-de-sac, road end, or boundary artifact.",
            "No graph mutation, snapping, node movement, edge creation, deletion, or direction change is performed.",
            "Thresholds are exploratory and require calibration against independently validated reference cases.",
            "Edge geometry is not retained in the source GraphML; headings use endpoint-to-neighbor coordinate vectors.",
            "Eligible status means only that the configured screening rules found no conflict; it is not repair approval.",
            "Semantic conflicts are conservative screening exclusions, not proof that a physical connection is impossible.",
        ],
        "next_validation_steps": [
            "Review candidates against source OSM geometry and imagery.",
            "Calibrate distance and heading thresholds using labelled reference cases.",
            "Evaluate candidate ranking with independently validated topology cases before any repair stage.",
        ],
        "candidates": candidates,
    }
    features: list[dict[str, Any]] = []
    for candidate in candidates:
        u = candidate["endpoint_u"]
        v = candidate["endpoint_v"]
        line = LineString([
            (candidate["geometry"]["u"]["x"], candidate["geometry"]["u"]["y"]),
            (candidate["geometry"]["v"]["x"], candidate["geometry"]["v"]["y"]),
        ])
        properties = {
            key: value for key, value in candidate.items()
            if key not in {"geometry", "endpoint_semantics"}
        }
        properties["endpoint_semantics"] = json.dumps(candidate["endpoint_semantics"], sort_keys=True)
        features.append({"type": "Feature", "geometry": mapping(line), "properties": properties | {"feature_type": "candidate_connection"}})
        for node, role in ((u, "u"), (v, "v")):
            point = Point(_node_xy(graph, node))
            point_properties = {
                "candidate_id": candidate["candidate_id"],
                "feature_type": f"endpoint_{role}",
                "endpoint": node,
                "status": candidate["status"],
                "reasons": json.dumps(candidate["reasons"]),
            }
            features.append({"type": "Feature", "geometry": mapping(point), "properties": point_properties})
    geojson = _empty_geojson()
    geojson["features"] = features
    return report, geojson


def detect_file(
    input_path: Path,
    report_json_path: Path,
    report_markdown_path: Path,
    geojson_path: Path,
    search_distance_m: float = DEFAULT_SEARCH_DISTANCE_M,
    max_heading_deviation_degrees: float = DEFAULT_MAX_HEADING_DEVIATION_DEGREES,
) -> dict[str, Any]:
    """Run detection from GraphML and write JSON, Markdown, and GeoJSON outputs."""
    graph = nx.read_graphml(input_path, force_multigraph=True, node_type=int)
    report, geojson = detect_candidates(graph, search_distance_m, max_heading_deviation_degrees)
    report["input_graph"]["path"] = str(input_path)
    report_json_path.parent.mkdir(parents=True, exist_ok=True)
    report_markdown_path.parent.mkdir(parents=True, exist_ok=True)
    geojson_path.parent.mkdir(parents=True, exist_ok=True)
    report_json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report_markdown_path.write_text(render_markdown(report), encoding="utf-8")
    geojson_path.write_text(json.dumps(geojson, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def render_markdown(report: dict[str, Any]) -> str:
    counts = report["counts"]
    configuration = report["configuration"]
    lines = [
        "# Component 2 Topology Candidate Detection",
        "",
        "## Interpretation",
        "These are possible topology-defect candidates, not confirmed defects or repairs.",
        "",
        "## Input and graph statistics",
        f"- Input: `{report['input_graph'].get('path', '<in-memory graph>')}`",
        f"- Graph: `{report['input_graph']['graph_type']}`; directed={report['input_graph']['directed']}; multigraph={report['input_graph']['multigraph']}",
        f"- Nodes / directed edge records: {report['input_graph']['node_count']} / {report['input_graph']['edge_count']}",
        "",
        "## Algorithm and configuration",
        f"- Endpoint definition: {configuration['degree_definition']}",
        f"- Search distance: {configuration['search_distance_m']} m",
        f"- Maximum exploratory heading deviation: {configuration['max_heading_deviation_degrees']} degrees",
        f"- CRS transformation: {configuration['source_crs']} -> {configuration['metric_crs']} for distance; GeoJSON exported as {configuration['geojson_crs']}",
        f"- CRS validation: {report['input_graph']['crs_validation']['status']} ({report['input_graph']['crs_validation']['interpreted_as']})",
        f"- Coordinate validation: {configuration['coordinate_validation']['status']}; unprocessable nodes: {configuration['coordinate_validation']['unprocessable_node_count']}",
        f"- Calibration status: {configuration['threshold_status']}",
        "",
        "## Candidate counts",
        f"- Endpoints: {counts['endpoint_count']}",
        f"- Nearby endpoint pairs before redundancy exclusion: {counts['nearby_endpoint_pair_count']}",
        f"- Candidate pairs: {counts['candidate_pair_count']}",
        f"- Redundant endpoint pairs excluded: {counts['redundant_pair_count']}",
        f"- Eligible: {counts['eligible_count']}",
        f"- Rejected: {counts['rejected_count']}",
        f"- Needs review: {counts['needs_review_count']}",
        "",
        "## Reasons",
        f"- `{report['reason_counts']}`",
        f"- Missing semantic attributes: `{report['semantic_missing_counts']}`",
        "",
        "## Limitations and next validation steps",
    ]
    lines.extend(f"- {item}" for item in report["limitations"])
    lines.append("")
    lines.extend(f"- {item}" for item in report["next_validation_steps"])
    return "\n".join(lines) + "\n"
