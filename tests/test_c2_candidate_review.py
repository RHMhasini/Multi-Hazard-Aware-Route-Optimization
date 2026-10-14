import hashlib
import importlib.util
import json
from pathlib import Path

import networkx as nx
import pytest

from src.road_network import c2_candidate_review as review_module


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPAIR_PATH = PROJECT_ROOT / "src/road_network/c2_topology_repair.py"
SPEC = importlib.util.spec_from_file_location("c2_topology_repair_for_review_test", REPAIR_PATH)
assert SPEC and SPEC.loader
REPAIR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REPAIR)


def _source_report(tmp_path: Path) -> Path:
    path = tmp_path / "candidates.json"
    path.write_text(json.dumps({"candidates": [
        {"candidate_id": "candidate-a", "status": "needs_review"},
        {"candidate_id": "candidate-b", "status": "rejected"},
    ]}), encoding="utf-8")
    return path


def _reviewed(dataset, candidate_id="candidate-a", classification="likely_gap"):
    item = next(record for record in dataset["reviews"] if record["candidate_id"] == candidate_id)
    item.update({
        "review_state": "reviewed",
        "classification": classification,
        "reviewer_decision": "Reviewed against the cited evidence; not a repair approval.",
        "supporting_evidence": [{
            "evidence_type": "imagery",
            "description": "Road approaches appear aligned at this location.",
            "reference": "researcher-supplied-image-reference",
        }],
        "limitations": ["Imagery does not establish legal access or road direction."],
    })
    return dataset


def test_candidate_ids_are_stable_and_new_records_are_explicitly_unreviewed(tmp_path):
    report_path = _source_report(tmp_path)
    first = review_module.create_unreviewed_dataset(report_path)
    second = review_module.create_unreviewed_dataset(report_path)
    assert first == second
    assert first["candidate_source"]["sha256"] == hashlib.sha256(report_path.read_bytes()).hexdigest()
    assert [item["candidate_id"] for item in first["reviews"]] == ["candidate-a", "candidate-b"]
    assert all(item["review_state"] == "unreviewed" and item["classification"] is None for item in first["reviews"])
    assert [item["automated_status"] for item in first["reviews"]] == ["needs_review", "rejected"]


@pytest.mark.parametrize("classification", ["likely_gap", "legitimate_endpoint", "uncertain"])
def test_valid_review_classifications_require_decision_evidence_and_limitations(tmp_path, classification):
    report_path = _source_report(tmp_path)
    dataset = _reviewed(review_module.create_unreviewed_dataset(report_path), classification=classification)
    assert review_module.validate_review_dataset(dataset, report_path) is dataset


@pytest.mark.parametrize("classification", ["confirmed_gap", "repair_ready", "", 3])
def test_invalid_review_classifications_are_rejected(tmp_path, classification):
    report_path = _source_report(tmp_path)
    dataset = _reviewed(review_module.create_unreviewed_dataset(report_path), classification=classification)
    with pytest.raises(review_module.ReviewDatasetError, match="classification"):
        review_module.validate_review_dataset(dataset, report_path)


@pytest.mark.parametrize("field,value", [
    ("supporting_evidence", [{}]),
    ("supporting_evidence", [{"evidence_type": "imagery", "description": "Seen."}]),
    ("supporting_evidence", "imagery"),
    ("limitations", [""]),
    ("reviewer_decision", "  "),
])
def test_malformed_or_missing_review_evidence_is_rejected(tmp_path, field, value):
    report_path = _source_report(tmp_path)
    dataset = _reviewed(review_module.create_unreviewed_dataset(report_path))
    dataset["reviews"][0][field] = value
    with pytest.raises(review_module.ReviewDatasetError):
        review_module.validate_review_dataset(dataset, report_path)


def test_source_status_is_preserved_and_source_report_hash_mismatch_fails(tmp_path):
    report_path = _source_report(tmp_path)
    dataset = review_module.create_unreviewed_dataset(report_path)
    dataset["reviews"][0]["automated_status"] = "eligible"
    with pytest.raises(review_module.ReviewDatasetError, match="status changed"):
        review_module.validate_review_dataset(dataset, report_path)

    dataset = review_module.create_unreviewed_dataset(report_path)
    report_path.write_text(json.dumps({"candidates": [
        {"candidate_id": "candidate-a", "status": "rejected"},
        {"candidate_id": "candidate-b", "status": "rejected"},
    ]}), encoding="utf-8")
    with pytest.raises(review_module.ReviewDatasetError, match="SHA-256 mismatch"):
        review_module.validate_review_dataset(dataset, report_path)


def test_review_classification_does_not_create_repair_approval_or_mutate_graph(tmp_path):
    report_path = _source_report(tmp_path)
    dataset = _reviewed(review_module.create_unreviewed_dataset(report_path))
    review_module.validate_review_dataset(dataset, report_path)
    assert "approvals" not in dataset

    graph = nx.MultiDiGraph(crs="EPSG:4326")
    graph.add_nodes_from([(1, {"x": "80", "y": "7"}), (2, {"x": "80.001", "y": "7"})])
    graph.add_edge(1, 3)
    graph.add_edge(4, 2)
    candidate = {
        "candidate_id": "candidate-a", "endpoint_u": 1, "endpoint_v": 2,
        "distance_m": 10.0, "status": "eligible", "already_connected": False,
        "heading_compatible": True, "semantic_conflicts": [],
        "semantic_missing_attributes": [],
    }
    repaired, report = REPAIR.repair_candidates(graph, [candidate], approvals=[])
    assert report["counts"]["accepted_repair_count"] == 0
    assert not repaired.has_edge(1, 2)
    assert graph.number_of_edges() == 2
