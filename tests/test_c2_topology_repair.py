"""Focused tests for Component 2 Function 2 conservative topology repair."""

from __future__ import annotations

import importlib.util
import json
import hashlib
from pathlib import Path

import networkx as nx
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_ROOT / "src" / "road_network" / "c2_topology_repair.py"
SPEC = importlib.util.spec_from_file_location("c2_topology_repair", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _graph() -> nx.MultiDiGraph:
    graph = nx.MultiDiGraph(crs="EPSG:4326", source="synthetic")
    graph.add_node(1, x="80.0", y="7.0", label="u")
    graph.add_node(2, x="80.001", y="7.0", label="v")
    graph.add_edge(
        1, 3, key=0, highway="residential", oneway_normalized="forward",
        layer="0", bridge="no", tunnel="no", custom="left",
    )
    graph.add_edge(
        4, 2, key=0, highway="residential", oneway_normalized="forward",
        layer="0", bridge="no", tunnel="no", custom="right",
    )
    return graph


def _candidate(**overrides):
    candidate = {
        "candidate_id": "candidate-test-1",
        "endpoint_u": 1,
        "endpoint_v": 2,
        "distance_m": 10.0,
        "status": "eligible",
        "already_connected": False,
        "heading_compatible": True,
        "semantic_conflicts": [],
        "semantic_missing_attributes": [],
    }
    candidate.update(overrides)
    return candidate


def _approval(**overrides):
    approval = {
        "candidate_id": "candidate-test-1",
        "approved": True,
        "direction": "u_to_v",
        "reason": "Independently reviewed synthetic gap.",
        "edge_attributes": {
            "oneway_normalized": "forward",
            "layer": "0",
            "bridge": "no",
            "tunnel": "no",
            "direction_source": "component2_explicit_approval",
        },
    }
    approval.update(overrides)
    return approval


def test_explicit_approval_adds_one_directed_connector_and_preserves_attributes():
    graph = _graph()
    before = (dict(graph.graph), dict(graph.nodes(data=True)), list(graph.edges(keys=True, data=True)))
    repaired, report = MODULE.repair_candidates(graph, [_candidate()], [_approval()])
    assert repaired.has_edge(1, 2)
    assert not repaired.has_edge(2, 1)
    assert report["counts"]["accepted_repair_count"] == 1
    assert report["accepted_repairs"][0]["repair_id"].startswith("repair-")
    assert before == (dict(graph.graph), dict(graph.nodes(data=True)), list(graph.edges(keys=True, data=True)))
    assert repaired.nodes[1] == graph.nodes[1]
    assert repaired.edges[1, 3, 0] == graph.edges[1, 3, 0]


@pytest.mark.parametrize(
    ("candidate", "reason"),
    [
        (_candidate(status="rejected"), "candidate_status_rejected"),
        (_candidate(status="needs_review"), "candidate_status_needs_review"),
        (_candidate(semantic_missing_attributes=["layer"]), "missing_semantic_attributes"),
        (_candidate(semantic_conflicts=["layer_conflict"]), "unresolved_semantic_conflicts"),
        (_candidate(heading_compatible=False), "heading_not_compatible"),
    ],
)
def test_unrepairable_candidates_are_skipped(candidate, reason):
    repaired, report = MODULE.repair_candidates(_graph(), [candidate], [_approval()])
    assert report["counts"]["accepted_repair_count"] == 0
    assert report["skipped_candidates"][0]["reason"] == reason
    assert repaired.number_of_edges() == 2


def test_missing_approval_is_not_implicitly_accepted():
    repaired, report = MODULE.repair_candidates(_graph(), [_candidate()])
    assert report["counts"]["accepted_repair_count"] == 0
    assert report["skipped_candidates"][0]["reason"] == "not_explicitly_approved"
    assert repaired.number_of_edges() == 2


@pytest.mark.parametrize("conflict", ["bridge_conflict", "tunnel_conflict", "layer_conflict"])
def test_bridge_tunnel_and_layer_conflicts_are_not_overridden(conflict):
    _, report = MODULE.repair_candidates(
        _graph(), [_candidate(semantic_conflicts=[conflict])], [_approval()]
    )
    assert report["counts"]["accepted_repair_count"] == 0
    assert report["skipped_candidates"][0]["reason"] == "unresolved_semantic_conflicts"


def test_direction_is_explicit_and_reverse_edge_is_never_invented():
    repaired, report = MODULE.repair_candidates(
        _graph(), [_candidate()], [_approval(direction="v_to_u")]
    )
    assert repaired.has_edge(2, 1)
    assert not repaired.has_edge(1, 2)
    assert report["accepted_repairs"][0]["direction"] == "v_to_u"


def test_parallel_existing_edge_skips_duplicate_connection():
    graph = _graph()
    graph.add_edge(1, 2, key=9, existing="parallel")
    _, report = MODULE.repair_candidates(graph, [_candidate()], [_approval()])
    assert report["accepted_repairs"] == []
    assert report["skipped_candidates"][0]["reason"] == "connection_exists_at_application_time"


def test_unknown_approval_is_rejected_and_accounted_for():
    unknown = _approval(candidate_id="candidate-unknown")
    _, report = MODULE.repair_candidates(_graph(), [_candidate()], [unknown])
    assert report["counts"]["manifest_entry_count"] == 1
    assert report["counts"]["valid_approval_count"] == 0
    assert report["counts"]["rejected_approval_count"] == 1
    assert report["counts"]["accepted_repair_count"] == 0
    assert report["rejected_approvals"][0]["reason"] == "unknown_candidate_id"


def test_duplicate_approval_ids_fail_safely():
    with pytest.raises(ValueError, match="Duplicate approval"):
        MODULE.repair_candidates(_graph(), [_candidate()], [_approval(), _approval()])


@pytest.mark.parametrize(
    "approval",
    [
        _approval(direction="both"),
        _approval(edge_attributes={}),
        _approval(reason=""),
        {**_approval(), "approved": False},
    ],
)
def test_unsafe_or_incomplete_approval_fails_before_mutation(approval):
    graph = _graph()
    with pytest.raises(ValueError):
        MODULE.repair_candidates(graph, [_candidate()], [approval])
    assert graph.number_of_edges() == 2


@pytest.mark.parametrize(
    "approval",
    [
        _approval(edge_attributes={"layer": "0", "bridge": "no", "tunnel": "no",
                                   "oneway_normalized": "forward"}),
        _approval(edge_attributes={**_approval()["edge_attributes"], "highway": "residential"}),
        _approval(edge_attributes={**_approval()["edge_attributes"], "layer": 0}),
        _approval(edge_attributes={**_approval()["edge_attributes"], "oneway_normalized": "bidirectional"}),
        _approval(edge_attributes={**_approval()["edge_attributes"], "direction_source": "existing_extraction_representation"}),
    ],
)
def test_connector_schema_is_strict(approval):
    with pytest.raises(ValueError):
        MODULE.repair_candidates(_graph(), [_candidate()], [approval])


def test_missing_endpoint_and_malformed_candidate_fail_safely():
    with pytest.raises(ValueError, match="missing endpoint"):
        MODULE.repair_candidates(
            _graph(), [_candidate(endpoint_v=999)], [_approval()]
        )
    with pytest.raises(ValueError, match="missing required fields"):
        MODULE.repair_candidates(_graph(), [{"candidate_id": "bad"}], [])


def test_deterministic_repair_id_and_report_accounting():
    first_graph, first_report = MODULE.repair_candidates(_graph(), [_candidate()], [_approval()])
    second_graph, second_report = MODULE.repair_candidates(_graph(), [_candidate()], [_approval()])
    assert first_report == second_report
    assert first_report["counts"]["candidate_count"] == (
        first_report["counts"]["accepted_repair_count"]
        + first_report["counts"]["skipped_candidate_count"]
    )
    assert list(first_graph.edges(data=True)) == list(second_graph.edges(data=True))
    evidence = first_report["accepted_repairs"][0]["candidate_evidence"]
    assert evidence["candidate_id"] == "candidate-test-1"
    assert evidence["endpoint_u"] == 1
    assert evidence["endpoint_v"] == 2
    assert evidence["status"] == "eligible"
    assert evidence["distance_m"] == 10.0
    assert evidence["semantic_conflicts"] == []
    assert evidence["semantic_missing_attributes"] == []


def test_real_data_run_without_approvals_has_zero_real_repairs():
    _, report = MODULE.repair_candidates(
        _graph(), [_candidate(status="rejected")], [], real_data_run=True
    )
    assert report["counts"]["real_data_repair_count"] == 0
    assert report["real_data_repair_occurred"] is False


def test_repair_file_writes_graph_and_reports(tmp_path):
    graph_path = tmp_path / "input.graphml"
    nx.write_graphml(_graph(), graph_path)
    candidate_path = tmp_path / "candidates.json"
    candidate_payload = {"schema_identifier": "c2-topology-candidates/v1", "candidates": [_candidate()]}
    candidate_bytes = json.dumps(candidate_payload).encode("utf-8")
    candidate_path.write_bytes(candidate_bytes)
    approval_path = tmp_path / "approvals.json"
    approval_path.write_text(json.dumps({"approvals": [_approval()]}), encoding="utf-8")
    output_path = tmp_path / "repaired.graphml"
    report_path = tmp_path / "report.json"
    markdown_path = tmp_path / "report.md"
    report = MODULE.repair_file(
        graph_path, candidate_path, output_path, report_path, markdown_path, approval_path
    )
    assert output_path.is_file() and report_path.is_file() and markdown_path.is_file()
    saved_report = json.loads(report_path.read_text(encoding="utf-8"))
    assert saved_report["counts"]["accepted_repair_count"] == 1
    assert saved_report["provenance"]["candidate_report_sha256"] == hashlib.sha256(candidate_bytes).hexdigest()
    assert saved_report["accepted_repairs"][0]["candidate_evidence"]["candidate_report_schema"] == "c2-topology-candidates/v1"
    loaded = nx.read_graphml(output_path, force_multigraph=True, node_type=int)
    connector = loaded.edges[1, 2, 0]
    assert connector["c2_repair_id"] == saved_report["accepted_repairs"][0]["repair_id"]
    assert connector["c2_repair_candidate_id"] == "candidate-test-1"
    assert connector["c2_repair_direction"] == "u_to_v"
    assert connector["c2_repair_action"] == "add_approved_directed_connector"
    assert connector["c2_repair_reason"] == "Independently reviewed synthetic gap."
    assert connector["oneway_normalized"] == "forward"
    assert connector["direction_source"] == "component2_explicit_approval"
    assert saved_report["before"] == {
        "node_count": 4,
        "edge_count": 2,
        "weak_component_count": 2,
        "strong_component_count": 4,
    }
    assert saved_report["after"] == {
        "node_count": 4,
        "edge_count": 3,
        "weak_component_count": 1,
        "strong_component_count": 4,
    }


def test_no_approval_copy_is_byte_identical(tmp_path):
    graph_path = tmp_path / "input.graphml"
    nx.write_graphml(_graph(), graph_path)
    candidate_path = tmp_path / "candidates.json"
    candidate_path.write_text(json.dumps({"candidates": [_candidate(status="rejected")]}), encoding="utf-8")
    output_path = tmp_path / "copied.graphml"
    report = MODULE.repair_file(
        graph_path, candidate_path, output_path, tmp_path / "report.json", tmp_path / "report.md"
    )
    assert output_path.read_bytes() == graph_path.read_bytes()
    assert report["counts"]["accepted_repair_count"] == 0
