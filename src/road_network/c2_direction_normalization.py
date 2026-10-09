"""Direction validation and metadata canonicalization for Component 2 Step 5."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

import networkx as nx

NORMALIZED_ATTRIBUTE = "oneway_normalized"
SOURCE_ATTRIBUTE = "direction_source"
EXCLUDED_COMPARISON_ATTRIBUTES = {NORMALIZED_ATTRIBUTE, SOURCE_ATTRIBUTE}
PRESERVATION_CHECKS = (
    "same_node_count",
    "same_edge_count",
    "same_node_ids",
    "same_node_attributes",
    "same_edge_keys",
    "same_original_edge_attributes",
    "graph_type_preserved",
    "directed_preserved",
    "multigraph_preserved",
)


def _source_value(data: dict[str, Any]) -> str:
    if "oneway_osm" in data:
        value = data["oneway_osm"]
        return "" if value in (None, "") else str(value).strip().lower()
    value = data.get("oneway")
    if isinstance(value, bool):
        return "yes" if value else "no"
    if value in (None, ""):
        return ""
    return str(value).strip().lower()


def classify_direction(data: dict[str, Any]) -> tuple[str, str]:
    """Classify the already-stored routing direction without changing topology."""
    value = _source_value(data)
    junction = str(data.get("junction", "")).strip().lower()
    if value in {"yes", "true", "1"}:
        return "forward", "existing_extraction_representation"
    if value == "-1":
        return "reverse", "existing_extraction_representation"
    if value == "no":
        return "bidirectional", "existing_extraction_representation"
    if value == "" and junction == "roundabout":
        return "forward", "roundabout_default"
    if value == "":
        return "bidirectional", "missing_oneway_default"
    return "bidirectional", "unknown_oneway_conservative_fallback"


def _edge_signature(graph: nx.MultiDiGraph, include_normalized: bool) -> dict[tuple[Any, Any, Any], dict[str, Any]]:
    signature: dict[tuple[Any, Any, Any], dict[str, Any]] = {}
    for u, v, key, data in graph.edges(keys=True, data=True):
        attrs = dict(data)
        if not include_normalized:
            for attribute in EXCLUDED_COMPARISON_ATTRIBUTES:
                attrs.pop(attribute, None)
        signature[(u, v, key)] = attrs
    return signature


def _osm_way_statistics(graph: nx.MultiDiGraph) -> dict[str, Any]:
    """Count unique OSM ways separately from directed edge records."""
    ways: dict[Any, list[dict[str, Any]]] = {}
    for _, _, _, data in graph.edges(keys=True, data=True):
        osmid = data.get("osmid")
        if osmid is None:
            continue
        ways.setdefault(osmid, []).append(data)
    counts = Counter()
    for records in ways.values():
        directions = {classify_direction(data)[0] for data in records}
        counts["one_way"] += directions <= {"forward", "reverse"}
        counts["bidirectional"] += "bidirectional" in directions
    return {
        "unique_osm_way_count": len(ways),
        "one_way_osm_way_count": counts["one_way"],
        "bidirectional_osm_way_count": counts["bidirectional"],
        "edges_without_osmid": sum(
            1 for _, _, _, data in graph.edges(keys=True, data=True)
            if data.get("osmid") is None
        ),
        "method": "Group directed edge records by their osmid attribute; no edge-to-way inference is performed.",
    }


def validate_preservation(
    before: nx.MultiDiGraph, after: nx.MultiDiGraph
) -> dict[str, Any]:
    """Compare structure, nodes, and original attributes before and after."""
    node_ids_same = set(before.nodes) == set(after.nodes)
    node_attributes_same = (
        node_ids_same
        and all(before.nodes[node] == after.nodes[node] for node in before.nodes)
    )
    edge_keys_same = _edge_signature(before, False).keys() == _edge_signature(after, False).keys()
    original_edge_attributes_same = (
        edge_keys_same and _edge_signature(before, False) == _edge_signature(after, False)
    )
    return {
        "same_node_count": before.number_of_nodes() == after.number_of_nodes(),
        "same_edge_count": before.number_of_edges() == after.number_of_edges(),
        "same_node_ids": node_ids_same,
        "same_node_attributes": node_attributes_same,
        "same_edge_keys": edge_keys_same,
        "same_original_edge_attributes": original_edge_attributes_same,
        "graph_type_preserved": isinstance(after, nx.MultiDiGraph),
        "directed_preserved": after.is_directed(),
        "multigraph_preserved": after.is_multigraph(),
        "topology_changed": not (
            node_ids_same
            and edge_keys_same
            and before.number_of_edges() == after.number_of_edges()
        ),
    }


def normalize_graph(graph: nx.MultiDiGraph) -> tuple[nx.MultiDiGraph, dict[str, Any]]:
    """Copy a graph and add direction metadata without adding/removing records."""
    if not isinstance(graph, nx.MultiDiGraph):
        raise TypeError("Step 5 requires a networkx.MultiDiGraph input.")
    normalized = graph.copy()
    counts = Counter()
    source_counts = Counter()
    for _, _, _, data in normalized.edges(keys=True, data=True):
        direction, source = classify_direction(data)
        data[NORMALIZED_ATTRIBUTE] = direction
        data[SOURCE_ATTRIBUTE] = source
        counts[direction] += 1
        source_counts[source] += 1
    edge_counts = {
        "directed_edge_records_one_way": counts["forward"] + counts["reverse"],
        "directed_edge_records_bidirectional": counts["bidirectional"],
    }
    return normalized, {
        "directed_edges_before": graph.number_of_edges(),
        "directed_edges_after": normalized.number_of_edges(),
        **edge_counts,
        "osm_way_statistics": _osm_way_statistics(graph),
        "reverse_direction_cases": sum(
            1 for _, _, _, data in graph.edges(keys=True, data=True)
            if _source_value(data) == "-1"
        ),
        "roundabout_records": sum(
            1 for _, _, _, data in graph.edges(keys=True, data=True)
            if str(data.get("junction", "")).strip().lower() == "roundabout"
        ),
        "missing_oneway_records": sum(
            1 for _, _, _, data in graph.edges(keys=True, data=True)
            if _source_value(data) == ""
        ),
        "edges_retained_without_structural_modification": graph.number_of_edges(),
        "direction_metadata_classified": graph.number_of_edges(),
        "direction_semantics_independently_verified": False,
        "direction_semantics_unverified_records": graph.number_of_edges(),
        "verification_limitation": (
            "The cleaned GraphML has edge endpoints and OSM tags but does not "
            "retain source-way geometry/order correspondence needed to independently "
            "verify every stored direction, especially oneway=-1."
        ),
        "normalized_direction_counts": dict(sorted(counts.items())),
        "direction_source_counts": dict(sorted(source_counts.items())),
    }


def normalize_file(input_path: Path, output_path: Path) -> dict[str, Any]:
    """Load, normalize, validate, save, and return the reproducibility report."""
    before = nx.read_graphml(input_path, force_multigraph=True, node_type=int)
    if not isinstance(before, nx.MultiDiGraph):
        raise TypeError("The cleaned graph did not load as a MultiDiGraph.")
    after, direction = normalize_graph(before)
    preservation = validate_preservation(before, after)
    if not all(preservation[key] for key in PRESERVATION_CHECKS):
        raise RuntimeError("Direction normalization changed graph structure or original attributes.")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    nx.write_graphml(after, output_path)
    reloaded = nx.read_graphml(output_path, force_multigraph=True, node_type=int)
    reload_preservation = validate_preservation(before, reloaded)
    if not all(reload_preservation[key] for key in PRESERVATION_CHECKS):
        raise RuntimeError("Serialized normalized graph failed preservation validation.")
    return {
        "input": str(input_path),
        "output": str(output_path),
        "direction": direction,
        "preservation": preservation,
        "serialization_preservation": reload_preservation,
        "normalization_decision": (
            "The extraction stage already encoded OSM direction semantics. "
            "Step 5 added canonical direction metadata only; no edges were "
            "reversed, duplicated, removed, or created."
        ),
        "scope_exclusions": {
            "topology_healing": False,
            "snapping": False,
            "bridging": False,
            "dmerge": False,
            "angular_analysis": False,
            "suspicious_topology_detection": False,
            "risk_calculation": False,
            "route_optimization": False,
            "component_3_processing": False,
        },
    }
