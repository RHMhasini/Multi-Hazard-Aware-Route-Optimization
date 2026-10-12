import json
import threading
from http.client import HTTPConnection
from pathlib import Path

import networkx as nx

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
    report = {"candidate_count": 0, "status_counts": {}}
    candidate_report = tmp_path / "candidate.json"
    candidate_report.write_text(json.dumps(report), encoding="utf-8")
    stats = {"nodes": 2, "directed_edges": 1, "weak_components": 1, "strong_components": 2}
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"graph_statistics": stats}), encoding="utf-8")
    repair = tmp_path / "repair.json"
    repair.write_text(json.dumps({"before": {"graph_statistics": stats}, "accepted_repair_count": 0,
                                  "skipped_candidate_count": 0, "approval_count": 0}), encoding="utf-8")
    return Component2Data(graph_path, candidates_path, candidate_report, baseline, repair)


def test_health_summary_candidates_and_cors(tmp_path):
    server, thread = _server(_fixture(tmp_path))
    try:
        assert _get(server, "/api/health")[1]["status"] == "ok"
        status, payload, cors = _get(server, "/api/summary")
        assert status == 200 and payload["graph"]["nodes"] == 2 and cors == "http://localhost:5173"
        assert _get(server, "/api/candidates")[1]["type"] == "FeatureCollection"
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
