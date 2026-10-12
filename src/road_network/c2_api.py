"""Read-only HTTP API for the Component 2 research dashboard."""

from __future__ import annotations

import json
import math
import threading
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import networkx as nx

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GRAPH = ROOT / "data" / "intermediate" / "c2_sabaragamuwa_direction_normalized_graph.graphml"
DEFAULT_CANDIDATES = ROOT / "data" / "intermediate" / "c2_topology_candidates.geojson"
DEFAULT_CANDIDATE_REPORT = ROOT / "outputs" / "component2_evaluation" / "c2_topology_candidates.json"
DEFAULT_BASELINE = ROOT / "outputs" / "component2_evaluation" / "c2_raw_network_baseline.json"
DEFAULT_REPAIR_REPORT = ROOT / "outputs" / "component2_evaluation" / "c2_topology_repair_report.json"
MAX_FEATURES = 5000
MAX_BBOX_AREA = 1.0


class ApiError(Exception):
    """An expected client or data error with an HTTP status."""

    def __init__(self, message: str, status: HTTPStatus = HTTPStatus.BAD_REQUEST) -> None:
        super().__init__(message)
        self.status = status


def _read_json(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as handle:
            value = json.load(handle)
    except FileNotFoundError as exc:
        raise ApiError(f"Required data file is missing: {path}", HTTPStatus.NOT_FOUND) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ApiError(f"Could not read data file: {path}", HTTPStatus.INTERNAL_SERVER_ERROR) from exc
    if not isinstance(value, dict):
        raise ApiError(f"Expected an object in data file: {path}", HTTPStatus.INTERNAL_SERVER_ERROR)
    return value


def _number(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ApiError("Graph contains a non-numeric coordinate", HTTPStatus.INTERNAL_SERVER_ERROR) from exc
    if not math.isfinite(result):
        raise ApiError("Graph contains a non-finite coordinate", HTTPStatus.INTERNAL_SERVER_ERROR)
    return result


def _json_value(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return str(value)


def _candidate_status_counts(report: dict[str, Any]) -> dict[str, int]:
    counts = report.get("status_counts")
    if isinstance(counts, dict):
        return {str(key): int(value) for key, value in counts.items()}
    candidates = report.get("candidates", [])
    result: dict[str, int] = {}
    if isinstance(candidates, list):
        for candidate in candidates:
            if isinstance(candidate, dict):
                status = str(candidate.get("status", "unknown"))
                result[status] = result.get(status, 0) + 1
    return result


def _graph_counts(graph: nx.MultiDiGraph) -> dict[str, int]:
    return {
        "nodes": graph.number_of_nodes(),
        "directed_edges": graph.number_of_edges(),
        "weak_components": nx.number_weakly_connected_components(graph),
        "strong_components": nx.number_strongly_connected_components(graph),
    }


@dataclass
class Component2Data:
    graph_path: Path = DEFAULT_GRAPH
    candidates_path: Path = DEFAULT_CANDIDATES
    candidate_report_path: Path = DEFAULT_CANDIDATE_REPORT
    baseline_path: Path = DEFAULT_BASELINE
    repair_report_path: Path = DEFAULT_REPAIR_REPORT

    def __post_init__(self) -> None:
        self._graph: nx.MultiDiGraph | None = None
        self._lock = threading.Lock()

    def graph(self) -> nx.MultiDiGraph:
        with self._lock:
            if self._graph is None:
                if not self.graph_path.exists():
                    raise ApiError(f"Required graph file is missing: {self.graph_path}", HTTPStatus.NOT_FOUND)
                loaded = nx.read_graphml(self.graph_path, force_multigraph=True)
                if not isinstance(loaded, nx.MultiDiGraph):
                    raise ApiError("Direction-normalized graph is not a MultiDiGraph", HTTPStatus.INTERNAL_SERVER_ERROR)
                self._graph = loaded
            return self._graph

    def candidates(self) -> dict[str, Any]:
        try:
            with self.candidates_path.open(encoding="utf-8") as handle:
                value = json.load(handle)
        except FileNotFoundError as exc:
            raise ApiError(f"Candidate GeoJSON is missing: {self.candidates_path}", HTTPStatus.NOT_FOUND) from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise ApiError("Could not read candidate GeoJSON", HTTPStatus.INTERNAL_SERVER_ERROR) from exc
        if not isinstance(value, dict) or value.get("type") != "FeatureCollection":
            raise ApiError("Candidate data is not a GeoJSON FeatureCollection", HTTPStatus.INTERNAL_SERVER_ERROR)
        return value

    def summary(self) -> dict[str, Any]:
        baseline = _read_json(self.baseline_path)
        candidates = _read_json(self.candidate_report_path)
        repair = _read_json(self.repair_report_path)
        graph_stats = repair.get("before", {}).get("graph_statistics")
        if not isinstance(graph_stats, dict):
            graph_stats = baseline.get("graph_statistics")
        if not isinstance(graph_stats, dict):
            graph_stats = _graph_counts(self.graph())
        return {
            "graph": graph_stats,
            "candidate_status_counts": _candidate_status_counts(candidates),
            "candidate_count": candidates.get("candidate_count", len(candidates.get("candidates", []))),
            "repairs": {
                "accepted": repair.get("accepted_repair_count", 0),
                "skipped": repair.get("skipped_candidate_count", 0),
                "approval_count": repair.get("approval_count", 0),
            },
            "study_area": "Sabaragamuwa Province, Sri Lanka",
        }

    def roads(self, bbox: tuple[float, float, float, float]) -> dict[str, Any]:
        min_lon, min_lat, max_lon, max_lat = bbox
        if (max_lon - min_lon) * (max_lat - min_lat) > MAX_BBOX_AREA:
            raise ApiError("Requested bbox is too broad; zoom in to request a bounded road view")
        features: list[dict[str, Any]] = []
        graph = self.graph()
        for source, target, key, attrs in graph.edges(keys=True, data=True):
            if len(features) >= MAX_FEATURES:
                raise ApiError("Requested bbox contains too many road segments; zoom in further")
            source_attrs = graph.nodes[source]
            target_attrs = graph.nodes[target]
            source_point = (_number(source_attrs.get("x")), _number(source_attrs.get("y")))
            target_point = (_number(target_attrs.get("x")), _number(target_attrs.get("y")))
            if not (
                min_lon <= source_point[0] <= max_lon
                and min_lat <= source_point[1] <= max_lat
                and min_lon <= target_point[0] <= max_lon
                and min_lat <= target_point[1] <= max_lat
            ):
                continue
            properties = {
                "source": str(source),
                "target": str(target),
                "edge_key": str(key),
                "length": _json_value(attrs.get("length")),
                "highway": _json_value(attrs.get("highway")),
                "oneway_normalized": _json_value(attrs.get("oneway_normalized")),
                "direction_source": _json_value(attrs.get("direction_source")),
            }
            features.append({
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": [source_point, target_point]},
                "properties": properties,
            })
        return {"type": "FeatureCollection", "features": features}


class Component2RequestHandler(BaseHTTPRequestHandler):
    data = Component2Data()

    def _send(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "http://localhost:5173")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self._send({})

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/health":
                self._send({"status": "ok", "service": "component2-api"})
            elif parsed.path == "/api/summary":
                self._send(self.data.summary())
            elif parsed.path == "/api/candidates":
                self._send(self.data.candidates())
            elif parsed.path == "/api/roads":
                self._send(self.data.roads(self._parse_bbox(parse_qs(parsed.query))))
            else:
                raise ApiError("Endpoint not found", HTTPStatus.NOT_FOUND)
        except ApiError as exc:
            self._send({"error": str(exc)}, exc.status)

    @staticmethod
    def _parse_bbox(query: dict[str, list[str]]) -> tuple[float, float, float, float]:
        values = query.get("bbox", [])
        if len(values) != 1:
            raise ApiError("bbox must be minLon,minLat,maxLon,maxLat")
        parts = values[0].split(",")
        if len(parts) != 4:
            raise ApiError("bbox must contain exactly four coordinates")
        try:
            bbox = tuple(float(part) for part in parts)
        except ValueError as exc:
            raise ApiError("bbox coordinates must be numbers") from exc
        if any(not math.isfinite(value) for value in bbox):
            raise ApiError("bbox coordinates must be finite")
        min_lon, min_lat, max_lon, max_lat = bbox
        if not (-180 <= min_lon < max_lon <= 180 and -90 <= min_lat < max_lat <= 90):
            raise ApiError("bbox coordinates are out of range or incorrectly ordered")
        return bbox  # type: ignore[return-value]

    def log_message(self, format: str, *args: Any) -> None:
        return


def create_server(host: str = "127.0.0.1", port: int = 8000) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), Component2RequestHandler)


def main() -> None:
    server = create_server()
    print("Component 2 API listening on http://127.0.0.1:8000")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
