#!/usr/bin/env python
"""Component 2 Function 1: detect possible topology-defect candidates."""

from __future__ import annotations

import argparse
from pathlib import Path

import importlib.util

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = PROJECT_ROOT / "src" / "road_network" / "c2_topology_detection.py"
SPEC = importlib.util.spec_from_file_location("c2_topology_detection", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise ImportError("Unable to load the topology-detection module.")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

INPUT_GRAPH = PROJECT_ROOT / "data/intermediate/c2_sabaragamuwa_direction_normalized_graph.graphml"
REPORT_JSON = PROJECT_ROOT / "outputs/component2_evaluation/c2_topology_candidates.json"
REPORT_MARKDOWN = PROJECT_ROOT / "outputs/component2_evaluation/c2_topology_candidates.md"
GEOJSON = PROJECT_ROOT / "data/intermediate/c2_topology_candidates.geojson"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=INPUT_GRAPH)
    parser.add_argument("--distance-m", type=float, default=MODULE.DEFAULT_SEARCH_DISTANCE_M)
    parser.add_argument(
        "--max-heading-deviation-degrees",
        type=float,
        default=MODULE.DEFAULT_MAX_HEADING_DEVIATION_DEGREES,
    )
    parser.add_argument("--report-json", type=Path, default=REPORT_JSON)
    parser.add_argument("--report-md", type=Path, default=REPORT_MARKDOWN)
    parser.add_argument("--geojson", type=Path, default=GEOJSON)
    args = parser.parse_args()
    report = MODULE.detect_file(
        args.input,
        args.report_json,
        args.report_md,
        args.geojson,
        args.distance_m,
        args.max_heading_deviation_degrees,
    )
    print(report["counts"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
