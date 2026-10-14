import json
import threading
from http.client import HTTPConnection
from pathlib import Path

import networkx as nx

from src.road_network.c2_candidate_review import create_unreviewed_dataset
from src.road_network.c2_api import Component2Data, Component2RequestHandler, ThreadingHTTPServer


def _server(data: Component2Data):
    Component2RequestHandler.data = data
    server = ThreadingHTTPServer(("127.0.0.1", 0), Component2RequestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _get(server, path):
    connection = HTTPConnection("127.0.0.1", server.server_port)
    connection.request("GET", path)
    response = connection.getresponse()
    payload = json.loads(response.read())
    connection.close()
    return response.status, payload, response.getheader("Access-Control-Allow-Origin")


def _fixture(tmp_path: Path) -> Component2Data:
    graph = nx.MultiDiGraph(crs="EPSG:4326")
    graph.add_node("a", x="80.2", y="7.2")
    graph.add_node("b", x="80.3", y="7.3")
    graph.add_edge("a", "b", 0, length="10", highway="primary", oneway_normalized="forward",
                   direction_source="existing_extraction_representation")
    graph_path = tmp_path / "roads.graphml"
    nx.write_graphml(graph, graph_path)
    candidates_path = tmp_path / "candidates.geojson"
    candidates_path.write_text(json.dumps({"type": "FeatureCollection", "features": []}), encoding="utf-8")
    report = {"candidate_count": 0, "status_counts": {}, "candidates": []}
    candidate_report = tmp_path / "candidate.json"
    candidate_report.write_text(json.dumps(report), encoding="utf-8")
    review_dataset = tmp_path / "reviews.json"
    review_dataset.write_text(
        json.dumps(create_unreviewed_dataset(candidate_report)), encoding="utf-8"
    )
    stats = {"nodes": 2, "directed_edges": 1, "weak_components": 1, "strong_components": 2}
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"graph_statistics": stats}), encoding="utf-8")
    repair = tmp_path / "repair.json"
    repair.write_text(json.dumps({"before": {"graph_statistics": stats}, "accepted_repair_count": 0,
                                  "skipped_candidate_count": 0, "approval_count": 0}), encoding="utf-8")
    province_path = tmp_path / "province.geojson"
    province_path.write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[80, 7], [81, 7], [81, 8], [80, 8], [80, 7]]]}, "properties": {}}],
    }), encoding="utf-8")
    district_path = tmp_path / "districts.geojson"
    district_path.write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[80, 7], [80.5, 7], [80.5, 8], [80, 8], [80, 7]]]}, "properties": {"district": "Ratnapura"}},
            {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[80.5, 7], [80.8, 7], [80.8, 8], [80.5, 8], [80.5, 7]]]}, "properties": {"district": "Kegalle"}},
        ],
    }), encoding="utf-8")
    return Component2Data(
        graph_path, candidates_path, candidate_report, baseline, repair, province_path,
        district_path, review_dataset,
    )


def test_health_summary_candidates_and_cors(tmp_path):
    server, thread = _server(_fixture(tmp_path))
    try:
        assert _get(server, "/api/health")[1]["status"] == "ok"
        status, payload, cors = _get(server, "/api/summary")
        assert status == 200 and payload["graph"]["nodes"] == 2 and cors == "http://localhost:5173"
        assert _get(server, "/api/candidates")[1]["type"] == "FeatureCollection"
        status, reviews, _ = _get(server, "/api/reviews")
        assert status == 200
        assert reviews["schema_identifier"] == "c2-topology-candidate-reviews/v1"
        assert reviews["reviews"] == []
    finally:
        server.shutdown()
        thread.join()


def test_reviews_endpoint_rejects_candidate_report_hash_mismatch(tmp_path):
    data = _fixture(tmp_path)
    data.candidate_report_path.write_text(json.dumps({"candidates": [{
        "candidate_id": "changed", "status": "rejected",
    }]}), encoding="utf-8")
    server, thread = _server(data)
    try:
        status, payload, _ = _get(server, "/api/reviews")
        assert status == 409
        assert payload["code"] == "source_report_hash_mismatch"
        assert "SHA-256 mismatch" in payload["error"]
    finally:
        server.shutdown()
        thread.join()


def test_roads_valid_bbox_returns_real_segment(tmp_path):
    server, thread = _server(_fixture(tmp_path))
    try:
        status, payload, _ = _get(server, "/api/roads?bbox=80.1,7.1,80.4,7.4")
        assert status == 200
        assert payload["features"][0]["properties"]["source"] == "a"
        assert payload["features"][0]["geometry"]["coordinates"] == [[80.2, 7.2], [80.3, 7.3]]
    finally:
        server.shutdown()
        thread.join()


def test_roads_reject_invalid_and_broad_bbox(tmp_path):
    server, thread = _server(_fixture(tmp_path))
    try:
        assert _get(server, "/api/roads?bbox=80,7,79,8")[0] == 400
        assert _get(server, "/api/roads?bbox=0,0,2,2")[0] == 400
    finally:
        server.shutdown()
        thread.join()


def test_missing_candidate_file_is_explicit(tmp_path):
    data = _fixture(tmp_path)
    data.candidates_path = tmp_path / "missing.geojson"
    server, thread = _server(data)
    try:
        status, payload, _ = _get(server, "/api/candidates")
        assert status == 404 and "missing" in payload["error"]
    finally:
        server.shutdown()
        thread.join()


def test_reviews_endpoint_reports_missing_artifact_with_action(tmp_path):
    data = _fixture(tmp_path)
    data.review_dataset_path = tmp_path / "missing-reviews.json"
    server, thread = _server(data)
    try:
        status, payload, _ = _get(server, "/api/reviews")
        assert status == 404
        assert payload["code"] == "review_artifact_missing"
        assert "--init" in payload["error"]
    finally:
        server.shutdown()
        thread.join()


def test_reviews_endpoint_reports_malformed_artifact_as_validation_error(tmp_path):
    data = _fixture(tmp_path)
    data.review_dataset_path.write_text("{invalid json", encoding="utf-8")
    server, thread = _server(data)
    try:
        status, payload, _ = _get(server, "/api/reviews")
        assert status == 422
        assert payload["code"] == "malformed_review_artifact"
        assert "malformed" in payload["error"]
    finally:
        server.shutdown()
        thread.join()


def test_unknown_endpoint_is_distinct_from_review_errors(tmp_path):
    server, thread = _server(_fixture(tmp_path))
    try:
        status, payload, _ = _get(server, "/api/not-a-route")
        assert status == 404
        assert payload["code"] == "endpoint_not_found"
        assert payload["error"] == "Endpoint not found"
    finally:
        server.shutdown()
        thread.join()


def test_district_filtering_keeps_only_selected_roads_and_candidates(tmp_path):
    data = _fixture(tmp_path)
    graph = nx.read_graphml(data.graph_path, force_multigraph=True)
    graph.add_node("c", x="80.6", y="7.1")
    graph.add_node("d", x="80.7", y="7.2")
    graph.add_node("e", x="80.9", y="7.9")
    graph.add_node("f", x="80.95", y="7.95")
    graph.add_edge("c", "d", 0, length="20")
    graph.add_edge("e", "f", 0, length="30")
    nx.write_graphml(graph, data.graph_path)
    data.candidates_path.write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[80.2, 7.2], [80.3, 7.3]]}, "properties": {"candidate_id": "rat"}},
            {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[80.6, 7.1], [80.7, 7.2]]}, "properties": {"candidate_id": "keg"}},
            {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[80.9, 7.9], [80.95, 7.95]]}, "properties": {"candidate_id": "outside"}},
        ],
    }), encoding="utf-8")
    server, thread = _server(data)
    try:
        status, rat_roads, _ = _get(server, "/api/roads?districts=Ratnapura&bbox=80,7,81,8")
        assert status == 200
        assert {(f["properties"]["source"], f["properties"]["target"]) for f in rat_roads["features"]} == {("a", "b")}
        status, rat_candidates, _ = _get(server, "/api/candidates?districts=Ratnapura")
        assert status == 200 and [f["properties"]["candidate_id"] for f in rat_candidates["features"]] == ["rat"]
        status, both_roads, _ = _get(server, "/api/roads?districts=Ratnapura,Kegalle&bbox=80,7,81,8")
        assert status == 200 and len(both_roads["features"]) == 2
        status, both_candidates, _ = _get(server, "/api/candidates?districts=Ratnapura,Kegalle")
        assert status == 200
        assert [feature["properties"]["candidate_id"] for feature in both_candidates["features"]] == ["rat", "keg"]
        status, no_district_roads, _ = _get(server, "/api/roads?districts=&bbox=80,7,81,8")
        assert status == 200 and no_district_roads["features"] == []
        status, no_district_candidates, _ = _get(server, "/api/candidates?districts=")
        assert status == 200 and no_district_candidates["features"] == []
    finally:
        server.shutdown()
        thread.join()


def test_invalid_or_missing_district_data_is_explicit(tmp_path):
    data = _fixture(tmp_path)
    data.district_boundaries_path = tmp_path / "missing-districts.geojson"
    server, thread = _server(data)
    try:
        assert _get(server, "/api/roads?districts=Ratnapura&bbox=80,7,81,8")[0] == 404
        assert _get(server, "/api/candidates?districts=Unknown")[0] == 400
    finally:
        server.shutdown()
        thread.join()
