# Component 2 artifact audit API

These read-only endpoints inspect artifacts already in the repository. They do not run extraction, cleaning, direction normalization, candidate detection, repair, or evaluation, and they do not write research outputs. The API uses the existing Python standard-library HTTP server.

## Endpoints

| Request | Response |
| --- | --- |
| `GET /api/pipeline-status` | `{"stages": [...]}` for extraction, cleaning/validation, direction normalization, candidate detection, candidate review, repair, evaluation, and export. |
| `GET /api/graph-comparison` | Graph artifact availability, validation, counts, SHA-256, and all pairwise byte-identity checks. |
| `GET /api/semantic-audit` | Candidate outcome counts and evidence coverage for semantic attributes and guards. |
| `GET /api/repaired-links` | EPSG:4326 FeatureCollection only when each link is traceable to an approved real repair and matching graph edge; otherwise an empty collection with a reason. |
| `GET /api/exports` | Allowlisted artifacts, validation results, and download paths. |
| `GET /api/exports/{artifact_id}` | Streams one validated artifact from the fixed allowlist. |

API errors use the existing JSON form `{"error": "...", "code": "..."}`. An unavailable artifact is represented in the response; it is not replaced with a value from another stage.

## Evidence and freshness

Pipeline statuses are derived from the relevant reports, report-to-artifact relationships, and current artifact validation. Each stage has a `status` (`completed`, `partial`, `pending`, `unavailable`, or `failed`), explanation, repository-relative `input_artifacts`, `output_artifacts`, `source_report_paths`, optional graph SHA-256, timestamp and runtime, `missing_prerequisites`, limitations, and `evidence_kind`. Runtime fields include `runtime_seconds`, `runtime_status`, `runtime_source`, `runtime_evidence_kind`, and an explanation. The extraction runtime is a historical measured value from `data/raw/c2_sabaragamuwa_osm_metadata.json` (`extraction_total_seconds`), recorded by the extraction script's elapsed-time timer. Other stages return null with `runtime_status: "not_recorded"` because no trustworthy measured duration appears in their source artifacts. The API does not estimate runtimes. `evidence_kind` distinguishes historical artifact/report evidence from fresh computation. A stored report by itself does not establish successful output.

Graph metrics use NetworkX on the local GraphML as a directed `MultiDiGraph`, preserving parallel directed edges and keys. Fields include node count, directed edge count, weakly connected component count, and degree-1 count. Degree-1 means a node whose directed total degree (in-degree plus out-degree, counting parallel edge records) is exactly one. Validation includes GraphML readability, graph type, EPSG:4326 graph CRS, node IDs and coordinates, core `highway`/`length` edge attributes, and normalized direction metadata where required.

Graph SHA-256 hashes establish byte identity only. Equal counts do not establish identity; differing hashes do not establish structural difference. Structural equivalence is not computed. Graph counts and validation are fresh computations when the cache refreshes, with the source artifact path and hash in the comparison response. Historical reports remain identified as such.

The process caches a complete audit snapshot using each tracked artifact's size, modification time, and change time. A changed signature or process restart causes the audit to refresh. Refresh hashes GraphML files and parses one GraphML per distinct hash; identical files share graph metrics. No large graph is reparsed for each request while the signature remains unchanged. Filesystems that preserve all three signature values across content replacement could evade invalidation; restart the API after out-of-band artifact changes when in doubt.

## Semantic guard audit

Candidate outcome counts (`rejected`, `needs_review`, `eligible`) are recomputed from candidate records and checked against stored report counts. Per-guard output separates code implementation, endpoint attribute evidence and missing/unknown/ambiguous values, and explicitly recorded reason hits. Reason counts can overlap because a candidate can have multiple reasons.

The detector applies cross-endpoint compatibility rules to `layer`, `bridge`, `tunnel`, `access`, `motor_vehicle`, and `vehicle`. It records endpoint evidence and considers missing values and ambiguity in screening for all eight stored semantic attributes, including `highway` and `service`; those two are not cross-compared for compatibility. `oneway`/direction evidence comes from direction normalization rather than candidate endpoint semantic records. The audit exposes candidate IDs that explicitly record a missing attribute, conflict, or specific reason. The reports do not record passing decisions for every guard and candidate, so complete per-candidate guard outcomes are marked unreconstructable. Candidate records are screening evidence, not confirmed defects. The audit does not change candidate statuses, human reviews, approvals, or graph topology.

## Repair links

The empty response states: "No real repairs accepted; therefore there are no accepted real-data repair joins to assess." This does not establish zero invalid joins. Non-empty output requires consistent report counts, a matching candidate report hash, an explicit repository-local approval manifest, a deterministic repair ID, the corresponding repair-tagged edge in the current topology-repaired GraphML, and matching candidate, direction, reason, and edge attributes. Returned lines are derived straight endpoint geometry, not original OSM road geometry.

## Exports

Only the raw, cleaned, direction-normalized, and topology-repaired GraphML files and validated candidate GeoJSON are allowlisted. GraphML remains a directed multigraph with keyed parallel edges and edge attributes; node coordinates are EPSG:4326. The graph files do not preserve original edge geometries. Candidate GeoJSON is EPSG:4326 and its candidate connector lines are derived straight endpoint lines. Its export item exposes a `provenance` object: the stored candidate report hash, its recorded input path and graph counts, and the current source graph hash used for comparison. The provenance status is partial because the historical candidate report did not record the input graph's SHA-256, so its exact source bytes at generation time cannot be proved. Download routes accept artifact IDs only, never arbitrary filesystem paths. No repaired-road GeoJSON is offered without traceable approved repair evidence.

The API does not provide ground-truth Precision/Recall/F1, topology improvement, hazard risk scores, or locality boundaries. Zero accepted repairs is not evidence of topology correctness.
