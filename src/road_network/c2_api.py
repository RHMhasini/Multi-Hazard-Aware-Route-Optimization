"""Read-only HTTP API for the Component 2 research dashboard."""

from __future__ import annotations

import json
import math
import threading
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import networkx as nx
from shapely.geometry import LineString, Point, box, shape
from shapely.errors import ShapelyError
from shapely.prepared import prep
from shapely.ops import unary_union
from shapely.strtree import STRtree

from src.road_network.c2_candidate_review import (
    DEFAULT_REVIEW_DATASET,
    ReviewDatasetError,
    load_review_dataset,
)
from src.road_network.c2_project_audit import ProjectAuditService
from src.road_network.c2_project_audit import GRAPH_FILES as PROJECT_GRAPH_FILES

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GRAPH = ROOT / "data" / "intermediate" / "c2_sabaragamuwa_direction_normalized_graph.graphml"
DEFAULT_CANDIDATES = ROOT / "data" / "intermediate" / "c2_topology_candidates.geojson"
DEFAULT_CANDIDATE_REPORT = ROOT / "outputs" / "component2_evaluation" / "c2_topology_candidates.json"
DEFAULT_BASELINE = ROOT / "outputs" / "component2_evaluation" / "c2_raw_network_baseline.json"
DEFAULT_REPAIR_REPORT = ROOT / "outputs" / "component2_evaluation" / "c2_topology_repair_report.json"
DEFAULT_PROVINCE_BOUNDARY = ROOT / "data" / "raw" / "c2_sabaragamuwa_boundary.geojson"
DEFAULT_DISTRICT_BOUNDARIES = ROOT / "data" / "raw" / "c2_sabaragamuwa_district_boundaries.geojson"
MAX_FEATURES = 5000
MAX_BBOX_AREA = 1.0
DISTRICT_NAMES = frozenset({"Ratnapura", "Kegalle"})


class ApiError(Exception):
    """An expected client or data error with an HTTP status."""

    def __init__(
        self,
        message: str,
        status: HTTPStatus = HTTPStatus.BAD_REQUEST,
        code: str = "bad_request",
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


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
    province_boundary_path: Path = DEFAULT_PROVINCE_BOUNDARY
    district_boundaries_path: Path = DEFAULT_DISTRICT_BOUNDARIES
    review_dataset_path: Path = DEFAULT_REVIEW_DATASET

    def __post_init__(self) -> None:
        self._graph: nx.MultiDiGraph | None = None
        self._graph_stat: tuple[int, int] | None = None
        self._lock = threading.Lock()
        self._metrics_lock = threading.Lock()
        self._metrics_cache: dict[tuple[str, ...], dict[str, Any]] = {}
        self._province_geometry = None
        self._district_features: dict[str, dict[str, Any]] | None = None
        self._road_tree: STRtree | None = None
        self._road_geometries: list[LineString] = []
        self._road_records: list[tuple[Any, Any, Any, dict[str, Any]]] = []
        self._project_audit = ProjectAuditService(ROOT)

    def project_audit(self) -> dict[str, Any]:
        with self._lock:
            try:
                stat = self.graph_path.stat()
                current_stat = (stat.st_size, stat.st_mtime_ns)
            except OSError:
                current_stat = None
            preloaded = self._graph if (self.graph_path.resolve() == (ROOT / PROJECT_GRAPH_FILES["direction_normalized"]).resolve() and current_stat == self._graph_stat) else None
        return self._project_audit.snapshot(preloaded_graph=preloaded)

    def graph(self) -> nx.MultiDiGraph:
        with self._lock:
            if self._graph is None:
                if not self.graph_path.exists():
                    raise ApiError(f"Required graph file is missing: {self.graph_path}", HTTPStatus.NOT_FOUND)
                try:
                    loaded = nx.read_graphml(self.graph_path, force_multigraph=True)
                except (OSError, nx.NetworkXError, ET.ParseError, ValueError) as exc:
                    raise ApiError(
                        f"Required graph file is unavailable or invalid: {self.graph_path}",
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        "graph_unavailable",
                    ) from exc
                if not isinstance(loaded, nx.MultiDiGraph):
                    raise ApiError("Direction-normalized graph is not a MultiDiGraph", HTTPStatus.INTERNAL_SERVER_ERROR)
                self._graph = loaded
                stat = self.graph_path.stat()
                self._graph_stat = (stat.st_size, stat.st_mtime_ns)
            return self._graph

    def _boundaries(self) -> tuple[Any, dict[str, dict[str, Any]]]:
        with self._lock:
            if self._district_features is not None and self._province_geometry is not None:
                return self._province_geometry, self._district_features
            try:
                with self.province_boundary_path.open(encoding="utf-8") as handle:
                    province = json.load(handle)
                with self.district_boundaries_path.open(encoding="utf-8") as handle:
                    districts = json.load(handle)
            except FileNotFoundError as exc:
                raise ApiError(f"Required boundary file is missing: {exc.filename}", HTTPStatus.NOT_FOUND) from exc
            except (OSError, json.JSONDecodeError) as exc:
                raise ApiError("Could not read district or province boundary data", HTTPStatus.INTERNAL_SERVER_ERROR) from exc
            if province.get("type") != "FeatureCollection" or len(province.get("features", [])) != 1:
                raise ApiError("Province boundary data is invalid", HTTPStatus.INTERNAL_SERVER_ERROR)
            if districts.get("type") != "FeatureCollection":
                raise ApiError("District boundary data is invalid", HTTPStatus.INTERNAL_SERVER_ERROR)
            try:
                province_geometry = shape(province["features"][0]["geometry"])
            except (KeyError, TypeError, ValueError, ShapelyError) as exc:
                raise ApiError("Province boundary geometry is invalid", HTTPStatus.INTERNAL_SERVER_ERROR) from exc
            if province_geometry.is_empty or not province_geometry.is_valid or province_geometry.geom_type not in {"Polygon", "MultiPolygon"}:
                raise ApiError("Province boundary geometry is invalid", HTTPStatus.INTERNAL_SERVER_ERROR)
            district_features: dict[str, dict[str, Any]] = {}
            for feature in districts.get("features", []):
                name = feature.get("properties", {}).get("district")
                if name in DISTRICT_NAMES:
                    try:
                        district_geometry = shape(feature["geometry"])
                    except (KeyError, TypeError, ValueError, ShapelyError) as exc:
                        raise ApiError(f"{name} district boundary geometry is invalid", HTTPStatus.INTERNAL_SERVER_ERROR) from exc
                    if district_geometry.is_empty or not district_geometry.is_valid or district_geometry.geom_type not in {"Polygon", "MultiPolygon"}:
                        raise ApiError(f"{name} district boundary geometry is invalid", HTTPStatus.INTERNAL_SERVER_ERROR)
                    district_features[name] = feature
            if set(district_features) != DISTRICT_NAMES:
                raise ApiError("District boundary data must cover all Sabaragamuwa Province districts", HTTPStatus.INTERNAL_SERVER_ERROR)
            self._province_geometry = province_geometry
            self._district_features = district_features
            return province_geometry, district_features

    @staticmethod
    def _selected_names(query: dict[str, list[str]]) -> tuple[str, ...] | None:
        values = query.get("districts", [])
        if not values:
            return None
        if len(values) != 1:
            raise ApiError("districts must be a comma-separated list")
        if not values[0].strip():
            return tuple()
        names = tuple(dict.fromkeys(name.strip() for name in values[0].split(",") if name.strip()))
        if not names or any(name not in DISTRICT_NAMES for name in names):
            raise ApiError("districts must contain only valid Sabaragamuwa Province districts")
        return names

    def boundaries(self) -> dict[str, Any]:
        province, districts = self._boundaries()
        return {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "geometry": json.loads(json.dumps(province.__geo_interface__)),
                 "properties": {"boundary_type": "province", "name": "Sabaragamuwa"}},
                *[districts[name] for name in sorted(districts)],
            ],
        }

    def candidates(self, selected: tuple[str, ...] | None = None) -> dict[str, Any]:
        try:
            with self.candidates_path.open(encoding="utf-8") as handle:
                value = json.load(handle)
        except FileNotFoundError as exc:
            raise ApiError(f"Candidate GeoJSON is missing: {self.candidates_path}", HTTPStatus.NOT_FOUND) from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise ApiError("Could not read candidate GeoJSON", HTTPStatus.INTERNAL_SERVER_ERROR) from exc
        if not isinstance(value, dict) or value.get("type") != "FeatureCollection":
            raise ApiError("Candidate data is not a GeoJSON FeatureCollection", HTTPStatus.INTERNAL_SERVER_ERROR)
        if selected == tuple():
            value = {**value, "features": []}
        elif selected:
            province, districts = self._boundaries()
            district_geometry = unary_union([shape(districts[name]["geometry"]) for name in selected])
            filtered = []
            for feature in value.get("features", []):
                geometry = feature.get("geometry")
                if not geometry:
                    continue
                try:
                    clipped = shape(geometry).intersection(province).intersection(district_geometry)
                except (TypeError, ValueError, ShapelyError) as exc:
                    raise ApiError("Candidate data contains an invalid geometry", HTTPStatus.INTERNAL_SERVER_ERROR) from exc
                if not clipped.is_empty:
                    filtered.append({**feature, "geometry": json.loads(json.dumps(clipped.__geo_interface__))})
            value = {**value, "features": filtered}
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
        repair_counts = repair.get("counts", {})
        return {
            "graph": graph_stats,
            "candidate_status_counts": _candidate_status_counts(candidates),
            "candidate_count": candidates.get("candidate_count", len(candidates.get("candidates", []))),
            "repairs": {
                "accepted": repair_counts.get("accepted_repair_count", 0),
                "skipped": repair_counts.get("skipped_candidate_count", 0),
                "approval_count": repair_counts.get("approval_count", 0),
            },
            "study_area": "Sabaragamuwa Province, Sri Lanka",
        }

    def metrics(self, selected: tuple[str, ...] | None = None) -> dict[str, Any]:
        """Return cached province totals and selected induced-subgraph metrics.

        Node membership uses the district union's boundary-inclusive ``covers``
        predicate. Edge membership requires both endpoints to be selected.
        """
        try:
            graph = self.graph()
        except ApiError as exc:
            if exc.status == HTTPStatus.NOT_FOUND:
                raise ApiError(str(exc), HTTPStatus.SERVICE_UNAVAILABLE, "graph_unavailable") from exc
            raise
        selected_names = tuple(sorted(DISTRICT_NAMES if selected is None else selected))
        with self._metrics_lock:
            if selected_names in self._metrics_cache:
                return self._metrics_cache[selected_names]

            if selected_names:
                _, districts = self._boundaries()
                area = unary_union([shape(districts[name]["geometry"]) for name in selected_names])
                prepared_area = prep(area)
                included_nodes = {
                    node
                    for node, attrs in graph.nodes(data=True)
                    if prepared_area.covers(Point(_number(attrs.get("x")), _number(attrs.get("y"))))
                }
            else:
                included_nodes = set()

            selected_graph = graph.subgraph(included_nodes)
            endpoint_split_edges = sum(
                (source in included_nodes) != (target in included_nodes)
                for source, target, _key in graph.edges(keys=True)
            )
            selected_metrics = {
                "node_count": selected_graph.number_of_nodes(),
                "induced_directed_edge_count": selected_graph.number_of_edges(),
                "weakly_connected_component_count": nx.number_weakly_connected_components(selected_graph),
                "endpoint_split_directed_edge_count": endpoint_split_edges,
            }
            response = {
                "selected_districts": list(selected_names),
                "spatial_policy": {
                    "node_inclusion": "A node is included when its EPSG:4326 point is covered by the selected district polygon union; polygon boundaries are included.",
                    "edge_inclusion": "The selected graph is induced: a directed edge is included only when both endpoint nodes are included. MultiDiGraph keys and parallel edges are preserved.",
                    "geometric_boundary_crossing_directed_edge_count": None,
                    "geometric_boundary_crossing_limitation": "GraphML has node coordinates but no edge geometry. Endpoint interpolation is not used to estimate spatial boundary crossings.",
                    "endpoint_split_directed_edge_count": "Count of directed edge records with exactly one endpoint node included; this is not a geometric boundary-crossing count.",
                },
                "province_wide": {
                    "node_count": graph.number_of_nodes(),
                    "directed_edge_count": graph.number_of_edges(),
                    "weakly_connected_component_count": nx.number_weakly_connected_components(graph),
                },
                "selected_area": selected_metrics,
            }
            self._metrics_cache[selected_names] = response
            return response

    def reviews(self) -> dict[str, Any]:
        try:
            dataset = load_review_dataset(self.review_dataset_path, self.candidate_report_path)
        except ReviewDatasetError as exc:
            raise ApiError(str(exc), HTTPStatus(exc.http_status), exc.code) from exc
        return dataset

    def _road_spatial_index(self, graph: nx.MultiDiGraph) -> tuple[STRtree, list[LineString], list[tuple[Any, Any, Any, dict[str, Any]]]]:
        with self._lock:
            if self._road_tree is None:
                geometries: list[LineString] = []
                records: list[tuple[Any, Any, Any, dict[str, Any]]] = []
                for source, target, key, attrs in graph.edges(keys=True, data=True):
                    source_attrs = graph.nodes[source]
                    target_attrs = graph.nodes[target]
                    source_point = (_number(source_attrs.get("x")), _number(source_attrs.get("y")))
                    target_point = (_number(target_attrs.get("x")), _number(target_attrs.get("y")))
                    geometries.append(LineString([source_point, target_point]))
                    records.append((source, target, key, attrs))
                self._road_geometries = geometries
                self._road_records = records
                self._road_tree = STRtree(geometries)
            return self._road_tree, self._road_geometries, self._road_records

    def roads(self, bbox: tuple[float, float, float, float], selected: tuple[str, ...] | None = None) -> dict[str, Any]:
        min_lon, min_lat, max_lon, max_lat = bbox
        if (max_lon - min_lon) * (max_lat - min_lat) > MAX_BBOX_AREA:
            raise ApiError("Requested bbox is too broad; zoom in to request a bounded road view")
        if selected == tuple():
            return {"type": "FeatureCollection", "features": []}
        graph = self.graph()
        tree, geometries, records = self._road_spatial_index(graph)
        province = district_geometry = None
        if selected:
            province, districts = self._boundaries()
            district_geometry = unary_union([shape(districts[name]["geometry"]) for name in selected])
        features: list[dict[str, Any]] = []
        query_box = box(min_lon, min_lat, max_lon, max_lat)
        for index in tree.query(query_box):
            segment = geometries[index].intersection(query_box)
            if segment.is_empty:
                continue
            if province is not None:
                segment = segment.intersection(province).intersection(district_geometry)
                if segment.is_empty:
                    continue
            if len(features) >= MAX_FEATURES:
                raise ApiError("Requested bbox contains too many road segments; zoom in further")
            source, target, key, attrs = records[index]
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
                "geometry": json.loads(json.dumps(segment.__geo_interface__)),
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

    def _send_artifact(self, path: Path, filename: str, content_type: str) -> None:
        try:
            size = path.stat().st_size
            stream = path.open("rb")
        except OSError as exc:
            raise ApiError("Validated export is no longer available", HTTPStatus.SERVICE_UNAVAILABLE, "export_unavailable") from exc
        with stream:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Access-Control-Allow-Origin", "http://localhost:5173")
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()
            while chunk := stream.read(1024 * 1024):
                self.wfile.write(chunk)

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
                self._send(self.data.candidates(Component2Data._selected_names(parse_qs(parsed.query, keep_blank_values=True))))
            elif parsed.path == "/api/reviews":
                self._send(self.data.reviews())
            elif parsed.path == "/api/metrics":
                selected = Component2Data._selected_names(parse_qs(parsed.query, keep_blank_values=True))
                self._send(self.data.metrics(selected))
            elif parsed.path == "/api/pipeline-status":
                self._send({"stages": self.data.project_audit()["pipeline"]})
            elif parsed.path == "/api/graph-comparison":
                self._send(self.data.project_audit()["graph_comparison"])
            elif parsed.path == "/api/semantic-audit":
                self._send(self.data.project_audit()["semantic_audit"])
            elif parsed.path == "/api/repaired-links":
                self._send(self.data.project_audit()["repaired_links"])
            elif parsed.path == "/api/exports":
                self._send(self.data.project_audit()["exports"])
            elif parsed.path.startswith("/api/exports/"):
                artifact_id = parsed.path.removeprefix("/api/exports/")
                if not artifact_id or "/" in artifact_id or "\\" in artifact_id:
                    raise ApiError("Unknown export artifact", HTTPStatus.NOT_FOUND, "export_not_found")
                exported = self.data._project_audit.export_path(artifact_id)
                if exported is None:
                    raise ApiError("Unknown or unavailable validated export artifact", HTTPStatus.NOT_FOUND, "export_not_found")
                path, item = exported
                media_type = "application/geo+json" if item["format"] == "GeoJSON" else "application/graphml+xml"
                self._send_artifact(path, path.name, media_type)
            elif parsed.path == "/api/boundaries":
                self._send(self.data.boundaries())
            elif parsed.path == "/api/roads":
                query = parse_qs(parsed.query, keep_blank_values=True)
                self._send(self.data.roads(self._parse_bbox(query), Component2Data._selected_names(query)))
            else:
                raise ApiError("Endpoint not found", HTTPStatus.NOT_FOUND, "endpoint_not_found")
        except ApiError as exc:
            self._send({"error": str(exc), "code": exc.code}, exc.status)

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
    print(f"Component 2 API listening on http://{server.server_address[0]}:{server.server_address[1]}")
    print(f"Repository root: {ROOT}")
    print("Review artifact: outputs/component2_evaluation/c2_topology_candidate_reviews.json")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
