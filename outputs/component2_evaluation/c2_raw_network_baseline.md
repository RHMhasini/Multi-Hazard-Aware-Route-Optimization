# Component 2 Step 3 Raw Network Baseline

## 1. Dataset information
- Study area: Sabaragamuwa Province, Sri Lanka
- Raw graph: `data\raw\c2_sabaragamuwa_raw_drive_network.graphml` (379592640 bytes)
- Raw extraction metadata status: `success`

## 2. Graph structure
- Class: `MultiDiGraph`; directed: `True`; multigraph: `True`
- Nodes: 297581; directed edges: 599077
- Weak components: 196; strong components: 222
- OSMnx validation: `{'performed': True, 'graph_type': 'MultiDiGraph', 'node_count': 297581, 'edge_count': 599077, 'is_directed': True}`

## 3. Node statistics
- Degree min/max/mean/median: 1 / 10 / 4.026312 / 4
- Degree 0/1/2/3/4/>4: 0 / 7 / 8778 / 88 / 276337 / 12371

## 4. Edge statistics
- Self-loops: 0; parallel statistics: `{'node_pair_count': 599077, 'parallel_node_pair_count': 0, 'edges_in_parallel_groups': 0, 'maximum_edges_per_node_pair': 1, 'multiplicity_distribution': {'1': 599077}, 'directed_pair_multiplicity_distribution': {'1': 599077}}`
- Length statistics: `{'count': 599077, 'minimum': 0.08802206075090219, 'maximum': 3659.727284363393, 'mean': 29.133524227401228, 'median': 21.59256742990773}`

## 5. Road-class distribution
- motorway: 10 (0.001669%)
- motorway_link: 4 (0.000668%)
- trunk: 25870 (4.31831%)
- trunk_link: 27 (0.004507%)
- primary: 57777 (9.644336%)
- primary_link: 86 (0.014355%)
- secondary: 9123 (1.522843%)
- secondary_link: 2 (0.000334%)
- tertiary: 60776 (10.14494%)
- tertiary_link: 106 (0.017694%)
- unclassified: 201099 (33.568139%)
- residential: 217344 (36.27981%)
- living_street: 6472 (1.080329%)
- service: 20381 (3.402067%)
- road: 0 (0.0%)
- Additional highway values: `[]`

## 6. Direction and semantic attributes
- oneway: `{'False': 598100, 'True': 977}`
- oneway_osm: `{'': 516761, 'no': 81506, 'yes': 810}`
- junction: `{'': 598839, 'roundabout': 238}`
- bridge: `{'': 596456, 'construction': 28, 'covered': 4, 'low_water_crossing': 6, 'no': 987, 'viaduct': 80, 'yes': 1516}`
- tunnel: `{'': 599067, 'no': 4, 'yes': 6}`
- layer: `{'': 596280, '-1': 284, '0': 343, '1': 2170}`
- access: `{'': 515479, 'no': 349, 'permissive': 276, 'private': 418, 'unknown': 276, 'yes': 82279}`
- motor_vehicle: `{'': 515811, 'designated': 14, 'no': 168, 'unknown': 22, 'yes': 83062}`
- vehicle: `{'': 599077}`
- service: `{'': 597383, 'alley': 776, 'driveway': 874, 'epalapitiya': 14, 'parking_aisle': 30}`

## 7. Spatial validation
- Boundary CRS: `EPSG:4326`; valid: `True`; features: 1
- Node bounds: `[80.1461703, 6.228130568776175, 80.9514508, 7.396209825699008]`; nodes outside boundary: 113
- Edges intersecting boundary: 599077; edges not covered: 225
- Negative node IDs: 218; all are clipping candidates: True

## 8. Connectivity statistics
- Weak components: 196; strong components: 222
- Component-size summary: `{'weak_min': 2, 'weak_max': 293043, 'weak_components_at_most_10_nodes': 115}`

## 9. Candidate topology indicators
- Degree-one candidates: 7; isolated candidates: 0
- Very short edges (<1 m): 179; high-degree (>=20) nodes: 0
- Nearby endpoint candidates: `{'available': False, 'reason': 'scipy is not installed'}`
- These are baseline observations, not confirmed topology defects.

## 10. Known limitations
- Degree-one nodes may be legitimate endpoints, cul-de-sacs, access roads, or digitization candidates.
- No Dmerge or angular thresholds were selected.
- No topology modification, snapping, bridging, merging, deletion, or simplification was performed.

## 11. Processing statement
No topology modification was performed. The raw GraphML, PBF, and boundary geometry were read only.
