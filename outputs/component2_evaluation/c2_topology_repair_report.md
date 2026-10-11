# Component 2 Topology Repair

## Interpretation
Repairs are conservative, explicit graph edits. Candidate proximity is not proof of a defect.

## Inputs and policy
- Input graph: `D:\SLIIT\4th year 1st semester\Research\PP1\road_network_graph\data\intermediate\c2_sabaragamuwa_direction_normalized_graph.graphml`
- Candidate report: `D:\SLIIT\4th year 1st semester\Research\PP1\road_network_graph\outputs\component2_evaluation\c2_topology_candidates.json`
- Approval manifest: `None`
- Output graph: `D:\SLIIT\4th year 1st semester\Research\PP1\road_network_graph\data\intermediate\c2_sabaragamuwa_topology_repaired_graph.graphml`
- Operation: add one explicitly approved directed connector edge
- Approval gate: Candidate status must be eligible, explicitly approved, not already connected, heading-compatible, and free of missing or conflicting semantic evidence.
- Direction policy: The approval manifest must name exactly one direction; reverse edges are never inferred or added.
- Candidate report SHA-256: `e0e178caf8a31af10bd830955299de1e7555a4d431a0a637168257870e67cdf3`

## Counts
- Candidates: 40
- Explicit approvals supplied: 0
- Accepted repairs: 0
- Skipped candidates: 40
- Manifest entries: 0
- Valid approvals: 0
- Rejected approvals: 0
- Real-data repairs: 0
- Real-data repair occurred: False

## Graph statistics
- Before: `{'node_count': 297581, 'edge_count': 599077, 'weak_component_count': 196, 'strong_component_count': 222}`
- After: `{'node_count': 297581, 'edge_count': 599077, 'weak_component_count': 196, 'strong_component_count': 222}`

## Accepted repairs
- None.

## Skipped candidates
- `candidate-0f70100b8e0714d3`: not_explicitly_approved
- `candidate-1074caa167fa8fc1`: not_explicitly_approved
- `candidate-18a0c27d46f3cabd`: not_explicitly_approved
- `candidate-1d14efe6a5c28d64`: not_explicitly_approved
- `candidate-24c41e0363ee2e9b`: not_explicitly_approved
- `candidate-26118467dead2980`: not_explicitly_approved
- `candidate-304d37da74e6710c`: not_explicitly_approved
- `candidate-387721c7724fb553`: not_explicitly_approved
- `candidate-4088aa20ed75d188`: not_explicitly_approved
- `candidate-42211c0bdb44e4fd`: not_explicitly_approved
- `candidate-44b58c53b9775589`: not_explicitly_approved
- `candidate-4574c5d87f3a9dae`: not_explicitly_approved
- `candidate-490a9fa4c46b6838`: not_explicitly_approved
- `candidate-4ba327be27970d7b`: not_explicitly_approved
- `candidate-50b35f904a6c5c7e`: not_explicitly_approved
- `candidate-53ee47d9b6e6b8e6`: not_explicitly_approved
- `candidate-5e547e00d5a1e3ad`: not_explicitly_approved
- `candidate-678977a7c88a7b2b`: not_explicitly_approved
- `candidate-69729ef945ef029b`: not_explicitly_approved
- `candidate-6c6e233ae24da57a`: not_explicitly_approved
- `candidate-74d4f652cf38ec98`: not_explicitly_approved
- `candidate-7c9f674524090dd6`: not_explicitly_approved
- `candidate-7d97bf36d9b91ad0`: not_explicitly_approved
- `candidate-7f53bfcc1ffb6020`: not_explicitly_approved
- `candidate-845ae7eb617a8722`: not_explicitly_approved
- `candidate-86857aafd01774a0`: not_explicitly_approved
- `candidate-8f33660321875be2`: not_explicitly_approved
- `candidate-99f1184259d6cb87`: not_explicitly_approved
- `candidate-9c95b85a49179601`: not_explicitly_approved
- `candidate-9e29d69b6bad5283`: not_explicitly_approved
- `candidate-a196d02daee34f7d`: not_explicitly_approved
- `candidate-a7b4083a52d4a012`: not_explicitly_approved
- `candidate-aa0097db3a370d9f`: not_explicitly_approved
- `candidate-b16ebc0b46fde81e`: not_explicitly_approved
- `candidate-ca70a9675ece166b`: not_explicitly_approved
- `candidate-d04a8de730415b92`: not_explicitly_approved
- `candidate-d8d49f0fbae385b6`: not_explicitly_approved
- `candidate-e3771c35e8764d33`: not_explicitly_approved
- `candidate-ec9afd28afba9f64`: not_explicitly_approved
- `candidate-fb32c3156d1da78b`: not_explicitly_approved

## Rejected approvals
- None.

## Limitations
- No node merging, snapping, coordinate movement, reverse-edge inference, or semantic conflict override is performed.
- An accepted synthetic repair is an explicitly approved graph edit, not evidence of a real topology defect.
- Real-data repair requires independently reviewed approvals; the default study-area run supplies none.

## Provenance
- Source graph mutated: False
- Source graph preserved: True
