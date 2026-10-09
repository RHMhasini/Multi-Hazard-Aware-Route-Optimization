#!/usr/bin/env python
"""Component 2 Step 5: validate and canonicalize existing OSM direction semantics."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = PROJECT_ROOT / "src/road_network/c2_direction_normalization.py"
MODULE_SPEC = importlib.util.spec_from_file_location("c2_direction_normalization", MODULE_PATH)
if MODULE_SPEC is None or MODULE_SPEC.loader is None:
    raise ImportError("Unable to load the Step 5 direction-normalization module.")
MODULE = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(MODULE)
normalize_file = MODULE.normalize_file
INPUT_GRAPH = PROJECT_ROOT / "data/intermediate/c2_sabaragamuwa_cleaned_graph.graphml"
OUTPUT_GRAPH = PROJECT_ROOT / "data/intermediate/c2_sabaragamuwa_direction_normalized_graph.graphml"
REPORT_JSON = PROJECT_ROOT / "outputs/component2_evaluation/c2_direction_normalization_report.json"
REPORT_MD = PROJECT_ROOT / "outputs/component2_evaluation/c2_direction_normalization_report.md"


def _relative(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _markdown(report: dict) -> str:
    direction = report["direction"]
    preservation = report["preservation"]
    return "\n".join(
        [
            "# Component 2 Step 5 Direction Normalization",
            "",
            "## Decision",
            report["normalization_decision"],
            "",
            "## Input and output",
            f"- Input: `{report['input']}`",
            f"- Output: `{report['output']}`",
            "",
            "## Direction statistics",
            f"- Directed edges before/after: {direction['directed_edges_before']} / {direction['directed_edges_after']}",
            f"- Directed edge records classified one-way: {direction['directed_edge_records_one_way']}",
            f"- Directed edge records classified bidirectional: {direction['directed_edge_records_bidirectional']}",
            f"- OSM-way statistics: `{direction['osm_way_statistics']}`",
            f"- Reverse (`oneway=-1`) cases: {direction['reverse_direction_cases']}",
            f"- Roundabout records: {direction['roundabout_records']}",
            f"- Missing/unspecified `oneway` records: {direction['missing_oneway_records']}",
            f"- Directed edge records retained without structural modification: {direction['edges_retained_without_structural_modification']}",
            f"- Directed edge records with classified metadata: {direction['direction_metadata_classified']}",
            f"- Direction semantics independently verified: {direction['direction_semantics_independently_verified']}",
            f"- Directed edge records not independently verified: {direction['direction_semantics_unverified_records']}",
            f"- Verification limitation: {direction['verification_limitation']}",
            f"- Normalized direction counts: `{direction['normalized_direction_counts']}`",
            "",
            "## Rules",
            "- `yes`, `true`, and `1` are classified as forward one-way records.",
            "- `-1` is classified as reverse relative to the original OSM way; extraction had already stored its routing direction.",
            "- `no` is classified as bidirectional.",
            "- Missing `oneway` is bidirectional unless `junction=roundabout`, which follows the extraction default of forward.",
            "- `oneway_osm` is the extracted GraphML-safe representation of the OSM source tag; missing values were normalized to empty strings during extraction and it is not a lossless raw-tag audit field.",
            "- Original topology and semantic attributes are preserved.",
            "",
            "## Preservation validation",
            f"- Node IDs and node attributes preserved: {preservation['same_node_ids'] and preservation['same_node_attributes']}",
            f"- Edge keys and original attributes preserved: {preservation['same_edge_keys'] and preservation['same_original_edge_attributes']}",
            f"- MultiDiGraph/directed structure preserved: {preservation['graph_type_preserved'] and preservation['directed_preserved'] and preservation['multigraph_preserved']}",
            f"- Topology changed: {preservation['topology_changed']}",
            "",
            "## Scope",
            "No node merging, snapping, bridging, topology healing, suspicious-topology detection, Dmerge/angular analysis, risk calculation, route optimization, or Component 3 processing was performed.",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=INPUT_GRAPH)
    parser.add_argument("--output", type=Path, default=OUTPUT_GRAPH)
    parser.add_argument("--report-json", type=Path, default=REPORT_JSON)
    parser.add_argument("--report-md", type=Path, default=REPORT_MD)
    args = parser.parse_args()
    report = normalize_file(args.input, args.output)
    report["input"] = _relative(args.input)
    report["output"] = _relative(args.output)
    args.report_json.parent.mkdir(parents=True, exist_ok=True)
    args.report_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    args.report_md.write_text(_markdown(report), encoding="utf-8")
    print(json.dumps(report["direction"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
