# Multi-Hazard-Aware Emergency Logistics Route Optimization
## Component 2 — Road Network Graph Construction / Automated Road-Network Topology Refinement

### Project Overview
This project is part of a larger research initiative: **Multi-Hazard-Aware Emergency Logistics Route Optimization for Sabaragamuwa Province**. The wider project focuses on delivering resilient routing solutions under multi-hazard emergency conditions (e.g., landslides and floods).

### Component 2 Responsibilities
Component 2 is specifically responsible for constructing and refining a robust, routing-ready directed road graph from OpenStreetMap (OSM) data. Its primary role is to extract geospatial road network data, resolve topological defects, ensure valid network connectivity, and export clean graph representations for downstream routing modules.

> **Note on Component Boundary:** Multi-hazard risk calculation belongs strictly to **Component 1** and is **not** implemented in Component 2.

### Planned Pipeline
The planned high-level processing pipeline consists of:

1. **OSM Extraction** — Downloading and parsing OpenStreetMap road network data.
2. **Road Filtering & Cleaning** — Isolating drivable ways and pruning non-routable segments.
3. **Direction Normalization** — Standardizing one-way, two-way, and roundabout lane orientations.
4. **Initial Graph Construction** — Assembling a Directed Multigraph (`networkx.MultiDiGraph`).
5. **Topology Defect Detection & Inspection** — Identifying disconnected components, dangling nodes, and angular anomalies.
6. **Topology Refinement & Healing** — Conservative, approval-gated directed connector repairs with layer/bridge/tunnel semantic preservation; proximity alone never authorizes a repair.

Function 2 adds only an explicitly approved directed connector. A synthetic connector
must provide string-valued `layer`, `bridge`, `tunnel`, `oneway_normalized`, and
`direction_source` attributes. The direction source must be
`component2_explicit_approval`; OSM identity fields such as `osmid`, `highway`,
road names, and physical geometry are not invented. Candidate evidence and the
exact candidate-report file SHA-256 are retained in repair provenance. Unknown,
duplicate, malformed, rejected, or unapproved manifest entries are never applied.
7. **Validation & Evaluation** — Quantifying network graph metrics and structural correctness.
8. **Graph Export** — Exporting routing-ready artifacts in GraphML and GeoJSON formats.

*(Note: The algorithms and pipelines outlined above represent the planned research methodology; implementation is carried out progressively across project steps.)*

### Project Structure
```text
road_network_graph/
├── src/
│   └── road_network/       # Core source package
├── scripts/                # Utility and verification scripts
├── tests/                  # Unit and integration tests
├── data/
│   ├── raw/                # Raw geospatial data
│   ├── intermediate/       # Filtered / transformed data
│   └── processed/          # Cleaned graph data
├── outputs/
│   ├── graphs/             # GraphML exports
│   ├── geojson/            # GeoJSON geometries
│   ├── maps/               # Visualizations and renders
│   └── evaluation/         # Metrics and evaluation reports
├── docs/                   # Documentation
├── notebooks/              # Exploration and research notebooks
├── .gitignore
├── README.md
└── requirements.txt
```

### Environment Setup
1. Requires **Python 3.13.x**.
2. Create and activate a virtual environment (`.venv`).
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Verify environment:
   ```bash
   python scripts/verify_environment.py
   ```

### Frontend Environment

The React frontend is isolated under `frontend/` and does not share dependencies
with the Python backend. It uses React, TypeScript, Vite, Tailwind CSS, and
Leaflet/React-Leaflet for the future map surface:

```bash
cd frontend
npm install
npm run dev
```

Run the read-only Component 2 API from the repository root in a second terminal
using the project virtual environment. Start it from this checkout so its
source-relative data paths resolve to this repository:

```bash
\.venv\Scripts\python.exe scripts\c2_api.py
```

The API serves `/api/health`, `/api/summary`, `/api/boundaries`,
`/api/candidates`, `/api/reviews`, and bounded
`/api/roads?bbox=minLon,minLat,maxLon,maxLat`
requests. Roads are derived from
the direction-normalized OSM graph using real endpoint coordinates; the full
GraphML file is never sent to the browser. The frontend displays candidate
evidence and repair counts from the existing reports, and cannot approve or
apply repairs. The API rejects malformed or overly broad road requests.
The normalized GraphML road network is a local generated artifact and is not
included in a fresh checkout. The API and frontend can start using the commands
above, but road requests need
`data/intermediate/c2_sabaragamuwa_direction_normalized_graph.graphml`. The
raw PBF cache is also excluded; provide it at `cache/sri-lanka-latest.osm.pbf`
before running the extraction and graph-processing scripts to recreate the
network.

The dashboard initially selects Ratnapura and Kegalle. District road and
candidate requests use the verified polygons in
`data/raw/c2_sabaragamuwa_district_boundaries.geojson`, while the existing
Sabaragamuwa Province boundary remains the maximum study-area limit. The local
dataset is a reproducible two-district subset of geoBoundaries `gbOpen LKA
ADM2`, retrieved on 2026-10-13 from the pinned
[source GeoJSON](https://github.com/wmgeolab/geoBoundaries/raw/9469f09/releaseData/gbOpen/LKA/ADM2/geoBoundaries-LKA-ADM2.geojson).
It is attributed to geoBoundaries, with boundary source OpenStreetMap /
Wambacher, boundary vintage 2017, and CRS EPSG:4326. Only these two district
polygons are included for this prototype; the boundaries are administrative
reference data and may not reflect later revisions.
The geoBoundaries gbOpen release is provided under [CC BY 4.0](https://github.com/wmgeolab/geoBoundaries/blob/9469f09/LICENSE).
The listed OpenStreetMap source data is available under the [ODbL 1.0](https://www.openstreetmap.org/copyright).
Credit geoBoundaries and OpenStreetMap / Wambacher when reusing these boundaries.
The unchanged province polygon was obtained through the project's OSM Nominatim
geocoding step; its extraction date is recorded as 2026-10-07 in the OSM
metadata file. It uses CRS84 coordinates and OpenStreetMap attribution.

The map fits the authoritative province boundary on load and masks the
surrounding basemap in the browser using a polygon with the province exterior
rings as holes. This is a visual mask: the Leaflet tile layer may still request
tiles outside the province viewport, and the source raster tiles are not
server-clipped. Road and candidate vector geometries are intersected with the
province and selected district polygons before they are returned by the API.

### Component 2 candidate review workflow

`outputs/component2_evaluation/c2_topology_candidate_reviews.json` is a separate,
versioned human-review dataset for the candidates in
`c2_topology_candidates.json`. The initial artifact contains one record for each
of the 40 candidates; all records are explicitly `unreviewed` with a null
classification. It records the source report's exact-byte SHA-256 and preserves
each candidate's automated status. It does not change the automated candidate
report.

To initialize a new review dataset for a candidate report, use
`python -m src.road_network.c2_candidate_review --init --candidate-report
<path> --output <new-path>`. Initialization refuses to overwrite an existing
file. To validate an edited dataset against its source report, use
`python -m src.road_network.c2_candidate_review --validate --candidate-report
<path> --output <review-path>`. Keep the source candidate report unchanged while
reviewing; any byte change invalidates the stored source hash and requires
reinitializing or explicitly reconciling the review dataset.

Each review record retains the stable candidate ID and original automated
status separately from `review_state` and the optional human `classification`
(`likely_gap`, `legitimate_endpoint`, or `uncertain`). Unreviewed records use a
null classification. A reviewed record must include a non-empty decision,
supporting evidence entries (`evidence_type`, `description`, `reference`), and
recorded limitations. Reviewer ID and ISO 8601 review time are optional and are
never filled by the system. The validation command rejects missing/duplicate
candidate records, altered automated statuses, malformed evidence, invalid
classifications, and source-report hash mismatches.

The read-only dashboard endpoint `GET /api/reviews` validates and serves these
records. Selecting a candidate connection displays its automated status and
human review state separately, along with available evidence and limitations.
Review records are annotations only: there is no review editing route, no
conversion to repair approvals, and no graph mutation. A `likely_gap` label is
not a confirmed defect or authorization to repair. The current dataset contains
no independent review decisions; researchers must add them based on recorded
evidence before using it as a ground-truth evaluation set.
