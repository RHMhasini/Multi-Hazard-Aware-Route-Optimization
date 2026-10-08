# Component 2 Step 4 Road Cleaning and Validation

## Decision summary
Validation-only cleaning produced an unchanged structural copy; no clearly invalid records were found.

## Records inspected and retained
- Nodes inspected/valid: 297581 / 297581
- Edges inspected/valid: 599077 / 599077
- Records removed: 0 nodes, 0 edges
- Structure: MultiDiGraph, directed=True, multigraph=True

## Validation findings
- Invalid node coordinates: 0
- Invalid edge references: 0
- Length counts: `{'valid': 598898, 'very_short': 179}`
- Missing highway: 0; unexpected: `{}`
- Semantic attributes preserved: `['highway', 'oneway', 'oneway_osm', 'junction', 'bridge', 'tunnel', 'layer', 'access', 'motor_vehicle', 'vehicle', 'service', 'maxspeed', 'lanes', 'ref', 'name', 'length']`

## Boundary and synthetic-node investigation
- Boundary: CRS EPSG:4326, valid=True, features=1
- Outside nodes: 113 (113 synthetic, 0 positive OSM)
- Not-fully-covered edges: 225; with synthetic endpoint: 225
- Synthetic negative nodes: 218; isolated synthetic nodes: 0
Boundary observations are retained. Outside nodes are predominantly synthetic clipping nodes; not-fully-covered edges are reported for later spatial/topology review and are not removed in Step 4.

## Connectivity and unusual records
- Weak/strong components: 196 / 222
- Component size range: 2 to 293043; components <=10 nodes: 115
- Retained unusual observations: `['degree-one endpoints', 'sub-metre edges', 'disconnected components', 'boundary-adjacent and boundary-crossing observations', 'all valid bridge/tunnel/layer/access/service semantics']`

## Later-stage observations
- Boundary coverage observations require later spatial review.
- Very short edges require later topology analysis.
- Disconnected components require contextual review, not size-only deletion.

## Scope statement
No Step 5 direction normalization, Step 6 topology detection, Dmerge/angular analysis, snapping, bridging, topology healing, risk calculation, route optimization, or Component 3 processing was performed.
