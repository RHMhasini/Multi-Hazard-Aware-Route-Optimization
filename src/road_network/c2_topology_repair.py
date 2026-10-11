"""Component 2 Function 2: conservative, approval-gated topology repair."""

from __future__ import annotations

import hashlib
import json
import math
import shutil
from pathlib import Path
from typing import Any

import networkx as nx

VALID_STATUSES = {"eligible", "rejected", "needs_review"}
VALID_DIRECTIONS = {"u_to_v", "v_to_u"}
REQUIRED_CANDIDATE_FIELDS = {
    "candidate_id",
    "endpoint_u",
    "endpoint_v",
    "distance_m",
    "status",
    "already_connected",
    "heading_compatible",
    "semantic_conflicts",
    "semantic_missing_attributes",
}
SCALAR_TYPES = (str, int, float, bool)
CONNECTOR_REQUIRED_ATTRIBUTES = {
    "layer",
    "bridge",
    "tunnel",
    "oneway_normalized",
    "direction_source",
}
CONNECTOR_ALLOWED_ATTRIBUTES = CONNECTOR_REQUIRED_ATTRIBUTES
CONNECTOR_DIRECTION_SOURCE = "component2_explicit_approval"


def _repair_id(candidate_id: str, direction: str) -> str:
    token = f"{candidate_id}:{direction}".encode("utf-8")
    return f"repair-{hashlib.sha256(token).hexdigest()[:16]}"


def _graph_counts(graph: nx.MultiDiGraph) -> dict[str, int]:
    return {
        "node_count": graph.number_of_nodes(),
        "edge_count": graph.number_of_edges(),
        "weak_component_count": nx.number_weakly_connected_components(graph),
        "strong_component_count": nx.number_strongly_connected_components(graph),
    }


def _is_scalar(value: Any) -> bool:
    return isinstance(value, SCALAR_TYPES) and not (
        isinstance(value, float) and not math.isfinite(value)
    )


def _validate_candidate(candidate: Any, index: int) -> None:
    if not isinstance(candidate, dict):
        raise ValueError(f"Candidate at index {index} must be an object.")
    missing = sorted(REQUIRED_CANDIDATE_FIELDS - candidate.keys())
    if missing:
        raise ValueError(
            f"Candidate at index {index} is missing required fields: {', '.join(missing)}."
        )
    if not isinstance(candidate["candidate_id"], str) or not candidate["candidate_id"]:
        raise ValueError(f"Candidate at index {index} has an invalid candidate_id.")
    if candidate["status"] not in VALID_STATUSES:
        raise ValueError(f"Candidate {candidate['candidate_id']} has an invalid status.")
    if not isinstance(candidate["semantic_conflicts"], list):
        raise ValueError(f"Candidate {candidate['candidate_id']} has invalid semantic_conflicts.")
    if not isinstance(candidate["semantic_missing_attributes"], list):
        raise ValueError(
            f"Candidate {candidate['candidate_id']} has invalid semantic_missing_attributes."
        )
    if not isinstance(candidate["already_connected"], bool):
        raise ValueError(f"Candidate {candidate['candidate_id']} has invalid already_connected.")
    if not isinstance(candidate["heading_compatible"], bool):
        raise ValueError(
            f"Candidate {candidate['candidate_id']} has invalid heading_compatible."
        )


def _validate_approval(approval: Any, index: int) -> None:
    if not isinstance(approval, dict):
        raise ValueError(f"Approval at index {index} must be an object.")
    required = {"candidate_id", "approved", "direction", "edge_attributes", "reason"}
    missing = sorted(required - approval.keys())
    if missing:
        raise ValueError(
            f"Approval at index {index} is missing required fields: {', '.join(missing)}."
        )
    if not isinstance(approval["candidate_id"], str) or not approval["candidate_id"]:
        raise ValueError(f"Approval at index {index} has an invalid candidate_id.")
    if approval["approved"] is not True:
        raise ValueError(
            f"Approval {approval['candidate_id']} must explicitly set approved to true."
        )
    if approval["direction"] not in VALID_DIRECTIONS:
        raise ValueError(
            f"Approval {approval['candidate_id']} must use u_to_v or v_to_u."
        )
    if not isinstance(approval["reason"], str) or not approval["reason"].strip():
        raise ValueError(f"Approval {approval['candidate_id']} requires a reason.")
    attributes = approval["edge_attributes"]
    if not isinstance(attributes, dict) or not attributes:
        raise ValueError(
            f"Approval {approval['candidate_id']} requires explicit edge_attributes."
        )
    missing = sorted(CONNECTOR_REQUIRED_ATTRIBUTES - attributes.keys())
    if missing:
        raise ValueError(
            f"Approval {approval['candidate_id']} is missing connector attributes: "
            f"{', '.join(missing)}."
        )
    unsupported = sorted(set(attributes) - CONNECTOR_ALLOWED_ATTRIBUTES)
    if unsupported:
        raise ValueError(
            f"Approval {approval['candidate_id']} contains unsupported connector "
            f"attributes: {', '.join(unsupported)}."
        )
    invalid = sorted(
        key for key, value in attributes.items()
        if not isinstance(key, str) or not key or not isinstance(value, str)
    )
    if invalid:
        raise ValueError(
            f"Approval {approval['candidate_id']} contains non-string connector "
            "attributes; the existing GraphML edge schema is string-valued."
        )
    if attributes["oneway_normalized"] != "forward":
        raise ValueError(
            f"Approval {approval['candidate_id']} must set oneway_normalized to forward."
        )
    if attributes["direction_source"] != CONNECTOR_DIRECTION_SOURCE:
        raise ValueError(
            f"Approval {approval['candidate_id']} must identify the synthetic "
            f"direction source as {CONNECTOR_DIRECTION_SOURCE!r}."
        )


def _validate_inputs(
    graph: nx.MultiDiGraph,
    candidates: list[Any],
    approvals: list[Any],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    if not isinstance(graph, nx.MultiDiGraph) or not graph.is_directed():
        raise TypeError("Topology repair requires a directed networkx.MultiDiGraph.")
    for index, candidate in enumerate(candidates):
        _validate_candidate(candidate, index)
        if candidate["endpoint_u"] not in graph or candidate["endpoint_v"] not in graph:
            raise ValueError(
                f"Candidate {candidate['candidate_id']} references a missing endpoint."
            )
    candidate_ids = {candidate["candidate_id"] for candidate in candidates}
    if len(candidate_ids) != len(candidates):
        raise ValueError("Candidate output contains duplicate candidate_id values.")
    approval_map: dict[str, dict[str, Any]] = {}
    seen_approval_ids: set[str] = set()
    rejected_approvals: list[dict[str, Any]] = []
    for index, approval in enumerate(approvals):
        _validate_approval(approval, index)
        candidate_id = approval["candidate_id"]
        if candidate_id in seen_approval_ids:
            raise ValueError(f"Duplicate approval for candidate {candidate_id}.")
        seen_approval_ids.add(candidate_id)
        if candidate_id not in candidate_ids:
            rejected_approvals.append(
                {
                    "candidate_id": candidate_id,
                    "status": "rejected",
                    "reason": "unknown_candidate_id",
                }
            )
            continue
        approval_map[candidate_id] = approval
    return approval_map, rejected_approvals


def _skip(candidate: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "candidate_id": candidate["candidate_id"],
        "repair_id": None,
        "status": "skipped",
        "action": "none",
        "reason": reason,
        "endpoint_u": candidate.get("endpoint_u"),
        "endpoint_v": candidate.get("endpoint_v"),
    }


def repair_candidates(
    graph: nx.MultiDiGraph,
    candidates: list[dict[str, Any]],
    approvals: list[dict[str, Any]] | None = None,
    *,
    real_data_run: bool = False,
    candidate_report_sha256: str | None = None,
    candidate_report_schema: str | None = None,
) -> tuple[nx.MultiDiGraph, dict[str, Any]]:
    """Apply only explicitly approved one-way connector repairs to a graph copy."""
    approvals = [] if approvals is None else approvals
    approval_map, rejected_approvals = _validate_inputs(graph, candidates, approvals)
    repaired = graph.copy()
    accepted: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for candidate in sorted(candidates, key=lambda item: item["candidate_id"]):
        candidate_id = candidate["candidate_id"]
        approval = approval_map.get(candidate_id)
        if approval is None:
            skipped.append(_skip(candidate, "not_explicitly_approved"))
            continue
        if candidate["status"] != "eligible":
            skipped.append(_skip(candidate, f"candidate_status_{candidate['status']}"))
            continue
        if candidate["already_connected"]:
            skipped.append(_skip(candidate, "already_connected"))
            continue
        if candidate["semantic_conflicts"]:
            skipped.append(_skip(candidate, "unresolved_semantic_conflicts"))
            continue
        if candidate["semantic_missing_attributes"]:
            skipped.append(_skip(candidate, "missing_semantic_attributes"))
            continue
        if not candidate["heading_compatible"]:
            skipped.append(_skip(candidate, "heading_not_compatible"))
            continue

        u = candidate["endpoint_u"]
        v = candidate["endpoint_v"]
        source, target = (u, v) if approval["direction"] == "u_to_v" else (v, u)
        if repaired.has_edge(source, target) or repaired.has_edge(target, source):
            skipped.append(_skip(candidate, "connection_exists_at_application_time"))
            continue
        repair_id = _repair_id(candidate_id, approval["direction"])
        edge_attributes = dict(approval["edge_attributes"])
        edge_attributes.update(
            {
                "c2_repair_id": repair_id,
                "c2_repair_candidate_id": candidate_id,
                "c2_repair_action": "add_approved_directed_connector",
                "c2_repair_direction": approval["direction"],
                "c2_repair_reason": approval["reason"],
            }
        )
        key = repaired.add_edge(source, target, **edge_attributes)
        accepted.append(
            {
                "candidate_id": candidate_id,
                "repair_id": repair_id,
                "status": "accepted",
                "action": "add_approved_directed_connector",
                "reason": approval["reason"],
                "endpoint_u": u,
                "endpoint_v": v,
                "source": source,
                "target": target,
                "edge_key": key,
                "distance_m": candidate["distance_m"],
                "direction": approval["direction"],
                "edge_attributes": edge_attributes,
                "candidate_evidence": {
                    "candidate_id": candidate_id,
                    "endpoint_u": u,
                    "endpoint_v": v,
                    "status": candidate["status"],
                    "distance_m": candidate["distance_m"],
                    "heading_compatible": candidate["heading_compatible"],
                    "heading_deviation_degrees": candidate.get(
                        "heading_deviation_degrees"
                    ),
                    "semantic_conflicts": candidate["semantic_conflicts"],
                    "semantic_missing_attributes": candidate[
                        "semantic_missing_attributes"
                    ],
                    "already_connected": candidate["already_connected"],
                    "candidate_report_schema": candidate_report_schema,
                    "unavailable_fields": [
                        field
                        for field in (
                            "heading_deviation_degrees",
                        )
                        if candidate.get(field) is None
                    ],
                },
                "candidate_report_sha256": candidate_report_sha256,
                "candidate_report_schema": candidate_report_schema,
            }
        )

    before = _graph_counts(graph)
    after = _graph_counts(repaired)
    report = {
        "function": "Component 2 Function 2: conservative topology repair",
        "input_graph": {
            "graph_type": type(graph).__name__,
            "directed": graph.is_directed(),
            "multigraph": graph.is_multigraph(),
        },
        "policy": {
            "operation": "add one explicitly approved directed connector edge",
            "approval_gate": (
                "Candidate status must be eligible, explicitly approved, not already "
                "connected, heading-compatible, and free of missing or conflicting "
                "semantic evidence."
            ),
            "direction_policy": (
                "The approval manifest must name exactly one direction; reverse edges "
                "are never inferred or added."
            ),
            "semantic_policy": (
                "Missing, ambiguous, bridge, tunnel, layer, and access conflicts are "
                "not overridden by proximity and remain unrepairable without new evidence."
            ),
            "threshold_status": "Inherited from candidate output; not calibrated.",
        },
        "counts": {
            "candidate_count": len(candidates),
            "approval_count": len(approvals),
            "manifest_entry_count": len(approvals),
            "valid_approval_count": len(approval_map),
            "rejected_approval_count": len(rejected_approvals),
            "accepted_repair_count": len(accepted),
            "skipped_candidate_count": len(skipped),
            "real_data_repair_count": len(accepted) if real_data_run else 0,
        },
        "before": before,
        "after": after,
        "accepted_repairs": accepted,
        "skipped_candidates": skipped,
        "rejected_approvals": rejected_approvals,
        "real_data_repair_occurred": bool(real_data_run and accepted),
        "provenance": {
            "source_graph_mutated": False,
            "source_graph_preserved": True,
            "candidate_evidence_used": True,
            "approval_manifest_required_for_change": True,
            "candidate_report_sha256": candidate_report_sha256,
            "candidate_report_hash_kind": "exact_file_bytes"
            if candidate_report_sha256
            else None,
            "candidate_report_schema": candidate_report_schema,
        },
        "limitations": [
            "No node merging, snapping, coordinate movement, reverse-edge inference, or semantic conflict override is performed.",
            "An accepted synthetic repair is an explicitly approved graph edit, not evidence of a real topology defect.",
            "Real-data repair requires independently reviewed approvals; the default study-area run supplies none.",
        ],
    }
    return repaired, report


def load_candidate_report(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("candidates"), list):
        raise ValueError("Candidate report must contain a candidates list.")
    return payload["candidates"]


def _load_candidate_report_document(
    path: Path,
) -> tuple[list[dict[str, Any]], str | None]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("candidates"), list):
        raise ValueError("Candidate report must contain a candidates list.")
    schema = payload.get("schema_identifier", payload.get("schema_version"))
    if schema is not None and not isinstance(schema, str):
        raise ValueError("Candidate report schema identifier must be a string.")
    return payload["candidates"], schema


def load_approvals(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("approvals"), list):
        raise ValueError("Approval manifest must contain an approvals list.")
    return payload["approvals"]


def repair_file(
    input_graph_path: Path,
    candidate_report_path: Path,
    output_graph_path: Path,
    report_json_path: Path,
    report_markdown_path: Path,
    approvals_path: Path | None = None,
    *,
    real_data_run: bool = False,
) -> dict[str, Any]:
    if not input_graph_path.is_file():
        raise FileNotFoundError(f"Input graph does not exist: {input_graph_path}")
    if not candidate_report_path.is_file():
        raise FileNotFoundError(f"Candidate report does not exist: {candidate_report_path}")
    graph = nx.read_graphml(input_graph_path, force_multigraph=True, node_type=int)
    candidate_report_bytes = candidate_report_path.read_bytes()
    candidates, candidate_report_schema = _load_candidate_report_document(
        candidate_report_path
    )
    approvals = load_approvals(approvals_path)
    repaired, report = repair_candidates(
        graph,
        candidates,
        approvals,
        real_data_run=real_data_run,
        candidate_report_sha256=hashlib.sha256(candidate_report_bytes).hexdigest(),
        candidate_report_schema=candidate_report_schema,
    )
    output_graph_path.parent.mkdir(parents=True, exist_ok=True)
    report_json_path.parent.mkdir(parents=True, exist_ok=True)
    report_markdown_path.parent.mkdir(parents=True, exist_ok=True)
    if report["counts"]["accepted_repair_count"] == 0:
        # The no-approval real-data path remains byte-for-byte separate from the
        # source graph without needlessly reserializing a 600k-edge GraphML file.
        shutil.copyfile(input_graph_path, output_graph_path)
    else:
        nx.write_graphml(repaired, output_graph_path)
    report.update(
        {
            "input_graph_path": str(input_graph_path),
            "candidate_report_path": str(candidate_report_path),
            "approval_manifest_path": None if approvals_path is None else str(approvals_path),
            "output_graph_path": str(output_graph_path),
        }
    )
    report_json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    report_markdown_path.write_text(render_markdown(report), encoding="utf-8")
    return report


def render_markdown(report: dict[str, Any]) -> str:
    counts = report["counts"]
    before = report["before"]
    after = report["after"]
    lines = [
        "# Component 2 Topology Repair",
        "",
        "## Interpretation",
        "Repairs are conservative, explicit graph edits. Candidate proximity is not proof of a defect.",
        "",
        "## Inputs and policy",
        f"- Input graph: `{report['input_graph_path']}`",
        f"- Candidate report: `{report['candidate_report_path']}`",
        f"- Approval manifest: `{report['approval_manifest_path']}`",
        f"- Output graph: `{report['output_graph_path']}`",
        f"- Operation: {report['policy']['operation']}",
        f"- Approval gate: {report['policy']['approval_gate']}",
        f"- Direction policy: {report['policy']['direction_policy']}",
        f"- Candidate report SHA-256: `{report['provenance']['candidate_report_sha256']}`",
        "",
        "## Counts",
        f"- Candidates: {counts['candidate_count']}",
        f"- Explicit approvals supplied: {counts['approval_count']}",
        f"- Accepted repairs: {counts['accepted_repair_count']}",
        f"- Skipped candidates: {counts['skipped_candidate_count']}",
        f"- Manifest entries: {counts['manifest_entry_count']}",
        f"- Valid approvals: {counts['valid_approval_count']}",
        f"- Rejected approvals: {counts['rejected_approval_count']}",
        f"- Real-data repairs: {counts['real_data_repair_count']}",
        f"- Real-data repair occurred: {report['real_data_repair_occurred']}",
        "",
        "## Graph statistics",
        f"- Before: `{before}`",
        f"- After: `{after}`",
        "",
        "## Accepted repairs",
    ]
    lines.extend(
        f"- `{item['repair_id']}`: {item['source']} -> {item['target']} "
        f"({item['direction']}); reason: {item['reason']}"
        for item in report["accepted_repairs"]
    )
    if not report["accepted_repairs"]:
        lines.append("- None.")
    lines.extend(["", "## Skipped candidates"])
    lines.extend(
        f"- `{item['candidate_id']}`: {item['reason']}"
        for item in report["skipped_candidates"]
    )
    if not report["skipped_candidates"]:
        lines.append("- None.")
    lines.extend(["", "## Rejected approvals"])
    lines.extend(
        f"- `{item['candidate_id']}`: {item['reason']}"
        for item in report["rejected_approvals"]
    )
    if not report["rejected_approvals"]:
        lines.append("- None.")
    lines.extend(
        [
            "",
            "## Limitations",
            *[f"- {item}" for item in report["limitations"]],
            "",
            "## Provenance",
            f"- Source graph mutated: {report['provenance']['source_graph_mutated']}",
            f"- Source graph preserved: {report['provenance']['source_graph_preserved']}",
        ]
    )
    return "\n".join(lines) + "\n"
