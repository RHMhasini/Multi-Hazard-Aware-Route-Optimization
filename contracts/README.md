# Component 3 Input & Output Contracts

This package specifies the strict data exchange contracts between components in the Multi-Hazard-Aware Route Optimization platform.

## Architecture & Data Flow

```
┌─────────────────────────────────┐       ┌─────────────────────────────────┐
│           Component 1           │       │           Component 2           │
│    Hazard Susceptibility Model  │       │   Road Network Construction     │
│   (Flood / Landslide Rasters)   │       │   (Cleaned Directed GraphML)    │
└────────────────┬────────────────┘       └────────────────┬────────────────┘
                 │                                         │
                 │   [C1HazardRasterContract]              │   [C2GraphContract]
                 └───────────────────┬─────────────────────┘
                                     │
                                     ▼
                       ┌───────────────────────────┐
                       │        Component 3        │
                       │     Route Optimization    │
                       │  - Hazard Attachment      │
                       │  - Graph Building         │
                       │  - Pareto & Baselines     │
                       │  - Evaluation & Recs      │
                       └─────────────┬─────────────┘
                                     │
                                     ▼
                        [C3RouteResultContract]
                        [C3EvaluationMetricsContract]
```

## Contract Definitions

### 1. Component 1: Hazard Raster Contract (`C1HazardRasterContract`)
- **`hazard_type`**: Type string (e.g. `flood`, `landslide`, `multi_hazard`).
- **`crs`**: Coordinate Reference System (`EPSG:4326`).
- **`bounds`**: Bounding box tuple `(min_lon, min_lat, max_lon, max_lat)`.
- **`resolution`**: Cell dimensions `(pixel_width_deg, pixel_height_deg)`.
- **`dimensions`**: Grid shape `(height_rows, width_cols)`.
- **`value_range`**: Normalized float interval `[0.0, 1.0]`, where 0.0 indicates no risk and 1.0 indicates critical risk.

### 2. Component 2: Road Network Graph Contract (`C2GraphContract`)
- **Format**: NetworkX `MultiDiGraph` or `DiGraph` serialized to GraphML.
- **`crs`**: `EPSG:4326` (WGS84).
- **Node Attributes**:
  - `x`: Node longitude (float).
  - `y`: Node latitude (float).
- **Edge Attributes**:
  - `length`: Edge length in meters (float).
  - `highway`: OSM highway classification (e.g. `primary`, `secondary`, `residential`).
  - `oneway`: Boolean directionality constraint.

### 3. Component 3: Route Optimization Output (`C3RouteResultContract`)
- **`route_id`**: Unique identifier for the planned route.
- **`algorithm`**: Strategy used (`shortest_time`, `shortest_distance`, `risk_min`, `weighted_sum`, `pareto_frontier`).
- **`node_path`**: Ordered list of traversal node IDs `[node_1, node_2, ..., node_n]`.
- **`metrics`** (`C3EvaluationMetricsContract`):
  - `total_travel_time_sec`: Time in seconds under standard speed profiles.
  - `total_distance_m`: Metric path length.
  - `cumulative_hazard_exposure`: $\sum \text{length}_e \times \text{hazard}_e$ or $\sum \text{time}_e \times \text{hazard}_e$.
  - `max_edge_hazard`: $\max_{e \in \text{route}} \text{hazard}_e \in [0.0, 1.0]$.
  - `threshold_penalty`: Penalty score if edges exceed configured hazard thresholds.
  - `is_pareto_optimal`: Boolean flag indicating if route is non-dominated in the bi-objective (time vs. exposure) space.
- **`audit_notes`**: Transparent rationale explaining trade-offs and recommendation decisions.
