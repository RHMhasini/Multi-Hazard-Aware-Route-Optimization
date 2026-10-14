"""Audit tests use temporary test fixtures only; no fixture is project data."""

import hashlib
import json
import shutil
import threading
from pathlib import Path

import networkx as nx

from src.road_network.c2_api import Component2Data, Component2RequestHandler, ThreadingHTTPServer
from src.road_network.c2_candidate_review import create_unreviewed_dataset
from src.road_network.c2_project_audit import ARTIFACTS, GRAPH_FILES, ProjectAuditService, _graph_metrics, _validate_candidate_geojson


def _write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _project_fixture(root: Path) -> ProjectAuditService:
    graph = nx.MultiDiGraph(crs="EPSG:4326")
    graph.add_node("a", x="80.2", y="7.2")
    graph.add_node("b", x="80.3", y="7.3")
    graph.add_edge("a", "b", "edge-key-0", highway="primary", length="10", oneway="False", oneway_osm="no")
    raw_path = root / GRAPH_FILES["raw"]
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    nx.write_graphml(graph, raw_path)
    cleaned_path = root / GRAPH_FILES["cleaned"]
    cleaned_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(raw_path, cleaned_path)
    normalized = graph.copy()
    for _, _, _, attrs in normalized.edges(keys=True, data=True):
        attrs["oneway_normalized"] = "forward"
        attrs["direction_source"] = "existing_extraction_representation"
    nx.write_graphml(normalized, root / GRAPH_FILES["direction_normalized"])
    repaired_path = root / GRAPH_FILES["topology_repaired"]
    repaired_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(root / GRAPH_FILES["direction_normalized"], repaired_path)

    _write_json(root / ARTIFACTS["osm_metadata"], {
        "status": "success", "source_file": "cache/source-test.pbf", "extraction_datetime": "2026-01-01T00:00:00Z",
        "extraction_total_seconds": 1.2, "validation_issues": [], "node_count": 2, "edge_count": 1,
    })
    _write_json(root / ARTIFACTS["baseline_report"], {"inspection": {"generated_at": "2026-01-01T00:00:00Z"}})
    _write_json(root / ARTIFACTS["cleaning_report"], {
        "inspection": {"generated_at": "2026-01-01T00:01:00Z"}, "records": {"nodes_inspected": 2, "edges_inspected": 1},
        "structure": {}, "decisions": {},
    })
    _write_json(root / ARTIFACTS["direction_report"], {
        "preservation": {"same_node_count": True, "same_edge_count": True, "same_edge_keys": True,
                         "graph_type_preserved": True, "directed_preserved": True, "multigraph_preserved": True},
        "serialization_preservation": {}, "direction": {"direction_semantics_independently_verified": False, "verification_limitation": "Test limitation."},
    })
    evidence = {"ambiguous": False, "missing": False, "values": ["0"]}
    candidate = {"candidate_id": "test-candidate", "status": "rejected", "reasons": ["layer_conflict"],
                 "endpoint_semantics": {side: {"layer": evidence} for side in ("u", "v")}}
    report = {
        "input_graph": {"path": str((root / GRAPH_FILES["direction_normalized"]).resolve()), "node_count": 2, "edge_count": 1}, "counts": {"candidate_pair_count": 1, "eligible_count": 0, "rejected_count": 1, "needs_review_count": 0},
        "candidates": [candidate], "reason_counts": {"layer_conflict": 1}, "semantic_missing_counts": {},
    }
    candidate_path = root / ARTIFACTS["candidate_report"]
    _write_json(candidate_path, report)
    geojson_path = root / ARTIFACTS["candidate_geojson"]
    _write_json(geojson_path, {"type": "FeatureCollection", "crs": {"type": "name", "properties": {"name": "EPSG:4326"}}, "features": [
        {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[80.2, 7.2], [80.3, 7.3]]},
         "properties": {"candidate_id": "test-candidate", "status": "rejected", "feature_type": "candidate_connection", "endpoint_u": "a", "endpoint_v": "b", "distance_m": 10, "heading_deviation_degrees": 0, "reasons": [], "semantic_conflicts": [], "semantic_missing_attributes": []}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [80.2, 7.2]},
         "properties": {"candidate_id": "test-candidate", "status": "rejected", "feature_type": "endpoint_u", "endpoint": "a", "reasons": []}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [80.3, 7.3]},
         "properties": {"candidate_id": "test-candidate", "status": "rejected", "feature_type": "endpoint_v", "endpoint": "b", "reasons": []}},
    ]})
    _write_json(root / ARTIFACTS["repair_report"], {
        "counts": {"candidate_count": 1, "accepted_repair_count": 0, "real_data_repair_count": 0},
        "before": {"node_count": 2, "edge_count": 1}, "after": {"node_count": 2, "edge_count": 1},
        "accepted_repairs": [], "provenance": {"candidate_report_sha256": hashlib.sha256(candidate_path.read_bytes()).hexdigest()},
        "real_data_repair_occurred": False,
    })
    _write_json(root / ARTIFACTS["review_dataset"], create_unreviewed_dataset(candidate_path))
    return ProjectAuditService(root)


def test_pipeline_status_and_comparison_use_validated_fixture_artifacts(tmp_path):
    service = _project_fixture(tmp_path)
    snapshot = service.snapshot()
    stages = {stage["stage_id"]: stage for stage in snapshot["pipeline"]}
    assert set(stages) == {"extraction", "cleaning_validation", "direction_normalization", "candidate_detection", "candidate_review", "repair", "evaluation", "export"}
    assert stages["extraction"]["status"] == "completed"
    assert stages["extraction"]["runtime_seconds"] == 1.2
    assert stages["extraction"]["runtime_status"] == "recorded"
    assert stages["extraction"]["runtime_source"] == {
        "artifact_path": ARTIFACTS["osm_metadata"], "field": "extraction_total_seconds"
    }
    assert stages["extraction"]["runtime_evidence_kind"] == "historical_metadata"
    assert all(stages[name]["runtime_seconds"] is None and stages[name]["runtime_status"] == "not_recorded" for name in stages if name != "extraction")
    assert stages["candidate_review"]["status"] == "pending"
    assert "No real repairs accepted" in stages["repair"]["explanation"]
    assert stages["direction_normalization"]["status"] == "partial"
    comparison = snapshot["graph_comparison"]
    assert comparison["graphs"]["raw"]["metrics"] == {
        "node_count": 2, "directed_edge_count": 1, "weakly_connected_component_count": 1,
        "degree_1_total_directed_degree_count": 2,
    }
    identity = {(item["left"], item["right"]): item["byte_identical"] for item in comparison["identity_comparisons"]}
    assert identity[("raw", "cleaned")] is True
    assert identity[("direction_normalized", "topology_repaired")] is True
    assert comparison["degree_1_definition"].startswith("Count of nodes whose directed total degree")


def test_graph_metrics_preserve_parallel_directed_edges_and_keys():
    graph = nx.MultiDiGraph(crs="EPSG:4326")
    graph.add_nodes_from((node, {"x": 80.0 + i, "y": 7.0}) for i, node in enumerate(("a", "b", "c")))
    graph.add_edge("a", "b", key="parallel-1", highway="primary", length="1")
    graph.add_edge("a", "b", key="parallel-2", highway="primary", length="1")
    graph.add_edge("b", "a", key="reverse", highway="primary", length="1")
    graph.add_edge("b", "c", key="tail", highway="primary", length="1")
    metrics, validation = _graph_metrics(graph)
    assert graph.number_of_edges("a", "b") == 2
    assert metrics["directed_edge_count"] == 4
    assert metrics["weakly_connected_component_count"] == 1
    assert metrics["degree_1_total_directed_degree_count"] == 1
    assert validation["validation"]["edge_record_count_matches_multigraph_count"] is True


def test_missing_and_malformed_artifacts_are_not_reported_as_completed(tmp_path):
    service = _project_fixture(tmp_path)
    (tmp_path / GRAPH_FILES["raw"]).unlink()
    snapshot = service.snapshot()
    stages = {stage["stage_id"]: stage for stage in snapshot["pipeline"]}
    assert stages["extraction"]["status"] == "unavailable"
    assert GRAPH_FILES["raw"] in stages["extraction"]["missing_prerequisites"]

    _project_fixture(tmp_path)
    (tmp_path / ARTIFACTS["candidate_report"]).write_text("{bad", encoding="utf-8")
    snapshot = service.snapshot()
    stages = {stage["stage_id"]: stage for stage in snapshot["pipeline"]}
    assert stages["candidate_detection"]["status"] == "failed"
    assert snapshot["semantic_audit"]["status"] == "unavailable"

    malformed_structure = {
        "counts": None,
        "input_graph": None,
        "candidates": [{"candidate_id": "test-only-malformed", "status": "eligible", "endpoint_semantics": None, "reasons": None}],
    }
    _write_json(tmp_path / ARTIFACTS["candidate_report"], malformed_structure)
    snapshot = service.snapshot()
    stages = {stage["stage_id"]: stage for stage in snapshot["pipeline"]}
    assert stages["candidate_detection"]["status"] == "failed"
    assert snapshot["semantic_audit"]["status"] == "partial"


def test_unrecorded_stage_runtime_is_explicitly_unavailable(tmp_path):
    service = _project_fixture(tmp_path)
    metadata_path = tmp_path / ARTIFACTS["osm_metadata"]
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata.pop("extraction_total_seconds")
    _write_json(metadata_path, metadata)
    stages = {stage["stage_id"]: stage for stage in service.snapshot()["pipeline"]}
    extraction = stages["extraction"]
    assert extraction["runtime_seconds"] is None
    assert extraction["runtime_status"] == "not_recorded"
    assert extraction["runtime_source"] is None
    assert "No trustworthy measured duration" in extraction["runtime_explanation"]


def test_semantic_audit_reports_actual_counts_and_incomplete_guard_outcomes(tmp_path):
    service = _project_fixture(tmp_path)
    snapshot = service.snapshot()
    audit = snapshot["semantic_audit"]
    assert audit["candidate_outcome_counts"] == {"rejected": 1, "needs_review": 0, "eligible": 0}
    assert audit["approved_repair_count"] == 0
    assert audit["guards"]["layer"]["guard_implemented_in_candidate_screening"] is True
    assert audit["guards"]["layer"]["explicitly_recorded_guard_reason_counts"] == {"layer_conflict": 1}
    assert audit["guards"]["highway"]["per_candidate_outcome_reconstructable"] is False
    assert audit["recomputed_candidate_counts_match_report"] is True


def test_repaired_links_empty_and_valid_provenance_cases(tmp_path):
    service = _project_fixture(tmp_path)
    snapshot = service.snapshot()
    empty = snapshot["repaired_links"]
    assert empty["status"] == "empty"
    assert empty["collection"]["features"] == []
    assert "no accepted real-data repair joins to assess" in empty["reason"].lower()

    candidate_hash = "a" * 64
    manifest_path = tmp_path / "approvals.json"
    attrs = {"layer": "0", "bridge": "no", "tunnel": "no", "oneway_normalized": "forward", "direction_source": "component2_explicit_approval"}
    _write_json(manifest_path, {"approvals": [{"candidate_id": "candidate-a", "approved": True, "direction": "u_to_v", "edge_attributes": attrs, "reason": "review evidence"}]})
    repair_id = "repair-" + hashlib.sha256(b"candidate-a:u_to_v").hexdigest()[:16]
    graph_attrs = attrs | {"c2_repair_id": repair_id}
    graph_edge = {"repair_id": repair_id, "candidate_id": "candidate-a", "source": "a", "target": "b", "edge_key": "0", "attributes": graph_attrs, "source_coordinates": [80.2, 7.2], "target_coordinates": [80.3, 7.3]}
    repair_record = {"repair_id": repair_id, "candidate_id": "candidate-a", "status": "accepted", "action": "add_approved_directed_connector", "reason": "review evidence", "source": "a", "target": "b", "edge_key": 0, "direction": "u_to_v", "candidate_report_sha256": candidate_hash, "candidate_evidence": {"candidate_id": "candidate-a", "status": "eligible", "already_connected": False, "heading_compatible": True, "semantic_conflicts": [], "semantic_missing_attributes": []}, "edge_attributes": attrs}
    report = {"counts": {"accepted_repair_count": 1, "real_data_repair_count": 1}, "accepted_repairs": [repair_record], "real_data_repair_occurred": True, "approval_manifest_path": "approvals.json", "provenance": {"candidate_report_sha256": candidate_hash}}
    graphs = {"topology_repaired": {"repair_edges": [graph_edge], "sha256": "b" * 64}}
    result = service._repaired_links(report, None, "c" * 64, candidate_hash, graphs)
    assert result["status"] == "available"
    assert result["collection"]["features"][0]["id"] == repair_id
    assert result["collection"]["features"][0]["properties"]["geometry_kind"] == "derived_straight_line_between_graph_endpoints"


def test_export_allowlist_rejects_arbitrary_paths(tmp_path):
    service = _project_fixture(tmp_path)
    assert service.export_path("../../outputs/component2_evaluation/c2_raw_network_baseline.json") is None
    assert service.export_path("raw") is not None
    candidate = next(item for item in service.snapshot()["exports"]["items"] if item["artifact_id"] == "candidate_geojson")
    assert candidate["available"] is True
    assert candidate["provenance"]["status"] == "partial"
    assert candidate["provenance"]["candidate_source_graph_sha256_recorded_in_report"] is False


def test_candidate_geojson_validation_rejects_unmatched_ids_and_invalid_coordinates(tmp_path):
    service = _project_fixture(tmp_path)
    path = tmp_path / ARTIFACTS["candidate_geojson"]
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["features"][0]["properties"]["candidate_id"] = "not-in-report"
    payload["features"][0]["geometry"]["coordinates"][0][0] = 999
    _write_json(path, payload)
    report = json.loads((tmp_path / ARTIFACTS["candidate_report"]).read_text(encoding="utf-8"))
    assert _validate_candidate_geojson(path, report)["status"] == "failed"


def test_audit_endpoints_and_existing_review_contract(tmp_path):
    service = _project_fixture(tmp_path)
    data = Component2Data(
        graph_path=tmp_path / GRAPH_FILES["direction_normalized"],
        candidates_path=tmp_path / ARTIFACTS["candidate_geojson"],
        candidate_report_path=tmp_path / ARTIFACTS["candidate_report"],
        repair_report_path=tmp_path / ARTIFACTS["repair_report"],
        review_dataset_path=tmp_path / ARTIFACTS["review_dataset"],
    )
    data._project_audit = service
    Component2RequestHandler.data = data
    server = ThreadingHTTPServer(("127.0.0.1", 0), Component2RequestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        from http.client import HTTPConnection

        for route in ("/api/pipeline-status", "/api/graph-comparison", "/api/semantic-audit", "/api/repaired-links", "/api/exports"):
            connection = HTTPConnection("127.0.0.1", server.server_port)
            connection.request("GET", route)
            response = connection.getresponse()
            payload = json.loads(response.read())
            connection.close()
            assert response.status == 200
            assert isinstance(payload, dict)
        connection = HTTPConnection("127.0.0.1", server.server_port)
        connection.request("GET", "/api/exports/../../secret")
        response = connection.getresponse()
        assert response.status == 404
        response.read()
        connection.close()

        connection = HTTPConnection("127.0.0.1", server.server_port)
        connection.request("GET", "/api/exports/candidate_geojson")
        response = connection.getresponse()
        exported = response.read()
        assert response.status == 200
        assert response.getheader("Content-Type") == "application/geo+json"
        assert exported == (tmp_path / ARTIFACTS["candidate_geojson"]).read_bytes()
        connection.close()

        connection = HTTPConnection("127.0.0.1", server.server_port)
        connection.request("GET", "/api/reviews")
        response = connection.getresponse()
        review = json.loads(response.read())
        assert response.status == 200
        assert review["schema_identifier"] == "c2-topology-candidate-reviews/v1"
        connection.close()
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
