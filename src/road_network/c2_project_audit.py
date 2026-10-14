"""Read-only, evidence-based audit views for Component 2 artifacts.

This module never runs pipeline stages or writes research artifacts. Its cache is
invalidated when any configured input's size, modification time, or change time changes, and is
also cleared on process restart. GraphML is hashed and parsed at most once per
snapshot; files with identical hashes share a single metric calculation.
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import networkx as nx
from pyproj import CRS
from shapely.geometry import shape
from shapely.validation import explain_validity

from src.road_network.c2_candidate_review import load_review_dataset

ROOT = Path(__file__).resolve().parents[2]
GRAPH_FILES = {
    "raw": "data/raw/c2_sabaragamuwa_raw_drive_network.graphml",
    "cleaned": "data/intermediate/c2_sabaragamuwa_cleaned_graph.graphml",
    "direction_normalized": "data/intermediate/c2_sabaragamuwa_direction_normalized_graph.graphml",
    "topology_repaired": "data/intermediate/c2_sabaragamuwa_topology_repaired_graph.graphml",
}
ARTIFACTS = {
    **GRAPH_FILES,
    "candidate_geojson": "data/intermediate/c2_topology_candidates.geojson",
    "osm_metadata": "data/raw/c2_sabaragamuwa_osm_metadata.json",
    "baseline_report": "outputs/component2_evaluation/c2_raw_network_baseline.json",
    "cleaning_report": "outputs/component2_evaluation/c2_road_cleaning_report.json",
    "direction_report": "outputs/component2_evaluation/c2_direction_normalization_report.json",
    "candidate_report": "outputs/component2_evaluation/c2_topology_candidates.json",
    "repair_report": "outputs/component2_evaluation/c2_topology_repair_report.json",
    "review_dataset": "outputs/component2_evaluation/c2_topology_candidate_reviews.json",
}
EDGE_ATTRIBUTES = (
    "highway", "length", "layer", "bridge", "tunnel", "access",
    "motor_vehicle", "vehicle", "service", "oneway", "oneway_osm",
    "oneway_normalized", "direction_source",
)
SEMANTIC_GUARDS = {
    "layer": {"implemented": True, "specific_reasons": ["layer_conflict", "layer_context_requires_review", "ambiguous_layer"]},
    "bridge": {"implemented": True, "specific_reasons": ["bridge_conflict", "bridge_context_requires_review", "ambiguous_bridge"]},
    "tunnel": {"implemented": True, "specific_reasons": ["tunnel_conflict", "tunnel_context_requires_review", "ambiguous_tunnel"]},
    "highway": {"implemented": True, "specific_reasons": []},
    "access": {"implemented": True, "specific_reasons": ["access_restriction_conflict", "access_restriction_requires_review", "ambiguous_access"]},
    "motor_vehicle": {"implemented": True, "specific_reasons": ["motor_vehicle_restriction_conflict", "motor_vehicle_restriction_requires_review", "ambiguous_motor_vehicle"]},
    "vehicle": {"implemented": True, "specific_reasons": ["vehicle_restriction_conflict", "vehicle_restriction_requires_review", "ambiguous_vehicle"]},
    "service": {"implemented": True, "specific_reasons": ["ambiguous_service"]},
    "oneway": {"implemented": True, "implementation_stage": "direction_normalization", "specific_reasons": []},
    "direction": {"implemented": True, "implementation_stage": "direction_normalization", "specific_reasons": []},
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "artifact_not_present"
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None, "malformed_or_unreadable_json"
    if not isinstance(value, dict):
        return None, "json_root_is_not_an_object"
    return value, None


def _path_item(relative: str, available: bool) -> dict[str, Any]:
    return {"path": relative, "available": available}


def _safe_project_path(root: Path, relative: str | None) -> Path | None:
    if not isinstance(relative, str) or not relative.strip():
        return None
    candidate = Path(relative)
    path = (candidate if candidate.is_absolute() else root / candidate).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError:
        return None
    return path


def _repo_relative_artifact(root: Path, value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = _safe_project_path(root, value)
    if candidate is None:
        return None
    try:
        return candidate.relative_to(root.resolve()).as_posix()
    except ValueError:
        return None


def _graph_metrics(graph: nx.MultiDiGraph) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(graph, nx.MultiDiGraph) or not graph.is_directed() or not graph.is_multigraph():
        raise ValueError("GraphML did not load as a directed MultiDiGraph.")
    invalid_nodes = 0
    try:
        graph_crs = CRS.from_user_input(graph.graph.get("crs"))
        crs_valid = graph_crs.to_epsg() == 4326
        crs_name = graph_crs.to_string()
    except Exception:
        crs_valid = False
        crs_name = None
    for node, attrs in graph.nodes(data=True):
        if not isinstance(node, str) or not node:
            invalid_nodes += 1
            continue
        try:
            x, y = float(attrs["x"]), float(attrs["y"])
        except (KeyError, TypeError, ValueError):
            invalid_nodes += 1
            continue
        if not (math.isfinite(x) and math.isfinite(y) and -180 <= x <= 180 and -90 <= y <= 90):
            invalid_nodes += 1
    edge_attribute_counts = {name: 0 for name in EDGE_ATTRIBUTES}
    repair_edges: list[dict[str, Any]] = []
    edge_key_count = 0
    for source, target, key, attrs in graph.edges(keys=True, data=True):
        edge_key_count += 1
        for name in EDGE_ATTRIBUTES:
            if name in attrs and attrs[name] not in (None, ""):
                edge_attribute_counts[name] += 1
        repair_id = attrs.get("c2_repair_id")
        if repair_id:
            repair_edges.append({
                "repair_id": str(repair_id),
                "candidate_id": str(attrs.get("c2_repair_candidate_id", "")),
                "source": str(source),
                "target": str(target),
                "edge_key": str(key),
                "attributes": {str(k): str(v) for k, v in attrs.items()},
                "source_coordinates": [float(graph.nodes[source]["x"]), float(graph.nodes[source]["y"])],
                "target_coordinates": [float(graph.nodes[target]["x"]), float(graph.nodes[target]["y"])],
            })
    edge_count = graph.number_of_edges()
    stats = {
        "node_count": graph.number_of_nodes(),
        "directed_edge_count": edge_count,
        "weakly_connected_component_count": nx.number_weakly_connected_components(graph),
        "degree_1_total_directed_degree_count": sum(1 for _, degree in graph.degree() if degree == 1),
    }
    validation = {
        "readable_as_directed_multigraph": True,
        "crs": crs_name,
        "crs_is_epsg4326": crs_valid,
        "node_ids_and_epsg4326_coordinates_valid": invalid_nodes == 0,
        "invalid_node_or_coordinate_count": invalid_nodes,
        "edge_record_count_matches_multigraph_count": edge_key_count == edge_count,
        "required_core_edge_attributes_present": edge_attribute_counts["highway"] == edge_count and edge_attribute_counts["length"] == edge_count,
        "all_node_ids_and_coordinates_valid": invalid_nodes == 0,
        "edge_attribute_present_counts": edge_attribute_counts,
        "edge_attribute_missing_counts": {key: edge_count - value for key, value in edge_attribute_counts.items()},
    }
    return stats, {"validation": validation, "repair_edges": repair_edges}


def _validate_candidate_geojson(
    path: Path,
    candidate_report: dict[str, Any] | None,
    *,
    root: Path = ROOT,
    candidate_report_sha256: str | None = None,
    source_graph: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"status": "unavailable", "reason": "GeoJSON file not present"}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {"status": "failed", "reason": f"GeoJSON unreadable or malformed: {type(exc).__name__}"}
    if not isinstance(payload, dict) or payload.get("type") != "FeatureCollection" or not isinstance(payload.get("features"), list):
        return {"status": "failed", "reason": "Invalid GeoJSON FeatureCollection structure"}
    crs = payload.get("crs")
    crs_properties = crs.get("properties") if isinstance(crs, dict) else None
    crs_name = crs_properties.get("name") if isinstance(crs_properties, dict) else None
    if crs_name not in ("EPSG:4326", "urn:ogc:def:crs:EPSG::4326"):
        return {"status": "failed", "reason": "Expected declared EPSG:4326 GeoJSON CRS", "crs": crs_name}
    invalid = 0
    candidate_ids_by_type: dict[str, dict[str, list[str]]] = {"candidate_connection": {}, "endpoint_u": {}, "endpoint_v": {}}
    missing_properties = 0
    for feature in payload["features"]:
        try:
            if not isinstance(feature, dict) or feature.get("type") != "Feature" or not isinstance(feature.get("properties"), dict):
                raise ValueError("invalid feature or properties")
            geometry = shape(feature.get("geometry"))
            if geometry.is_empty or not geometry.is_valid:
                raise ValueError(explain_validity(geometry))
            min_x, min_y, max_x, max_y = geometry.bounds
            if min_x < -180 or max_x > 180 or min_y < -90 or max_y > 90:
                raise ValueError("coordinates outside EPSG:4326 range")
            props = feature["properties"]
            feature_type = props.get("feature_type")
            if feature_type not in candidate_ids_by_type:
                raise ValueError("unknown candidate feature_type")
            if feature_type == "candidate_connection":
                required = ("candidate_id", "status", "endpoint_u", "endpoint_v", "distance_m", "heading_deviation_degrees", "reasons", "semantic_conflicts", "semantic_missing_attributes")
                if not isinstance(props.get("candidate_id"), str) or not props.get("candidate_id") or props.get("status") not in {"eligible", "needs_review", "rejected"} or any(key not in props for key in required):
                    missing_properties += 1
                else:
                    candidate_ids_by_type[feature_type].setdefault(props["candidate_id"], []).append(props["status"])
            else:
                required = ("candidate_id", "endpoint", "status", "reasons")
                if not isinstance(props.get("candidate_id"), str) or not props.get("candidate_id") or any(key not in props for key in required):
                    missing_properties += 1
                elif props.get("status") not in {"eligible", "needs_review", "rejected"}:
                    missing_properties += 1
                else:
                    candidate_ids_by_type[feature_type].setdefault(props["candidate_id"], []).append(props["status"])
        except Exception:
            invalid += 1
    report_candidates = candidate_report.get("candidates") if isinstance(candidate_report, dict) else None
    report_by_id = {c.get("candidate_id"): c for c in report_candidates if isinstance(c, dict) and isinstance(c.get("candidate_id"), str)} if isinstance(report_candidates, list) else {}
    report_ids = set(report_by_id)
    candidate_feature_ids = set(candidate_ids_by_type["candidate_connection"])
    ids_match = report_ids == candidate_feature_ids if candidate_report is not None else None
    statuses_match = ids_match is True and all(
        candidate_ids_by_type["candidate_connection"].get(candidate_id) == [record.get("status")]
        and candidate_ids_by_type["endpoint_u"].get(candidate_id) == [record.get("status")]
        and candidate_ids_by_type["endpoint_v"].get(candidate_id) == [record.get("status")]
        for candidate_id, record in report_by_id.items()
    )
    candidate_report_input = candidate_report.get("input_graph") if isinstance(candidate_report, dict) else None
    report_graph_path = _repo_relative_artifact(root, candidate_report_input.get("path")) if isinstance(candidate_report_input, dict) else None
    expected_graph_path = GRAPH_FILES["direction_normalized"]
    source_graph_metrics = source_graph.get("metrics") if isinstance(source_graph, dict) else None
    graph_counts_match = bool(
        isinstance(candidate_report_input, dict)
        and isinstance(source_graph_metrics, dict)
        and candidate_report_input.get("node_count") == source_graph_metrics.get("node_count")
        and candidate_report_input.get("edge_count") == source_graph_metrics.get("directed_edge_count")
    )
    source_path_matches = report_graph_path == expected_graph_path
    provenance = {
        "status": "partial" if source_path_matches and graph_counts_match else "unavailable",
        "source_report_path": ARTIFACTS["candidate_report"],
        "source_report_sha256": candidate_report_sha256,
        "candidate_source_graph_path": report_graph_path,
        "candidate_source_graph_path_matches_expected": source_path_matches,
        "candidate_source_graph_sha256_recorded_in_report": False,
        "current_source_graph_path": expected_graph_path,
        "current_source_graph_sha256": source_graph.get("sha256") if isinstance(source_graph, dict) else None,
        "candidate_report_graph_counts_match_current": graph_counts_match,
        "limitation": "The candidate report records its input path and counts but no source graph hash; current path and counts can be checked, but exact source bytes at candidate-generation time cannot be established.",
    }
    valid = invalid == 0 and missing_properties == 0 and ids_match is True and statuses_match and source_path_matches and graph_counts_match
    return {
        "status": "validated" if valid else "failed",
        "feature_count": len(payload["features"]),
        "candidate_connection_count": len(candidate_feature_ids),
        "invalid_feature_count": invalid,
        "candidate_features_missing_id_or_status": missing_properties,
        "candidate_ids_match_report": ids_match,
        "candidate_statuses_match_report": statuses_match,
        "feature_counts_by_type": {feature_type: sum(len(values) for values in candidate_ids_by_type[feature_type].values()) for feature_type in candidate_ids_by_type},
        "crs": crs_name,
        "provenance": provenance,
        "geometry_source_limitation": "Candidate connector lines are derived straight endpoint lines, not original OSM road geometry.",
    }


class ProjectAuditService:
    """Repository-scoped artifact inventory, graph comparison, and audit cache."""

    def __init__(self, root: Path = ROOT, artifacts: dict[str, str] | None = None):
        self.root = root.resolve()
        self.artifacts = dict(ARTIFACTS if artifacts is None else artifacts)
        self._lock = threading.Lock()
        self._signature: tuple[Any, ...] | None = None
        self._snapshot: dict[str, Any] | None = None

    def _paths(self) -> dict[str, Path]:
        resolved = {}
        for key, relative in self.artifacts.items():
            safe = _safe_project_path(self.root, relative)
            resolved[key] = safe if safe is not None else self.root / ".invalid_artifact_path" / key
        return resolved

    def _current_signature(self, paths: dict[str, Path]) -> tuple[Any, ...]:
        result = []
        for key, path in sorted(paths.items()):
            try:
                stat = path.stat()
                result.append((key, True, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
            except OSError:
                result.append((key, False, None, None))
        return tuple(result)

    def snapshot(self, preloaded_graph: nx.MultiDiGraph | None = None) -> dict[str, Any]:
        paths = self._paths()
        signature = self._current_signature(paths)
        with self._lock:
            if self._snapshot is None or signature != self._signature:
                self._snapshot = self._build(paths, preloaded_graph)
                self._signature = signature
            return deepcopy(self._snapshot)

    def _build(self, paths: dict[str, Path], preloaded_graph: nx.MultiDiGraph | None = None) -> dict[str, Any]:
        artifact_hashes: dict[str, str | None] = {}
        artifact_errors: dict[str, str | None] = {}
        reports: dict[str, dict[str, Any] | None] = {}
        for key, relative in self.artifacts.items():
            path = paths[key]
            if key in GRAPH_FILES or key == "candidate_geojson":
                continue
            report, error = _read_json(path)
            reports[key] = report
            artifact_errors[key] = error
            try:
                artifact_hashes[key] = sha256_file(path) if error is None else None
            except OSError:
                artifact_hashes[key] = None

        graph_hashes: dict[str, str | None] = {}
        graph_errors: dict[str, str | None] = {}
        for stage, relative in GRAPH_FILES.items():
            path = paths[stage]
            try:
                graph_hashes[stage] = sha256_file(path)
                graph_errors[stage] = None
            except FileNotFoundError:
                graph_hashes[stage] = None
                graph_errors[stage] = "graph_file_not_present"
            except OSError as exc:
                graph_hashes[stage] = None
                graph_errors[stage] = f"graph_hash_failed:{type(exc).__name__}"

        metric_by_hash: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
        normalized_hash = graph_hashes.get("direction_normalized")
        if preloaded_graph is not None and normalized_hash:
            try:
                metric_by_hash[normalized_hash] = _graph_metrics(preloaded_graph)
            except Exception:
                pass
        for stage in GRAPH_FILES:
            digest = graph_hashes[stage]
            if not digest or digest in metric_by_hash:
                continue
            path = paths[stage]
            try:
                graph = nx.read_graphml(path, force_multigraph=True)
                metric_by_hash[digest] = _graph_metrics(graph)
                del graph
            except Exception as exc:
                graph_errors[stage] = f"graph_validation_failed:{type(exc).__name__}:{exc}"

        graph_results: dict[str, Any] = {}
        for stage, relative in GRAPH_FILES.items():
            digest = graph_hashes[stage]
            metrics = metric_by_hash.get(digest) if digest else None
            graph_results[stage] = {
                "artifact_path": relative,
                "sha256": digest,
                "metrics_freshly_computed": metrics is not None,
                "metrics_source": "fresh_graphml_computation" if metrics is not None else None,
                "status": "validated" if metrics and graph_errors[stage] is None and metrics[1]["validation"]["required_core_edge_attributes_present"] and metrics[1]["validation"]["crs_is_epsg4326"] and metrics[1]["validation"]["all_node_ids_and_coordinates_valid"] and metrics[1]["validation"]["edge_record_count_matches_multigraph_count"] and (stage not in {"direction_normalized", "topology_repaired"} or (metrics[1]["validation"]["edge_attribute_present_counts"]["oneway_normalized"] == metrics[0]["directed_edge_count"] and metrics[1]["validation"]["edge_attribute_present_counts"]["direction_source"] == metrics[0]["directed_edge_count"])) else "unavailable" if graph_errors[stage] == "graph_file_not_present" else "failed",
                "error": graph_errors[stage],
                "metrics": metrics[0] if metrics else None,
                "validation": metrics[1]["validation"] if metrics else None,
                "repair_edges": metrics[1]["repair_edges"] if metrics else [],
            }

        statuses = self._pipeline(paths, reports, artifact_errors, artifact_hashes, graph_results)
        comparison = self._comparison(graph_results)
        candidate_report = reports.get("candidate_report")
        semantic = self._semantic_audit(candidate_report, artifact_hashes.get("candidate_report"), graph_results, reports.get("repair_report"), reports.get("direction_report"))
        repaired_links = self._repaired_links(reports.get("repair_report"), artifact_errors.get("repair_report"), artifact_hashes.get("repair_report"), artifact_hashes.get("candidate_report"), graph_results)
        geojson_validation = _validate_candidate_geojson(
            paths["candidate_geojson"], candidate_report, root=self.root,
            candidate_report_sha256=artifact_hashes.get("candidate_report"),
            source_graph=graph_results.get("direction_normalized"),
        )
        exports = self._exports(paths, artifact_hashes, graph_results, geojson_validation)
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "cache_policy": "Cached by artifact size, mtime_ns, and ctime_ns; recomputed when a tracked artifact changes or the process restarts.",
            "pipeline": statuses,
            "graph_comparison": comparison,
            "semantic_audit": semantic,
            "repaired_links": repaired_links,
            "exports": exports,
        }

    def _pipeline(self, paths: dict[str, Path], reports: dict[str, dict[str, Any] | None], errors: dict[str, str | None], artifact_hashes: dict[str, str | None], graphs: dict[str, Any]) -> list[dict[str, Any]]:
        get = lambda key: reports.get(key)
        exists = lambda key: paths[key].is_file()
        metadata, cleaning, direction, candidates, repair = (get(k) for k in ("osm_metadata", "cleaning_report", "direction_report", "candidate_report", "repair_report"))
        review = None
        review_error = errors.get("review_dataset")
        if exists("review_dataset") and exists("candidate_report"):
            try:
                review = load_review_dataset(paths["review_dataset"], paths["candidate_report"])
                review_error = None
            except Exception as exc:
                review_error = f"review_validation_failed:{type(exc).__name__}:{exc}"
        raw = graphs["raw"]
        cleaned = graphs["cleaned"]
        normalized = graphs["direction_normalized"]
        repaired = graphs["topology_repaired"]
        candidate_geojson = _validate_candidate_geojson(
            paths["candidate_geojson"], candidates, root=self.root,
            candidate_report_sha256=artifact_hashes.get("candidate_report"),
            source_graph=normalized,
        )

        def stage(stage_id: str, status: str, explanation: str, inputs: list[str], outputs: list[str], source_reports: list[str] = [], graph_sha: str | None = None, timestamp: Any = None, runtime: Any = None, limitations: list[str] | None = None, historical: bool = True, runtime_source: dict[str, str] | None = None, runtime_status_override: str | None = None) -> dict[str, Any]:
            missing = []
            for artifact in inputs:
                candidate = _safe_project_path(self.root, artifact)
                if candidate is not None and not candidate.exists():
                    missing.append(artifact)
            for artifact in outputs:
                candidate = _safe_project_path(self.root, artifact)
                if candidate is not None and not candidate.exists():
                    missing.append(artifact)
            runtime_status = runtime_status_override or ("recorded" if runtime is not None and runtime_source is not None else "not_recorded")
            runtime_recorded = runtime_status == "recorded"
            return {"stage_id": stage_id, "status": status, "explanation": explanation,
                    "input_artifacts": inputs, "output_artifacts": outputs, "source_report_paths": source_reports,
                    "graph_sha256": graph_sha, "timestamp": timestamp,
                    "runtime_seconds": runtime if runtime_recorded else None,
                    "runtime_status": runtime_status,
                    "runtime_source": runtime_source if runtime_recorded else None,
                    "runtime_evidence_kind": "historical_metadata" if runtime_recorded else None,
                    "runtime_explanation": "Measured duration is recorded in the cited source metadata." if runtime_recorded else "No trustworthy measured duration is recorded for this stage." if runtime_status == "not_recorded" else "A runtime field exists but is invalid; no duration is reported.",
                    "missing_prerequisites": sorted(set(missing)), "limitations": limitations or [],
                    "evidence_kind": "historical_artifact_or_report" if historical else "fresh_computation"}

        extraction_status = "unavailable"
        extraction_explanation = "Extraction metadata or raw GraphML is unavailable."
        extraction_limitations: list[str] = []
        metadata_source_path = _repo_relative_artifact(self.root, metadata.get("source_file")) if metadata else None
        if metadata is not None and raw["status"] == "validated":
            if metadata.get("status") == "success" and metadata.get("validation_issues") == [] and (metadata.get("node_count"), metadata.get("edge_count")) == (raw["metrics"]["node_count"], raw["metrics"]["directed_edge_count"]):
                extraction_status = "completed"
                extraction_explanation = "Historical extraction metadata reports success with no recorded validation issues; raw GraphML exists and its counts match metadata."
            else:
                extraction_status = "partial"
                extraction_explanation = "Raw GraphML is valid, but extraction metadata status, issues, or recorded counts do not fully match it."
            pbf = self.root / str(metadata.get("source_file", ""))
            if not pbf.is_file():
                extraction_limitations.append("The source PBF is not present; extraction was not rerun.")
            if metadata_source_path is None:
                extraction_limitations.append("The recorded source PBF path is missing or outside the repository; its absolute location is not exposed.")
        if metadata is None and errors.get("osm_metadata") not in (None, "artifact_not_present"):
            extraction_status = "failed"
            extraction_explanation = "Extraction metadata exists but is malformed or unreadable."
        extraction_runtime_value = metadata.get("extraction_total_seconds") if metadata else None
        extraction_runtime_valid = isinstance(extraction_runtime_value, (int, float)) and not isinstance(extraction_runtime_value, bool) and math.isfinite(extraction_runtime_value) and extraction_runtime_value >= 0
        extraction_runtime = extraction_runtime_value if extraction_runtime_valid else None
        extraction_runtime_status = "recorded" if extraction_runtime_valid else "invalid_record" if metadata and "extraction_total_seconds" in metadata else "not_recorded"
        stages = [stage("extraction", extraction_status, extraction_explanation, [metadata_source_path] if metadata_source_path else [], [ARTIFACTS["raw"]], [ARTIFACTS["osm_metadata"]], raw["sha256"], metadata.get("extraction_datetime") if metadata else None, extraction_runtime, extraction_limitations, runtime_source={"artifact_path": ARTIFACTS["osm_metadata"], "field": "extraction_total_seconds"} if extraction_runtime_valid else None, runtime_status_override=extraction_runtime_status)]

        clean_report_ok = cleaning is not None and all(isinstance(cleaning.get(section), dict) for section in ("inspection", "records", "structure", "decisions"))
        clean_valid = clean_report_ok and cleaned["status"] == "validated" and raw["status"] == "validated" and raw["sha256"] == cleaned["sha256"] and cleaning["records"].get("nodes_inspected") == cleaned["metrics"]["node_count"] and cleaning["records"].get("edges_inspected") == cleaned["metrics"]["directed_edge_count"]
        clean_status = "completed" if clean_valid else "failed" if (cleaning is not None and not clean_report_ok) or errors.get("cleaning_report") not in (None, "artifact_not_present") else "partial" if cleaning else "unavailable"
        cleaning_inspection = cleaning.get("inspection") if isinstance(cleaning, dict) else None
        stages.append(stage("cleaning_validation", clean_status, "Historical cleaning report and raw/cleaned GraphML validate; the output is byte-identical to raw, consistent with validation-only cleaning." if clean_valid else "Cleaning report or graph evidence is insufficient or inconsistent.", [ARTIFACTS["raw"]], [ARTIFACTS["cleaned"]], [ARTIFACTS["cleaning_report"]], cleaned["sha256"], cleaning_inspection.get("generated_at") if isinstance(cleaning_inspection, dict) else None, None, ["No records were removed; this does not establish topology correctness."]))

        direction_block = direction.get("direction") if isinstance(direction, dict) else None
        direction_ok = direction is not None and isinstance(direction.get("direction"), dict) and isinstance(direction.get("preservation"), dict) and isinstance(direction.get("serialization_preservation"), dict) and normalized["status"] == "validated" and cleaned["status"] == "validated" and (cleaned["metrics"]["node_count"], cleaned["metrics"]["directed_edge_count"]) == (normalized["metrics"]["node_count"], normalized["metrics"]["directed_edge_count"]) and all(direction["preservation"].get(k) is True for k in ("same_node_count", "same_edge_count", "same_edge_keys", "graph_type_preserved", "directed_preserved", "multigraph_preserved"))
        direction_independent = bool(direction_block and direction_block.get("direction_semantics_independently_verified"))
        stages.append(stage("direction_normalization", "completed" if direction_ok and direction_independent else "partial" if direction_ok else "failed" if direction else "unavailable", "Direction metadata and serialization preservation are evidenced, but independent semantic verification is not recorded." if direction_ok and not direction_independent else "Direction normalization report and output graph were validated." if direction_ok else "Direction report or graph evidence is insufficient.", [ARTIFACTS["cleaned"]], [ARTIFACTS["direction_normalized"]], [ARTIFACTS["direction_report"]], normalized["sha256"], direction.get("generated_at") if direction else None, None, [direction.get("direction", {}).get("verification_limitation", "Direction semantics independently verified: false.")] if direction_ok and not direction_independent else []))

        candidate_rows = candidates.get("candidates") if isinstance(candidates, dict) else None
        candidate_ids = [item.get("candidate_id") for item in candidate_rows if isinstance(item, dict)] if isinstance(candidate_rows, list) else []
        candidate_outcomes = {status: sum(1 for item in candidate_rows if isinstance(item, dict) and item.get("status") == status) for status in ("eligible", "rejected", "needs_review")} if isinstance(candidate_rows, list) else {}
        candidate_counts = candidates.get("counts") if isinstance(candidates, dict) else None
        candidate_counts = candidate_counts if isinstance(candidate_counts, dict) else {}
        candidate_input_graph = candidates.get("input_graph") if isinstance(candidates, dict) else None
        candidate_counts_valid = all(candidate_counts.get(f"{status}_count") == count for status, count in candidate_outcomes.items()) and candidate_counts.get("candidate_pair_count") == len(candidate_rows or [])
        candidate_ok = isinstance(candidate_rows, list) and isinstance(candidate_input_graph, dict) and len(candidate_ids) == len(candidate_rows) and all(isinstance(cid, str) and cid for cid in candidate_ids) and len(set(candidate_ids)) == len(candidate_ids) and candidate_counts_valid and normalized["status"] == "validated" and candidate_input_graph.get("node_count") == normalized["metrics"]["node_count"] and candidate_input_graph.get("edge_count") == normalized["metrics"]["directed_edge_count"] and candidate_geojson.get("status") == "validated"
        candidate_status = "completed" if candidate_ok else "failed" if errors.get("candidate_report") not in (None, "artifact_not_present") or candidates else "unavailable"
        stages.append(stage("candidate_detection", candidate_status, "Historical candidate report matches the normalized graph counts and its GeoJSON candidate IDs, statuses, CRS, and geometries validate." if candidate_ok else "Candidate report, graph relationship, or GeoJSON evidence is incomplete or inconsistent.", [ARTIFACTS["direction_normalized"]], [ARTIFACTS["candidate_geojson"]], [ARTIFACTS["candidate_report"]], normalized["sha256"] if candidate_ok else None, None, None, ["Candidate screening results are not ground truth; thresholds are exploratory."]))

        review_count = len(review.get("reviews", [])) if review else None
        all_unreviewed = bool(review) and all(item.get("review_state") == "unreviewed" for item in review["reviews"])
        stages.append(stage("candidate_review", "pending" if all_unreviewed else "completed" if review else "failed" if review_error and "validation_failed" in review_error else "unavailable", f"Review dataset validates against the current candidate report ({review_count} records); all records remain unreviewed." if all_unreviewed else "Human review artifact validates." if review else "Human review dataset is unavailable or invalid.", [ARTIFACTS["candidate_report"]], [ARTIFACTS["review_dataset"]], [ARTIFACTS["review_dataset"]], artifact_hashes.get("candidate_report"), None, None, [review_error] if review_error else []))

        before_metrics = repair.get("before", {}) if repair else {}
        after_metrics = repair.get("after", {}) if repair else {}
        repair_provenance = repair.get("provenance") if isinstance(repair, dict) else None
        repair_provenance = repair_provenance if isinstance(repair_provenance, dict) else {}
        repair_ok = repair is not None and isinstance(repair.get("counts"), dict) and isinstance(repair.get("accepted_repairs"), list) and repair.get("counts", {}).get("accepted_repair_count") == len(repair.get("accepted_repairs", [])) and repair.get("counts", {}).get("real_data_repair_count") == len(repair.get("accepted_repairs", [])) and repair.get("real_data_repair_occurred") is (len(repair.get("accepted_repairs", [])) > 0) and repaired["status"] == "validated" and normalized["status"] == "validated" and (before_metrics.get("node_count"), before_metrics.get("edge_count")) == (normalized["metrics"]["node_count"], normalized["metrics"]["directed_edge_count"]) and (after_metrics.get("node_count"), after_metrics.get("edge_count")) == (repaired["metrics"]["node_count"], repaired["metrics"]["directed_edge_count"]) and repair_provenance.get("candidate_report_sha256") == artifact_hashes.get("candidate_report")
        accepted = repair.get("counts", {}).get("accepted_repair_count") if repair else None
        repair_status = "completed" if repair_ok else "failed" if repair is not None or errors.get("repair_report") not in (None, "artifact_not_present") else "unavailable"
        stages.append(stage("repair", repair_status, "Repair report and repaired GraphML validate. No real repairs accepted; therefore there are no accepted real-data repair joins to assess." if repair_ok and accepted == 0 else "Approval-gated repair report and graph validate." if repair_ok else "Repair evidence or graph provenance does not validate.", [ARTIFACTS["direction_normalized"], ARTIFACTS["candidate_report"]], [ARTIFACTS["topology_repaired"]], [ARTIFACTS["repair_report"]], repaired["sha256"] if repair_ok else None, None, None, ["Zero accepted repairs is not proof that joins are valid or that topology is correct."]))

        evaluation_reports = [ARTIFACTS[name] for name in ("baseline_report", "cleaning_report", "direction_report", "candidate_report", "repair_report") if reports.get(name)]
        baseline = reports.get("baseline_report")
        baseline_valid = isinstance(baseline, dict) and all(isinstance(baseline.get(section), dict) for section in ("inspection", "graph_structure", "node_statistics", "edge_statistics"))
        evaluation_valid = bool(baseline_valid and clean_report_ok and direction_ok and candidate_ok and repair_ok)
        baseline_inspection = baseline.get("inspection") if isinstance(baseline, dict) else None
        stages.append(stage("evaluation", "partial" if evaluation_valid else "unavailable", "Historical graph and screening reports are available; no independent ground truth supports Precision, Recall, F1, or a topology-improvement claim." if evaluation_valid else "Evaluation reports are incomplete or fail structural validation.", [ARTIFACTS["topology_repaired"]], evaluation_reports, evaluation_reports, repaired["sha256"] if repaired["status"] == "validated" else None, baseline_inspection.get("generated_at") if isinstance(baseline_inspection, dict) else None, None, ["No independent ground-truth evaluation is available."]))

        export_valid = all(graphs[k]["status"] == "validated" for k in GRAPH_FILES) and candidate_geojson.get("status") == "validated"
        stages.append(stage("export", "completed" if export_valid else "partial" if any(graphs[k]["status"] == "validated" for k in GRAPH_FILES) or candidate_geojson.get("status") == "validated" else "unavailable", "Existing GraphML artifacts validate as directed multigraphs and candidate GeoJSON validates; read-only downloads are available." if export_valid else "Only some existing export artifacts are available or valid.", [ARTIFACTS["raw"], ARTIFACTS["cleaned"], ARTIFACTS["direction_normalized"], ARTIFACTS["topology_repaired"]], [ARTIFACTS["candidate_geojson"]], [], None, None, None, ["No repaired-road GeoJSON is available because the graph does not retain original edge geometry."]))
        return stages

    @staticmethod
    def _comparison(graphs: dict[str, Any]) -> dict[str, Any]:
        comparisons = []
        names = list(GRAPH_FILES)
        for index, left in enumerate(names):
            for right in names[index + 1:]:
                lh, rh = graphs[left]["sha256"], graphs[right]["sha256"]
                comparisons.append({"left": left, "right": right,
                                    "byte_identical": lh == rh if lh and rh else None,
                                    "structural_equivalence": "not_assessed",
                                    "explanation": "SHA-256 hashes match; files are byte-identical." if lh and lh == rh else "Different or unavailable hashes do not establish structural difference; structural equivalence was not computed."})
        overall = "available" if all(value["status"] == "validated" for value in graphs.values()) else "partial"
        return {"status": overall, "graphs": {k: {key: value for key, value in v.items() if key != "repair_edges"} for k, v in graphs.items()},
                "degree_1_definition": "Count of nodes whose directed total degree (in-degree plus out-degree, counting multiedge records) equals one.",
                "identity_comparisons": comparisons,
                "interpretation_limitations": ["Equal counts do not prove graph identity.", "Structural equivalence is not computed when hashes differ.", "No topology-quality improvement is inferred from these metrics."]}

    @staticmethod
    def _semantic_audit(report: dict[str, Any] | None, report_hash: str | None, graphs: dict[str, Any], repair_report: dict[str, Any] | None, direction_report: dict[str, Any] | None) -> dict[str, Any]:
        if report is None or not isinstance(report.get("candidates"), list):
            return {"status": "unavailable", "reason": "Candidate report is missing or malformed.", "guards": {}}
        candidates = report["candidates"]
        counts = report.get("counts") if isinstance(report.get("counts"), dict) else {}
        outcomes = {status: sum(1 for c in candidates if isinstance(c, dict) and c.get("status") == status) for status in ("rejected", "needs_review", "eligible")}
        recorded_counts = {"rejected_count": outcomes["rejected"], "needs_review_count": outcomes["needs_review"], "eligible_count": outcomes["eligible"], "candidate_pair_count": len(candidates)}
        guards = {}
        graph_attrs = graphs["direction_normalized"].get("validation", {}).get("edge_attribute_present_counts")
        total_edges = graphs["direction_normalized"].get("metrics", {}).get("directed_edge_count")
        for attr, config in SEMANTIC_GUARDS.items():
            with_evidence = missing = unknown = ambiguous = 0
            candidate_evidence_available = attr not in {"oneway", "direction"}
            reason_hits: dict[str, int] = {}
            reason_candidate_ids: dict[str, list[str]] = {}
            missing_evidence_ids: list[str] = []
            conflict_evidence_ids: list[str] = []
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    continue
                if not candidate_evidence_available:
                    continue
                endpoints = candidate.get("endpoint_semantics")
                endpoints = endpoints if isinstance(endpoints, dict) else {}
                values = [endpoints.get(side, {}).get(attr) for side in ("u", "v") if isinstance(endpoints.get(side), dict)]
                actual = [v for v in values if isinstance(v, dict)]
                if any(v.get("values") for v in actual): with_evidence += 1
                if any(v.get("missing") for v in actual) or len(actual) != 2: missing += 1
                if any(v.get("ambiguous") for v in actual): ambiguous += 1
                if any(str(value).strip().lower() in {"unknown", "unclassified", "?"} for item in actual for value in (item.get("values") if isinstance(item.get("values"), list) else [])): unknown += 1
                recorded_reasons = candidate.get("reasons")
                reasons = set(recorded_reasons) if isinstance(recorded_reasons, list) else set()
                candidate_id = str(candidate.get("candidate_id", ""))
                recorded_missing = candidate.get("semantic_missing_attributes")
                recorded_missing = recorded_missing if isinstance(recorded_missing, list) else []
                recorded_conflicts = candidate.get("semantic_conflicts")
                recorded_conflicts = recorded_conflicts if isinstance(recorded_conflicts, list) else []
                if attr in recorded_missing:
                    missing_evidence_ids.append(candidate_id)
                if any(isinstance(reason, str) and reason.startswith(f"{attr}_") for reason in recorded_conflicts):
                    conflict_evidence_ids.append(candidate_id)
                for reason in config["specific_reasons"]:
                    if reason in reasons:
                        reason_hits[reason] = reason_hits.get(reason, 0) + 1
                        reason_candidate_ids.setdefault(reason, []).append(candidate_id)
            guards[attr] = {
                "guard_implemented_in_candidate_screening": config["implemented"] and attr not in {"oneway", "direction"},
                "implementation_stage": config.get("implementation_stage", "candidate_screening"),
                "cross_endpoint_value_compatibility_rule_implemented": attr in {"layer", "bridge", "tunnel", "access", "motor_vehicle", "vehicle"},
                "attribute_presence_or_missingness_checked": attr in {"layer", "bridge", "tunnel", "highway", "access", "motor_vehicle", "vehicle", "service"},
                "source_evidence": {"candidate_endpoint_evidence_available": candidate_evidence_available,
                                    "attribute_present_for_any_endpoint_candidates": with_evidence if candidate_evidence_available else None,
                                    "candidates_with_missing_or_unavailable_attribute": missing if candidate_evidence_available else None,
                                    "candidates_with_ambiguous_attribute": ambiguous if candidate_evidence_available else None,
                                    "candidates_with_explicit_unknown_value": unknown if candidate_evidence_available else None,
                                    "graphml_directed_edge_attribute_present_count": graph_attrs.get(attr) if graph_attrs is not None and attr in graph_attrs else None,
                                    "graphml_directed_edge_attribute_missing_count": total_edges - graph_attrs[attr] if graph_attrs is not None and attr in graph_attrs and total_edges is not None else None},
                "explicitly_recorded_guard_reason_counts": reason_hits,
                "candidate_ids_by_explicit_reason": reason_candidate_ids,
                "explicit_candidate_evidence": {
                    "candidate_ids_recording_attribute_missing": missing_evidence_ids,
                    "candidate_ids_recording_attribute_conflict": conflict_evidence_ids,
                    "candidate_ids_recording_specific_reason": sorted({cid for ids in reason_candidate_ids.values() for cid in ids}),
                    "pass_outcomes_recorded": False,
                },
                "per_candidate_outcome_reconstructable": False,
                "direction_report_evidence": direction_report.get("direction", {}) if attr in {"oneway", "direction"} and isinstance(direction_report, dict) else None,
                "limitation": "Candidate evidence records endpoint values and emitted reasons only; it does not record every guard evaluation. Missing-attribute reasons can be aggregated, and reason occurrences overlap across candidates."}
        result = {"status": "available" if all(counts.get(k) == v for k, v in recorded_counts.items()) else "partial", "freshly_computed": True,
                  "source_report_path": ARTIFACTS["candidate_report"], "source_report_sha256": report_hash,
                  "generated_at": datetime.now(timezone.utc).isoformat(), "candidate_outcome_counts": outcomes,
                  "stored_candidate_counts": counts, "recomputed_candidate_counts_match_report": all(counts.get(k) == v for k, v in recorded_counts.items()), "reason_counts_non_exclusive": report.get("reason_counts", {}),
                  "approved_repair_count": repair_report.get("counts", {}).get("accepted_repair_count") if isinstance(repair_report, dict) else None, "guards": guards,
                  "integrity_note": "Reason counts are overlapping evidence occurrences, not mutually exclusive candidate totals; candidates are screening evidence, not confirmed defects."}
        return result

    def _repaired_links(self, report: dict[str, Any] | None, error: str | None, repair_hash: str | None, candidate_hash: str | None, graphs: dict[str, Any]) -> dict[str, Any]:
        empty = {"type": "FeatureCollection", "features": [], "crs": {"type": "name", "properties": {"name": "EPSG:4326"}}}
        if report is None or not isinstance(report.get("counts"), dict) or not isinstance(report.get("accepted_repairs"), list):
            return {"status": "unavailable", "collection": empty, "reason": "Repair report missing or malformed; repaired links cannot be verified."}
        accepted = report["accepted_repairs"]
        count = report["counts"].get("accepted_repair_count")
        if count != len(accepted):
            return {"status": "unavailable", "collection": empty, "reason": "Accepted-repair count conflicts with the repair record list; no links are returned."}
        if count == 0 and not accepted:
            if report.get("counts", {}).get("real_data_repair_count") != 0 or report.get("real_data_repair_occurred") is not False:
                return {"status": "unavailable", "collection": empty, "reason": "Repair report has inconsistent zero-repair indicators; no links are returned."}
            if graphs["topology_repaired"].get("repair_edges"):
                return {"status": "unavailable", "collection": empty, "accepted_real_data_repair_count": 0,
                        "reason": "Repair report says zero accepted repairs but repaired GraphML contains repair-tagged edges; evidence conflicts, so no links are returned."}
            return {"status": "empty", "collection": empty, "accepted_real_data_repair_count": 0,
                    "repair_report_path": ARTIFACTS["repair_report"], "repair_report_sha256": repair_hash,
                    "does_not_establish_zero_invalid_joins": True,
                    "reason": "No approved real-data repaired links are available. No real repairs accepted; therefore there are no accepted real-data repair joins to assess."}
        graph_edges = {item["repair_id"]: item for item in graphs["topology_repaired"].get("repair_edges", [])}
        features = []
        manifest_rel = report.get("approval_manifest_path")
        manifest_path = _safe_project_path(self.root, manifest_rel)
        if manifest_path is None or not manifest_path.is_file():
            return {"status": "unavailable", "collection": empty, "reason": "Accepted repair evidence lacks an available repository-local explicit approval manifest."}
        manifest, manifest_error = _read_json(manifest_path)
        if manifest_error or not isinstance(manifest, dict) or not isinstance(manifest.get("approvals"), list):
            return {"status": "unavailable", "collection": empty, "reason": "Approval manifest is unreadable or does not contain an approvals list."}
        provenance = report.get("provenance") if isinstance(report.get("provenance"), dict) else {}
        if provenance.get("candidate_report_sha256") != candidate_hash:
            return {"status": "unavailable", "collection": empty, "reason": "Repair report candidate-source hash does not match the current candidate report."}
        approved = {str(a.get("candidate_id")): a for a in manifest["approvals"] if isinstance(a, dict) and a.get("approved") is True}
        for repair in accepted:
            if not isinstance(repair, dict):
                continue
            repair_id = str(repair.get("repair_id", ""))
            candidate_id = str(repair.get("candidate_id", ""))
            expected_id = "repair-" + hashlib.sha256(f"{candidate_id}:{repair.get('direction', '')}".encode("utf-8")).hexdigest()[:16]
            if repair_id != expected_id:
                continue
            edge = graph_edges.get(repair_id)
            approval = approved.get(candidate_id)
            if not edge or not approval or edge["candidate_id"] != candidate_id:
                continue
            if repair.get("candidate_report_sha256") != candidate_hash or repair.get("status") != "accepted":
                continue
            if str(repair.get("source")) != edge["source"] or str(repair.get("target")) != edge["target"] or str(repair.get("edge_key")) != edge["edge_key"]:
                continue
            if repair.get("direction") != approval.get("direction") or repair.get("reason") != approval.get("reason"):
                continue
            candidate_evidence = repair.get("candidate_evidence")
            candidate_evidence = candidate_evidence if isinstance(candidate_evidence, dict) else {}
            if (candidate_evidence.get("candidate_id") != candidate_id
                    or candidate_evidence.get("status") != "eligible"
                    or candidate_evidence.get("already_connected") is not False
                    or candidate_evidence.get("heading_compatible") is not True
                    or candidate_evidence.get("semantic_conflicts")
                    or candidate_evidence.get("semantic_missing_attributes")):
                continue
            approved_attributes = approval.get("edge_attributes")
            recorded_attributes = repair.get("edge_attributes")
            if not isinstance(approved_attributes, dict) or not isinstance(recorded_attributes, dict):
                continue
            if any(str(recorded_attributes.get(key)) != str(value) or str(edge["attributes"].get(key)) != str(value) for key, value in approved_attributes.items()):
                continue
            if report.get("real_data_repair_occurred") is not True or report.get("counts", {}).get("real_data_repair_count") != len(accepted):
                continue
            features.append({"type": "Feature", "id": repair_id,
                             "geometry": {"type": "LineString", "coordinates": [edge["source_coordinates"], edge["target_coordinates"]]},
                             "properties": {"repair_id": repair_id, "candidate_id": candidate_id,
                                            "geometry_kind": "derived_straight_line_between_graph_endpoints",
                                            "source_graph_path": ARTIFACTS["topology_repaired"],
                                            "source_graph_sha256": graphs["topology_repaired"].get("sha256"),
                                            "approval_manifest_path": manifest_rel,
                                            "repair_report_path": ARTIFACTS["repair_report"],
                                            "repair_report_sha256": repair_hash,
                                            "candidate_report_sha256": candidate_hash}})
        collection = {**empty, "features": features}
        return {"status": "available" if len(features) == len(accepted) else "unavailable",
                "collection": collection, "accepted_real_data_repair_count": count,
                "verified_repaired_link_count": len(features),
                "repair_report_path": ARTIFACTS["repair_report"], "repair_report_sha256": repair_hash,
                "reason": "Each feature is matched to an accepted report record, explicit approval, and current repaired-graph edge." if len(features) == len(accepted) else "Some accepted repairs lack complete approval, provenance, or matching graph-edge evidence; no unverified links are returned."}

    def _exports(self, paths: dict[str, Path], hashes: dict[str, str | None], graphs: dict[str, Any], geojson: dict[str, Any]) -> dict[str, Any]:
        items = []
        labels = {"raw": "Raw GraphML", "cleaned": "Cleaned GraphML", "direction_normalized": "Direction-normalized GraphML", "topology_repaired": "Topology-repaired GraphML"}
        for artifact_id, label in labels.items():
            graph = graphs[artifact_id]
            items.append({"artifact_id": artifact_id, "label": label, "path": self.artifacts[artifact_id],
                          "available": graph["status"] == "validated", "validation_status": graph["status"],
                          "sha256": graph["sha256"], "format": "GraphML", "download_path": f"/api/exports/{artifact_id}" if graph["status"] == "validated" else None,
                          "limitations": ["Directed MultiDiGraph; multiedge keys preserved.", "Node coordinates are EPSG:4326; GraphML edge geometry is not retained."]})
        items.append({"artifact_id": "candidate_geojson", "label": "Candidate GeoJSON", "path": self.artifacts["candidate_geojson"],
                      "available": geojson.get("status") == "validated", "validation_status": geojson.get("status"),
                      "sha256": sha256_file(paths["candidate_geojson"]) if paths["candidate_geojson"].is_file() else None,
                      "format": "GeoJSON", "crs": geojson.get("crs"), "provenance": geojson.get("provenance"),
                      "download_path": "/api/exports/candidate_geojson" if geojson.get("status") == "validated" else None,
                      "limitations": [geojson.get("geometry_source_limitation", "Candidate geometry not validated.")]})
        return {"items": items, "path_policy": "Download routes use a fixed artifact ID allowlist and never accept filesystem paths."}

    def export_path(self, artifact_id: str) -> tuple[Path, dict[str, Any]] | None:
        snapshot = self.snapshot()
        item = next((entry for entry in snapshot["exports"]["items"] if entry["artifact_id"] == artifact_id), None)
        if not item or not item["available"]:
            return None
        path = self._paths().get(artifact_id)
        if path is None or not path.is_file():
            return None
        return path, item
