# Component 2 Topology Candidate Detection

## Interpretation
These are possible topology-defect candidates, not confirmed defects or repairs.

## Input and graph statistics
- Input: `D:\SLIIT\4th year 1st semester\Research\PP1\road_network_graph\data\intermediate\c2_sabaragamuwa_direction_normalized_graph.graphml`
- Graph: `MultiDiGraph`; directed=True; multigraph=True
- Nodes / directed edge records: 297581 / 599077

## Algorithm and configuration
- Endpoint definition: An endpoint is a node with exactly one unique undirected neighbor after collapsing directed and parallel edge records.
- Search distance: 20.0 m
- Maximum exploratory heading deviation: 45.0 degrees
- CRS transformation: EPSG:4326 -> EPSG:32644 for distance; GeoJSON exported as EPSG:4326
- CRS validation: validated (EPSG:4326)
- Coordinate validation: validated; unprocessable nodes: 0
- Calibration status: exploratory; not empirically calibrated

## Candidate counts
- Endpoints: 8106
- Nearby endpoint pairs before redundancy exclusion: 47
- Candidate pairs: 40
- Redundant endpoint pairs excluded: 7
- Eligible: 0
- Rejected: 34
- Needs review: 6

## Reasons
- `{'access': 38, 'bridge': 39, 'bridge_context_requires_review': 1, 'heading_incompatible': 34, 'layer': 39, 'layer_context_requires_review': 1, 'missing_semantic_attributes': 40, 'motor_vehicle': 36, 'service': 38, 'tunnel': 40, 'vehicle': 40}`
- Missing semantic attributes: `{'access': 38, 'bridge': 39, 'layer': 39, 'motor_vehicle': 36, 'service': 38, 'tunnel': 40, 'vehicle': 40}`

## Limitations and next validation steps
- Candidates are possible topology issues, not confirmed defects.
- A degree-one endpoint may be a legitimate cul-de-sac, road end, or boundary artifact.
- No graph mutation, snapping, node movement, edge creation, deletion, or direction change is performed.
- Thresholds are exploratory and require calibration against independently validated reference cases.
- Edge geometry is not retained in the source GraphML; headings use endpoint-to-neighbor coordinate vectors.
- Eligible status means only that the configured screening rules found no conflict; it is not repair approval.
- Semantic conflicts are conservative screening exclusions, not proof that a physical connection is impossible.

- Review candidates against source OSM geometry and imagery.
- Calibrate distance and heading thresholds using labelled reference cases.
- Evaluate candidate ranking with independently validated topology cases before any repair stage.
