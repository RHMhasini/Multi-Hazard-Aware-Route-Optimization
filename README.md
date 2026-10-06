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
6. **Topology Refinement & Healing** — Automated proximity-based healing, directional bridging, and layer/bridge/tunnel semantic preservation.
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
