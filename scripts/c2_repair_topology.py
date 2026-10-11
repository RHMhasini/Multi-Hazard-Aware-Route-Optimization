#!/usr/bin/env python
"""Run Component 2's explicit-approval topology repair stage."""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = PROJECT_ROOT / "src" / "road_network" / "c2_topology_repair.py"
SPEC = importlib.util.spec_from_file_location("c2_topology_repair", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise ImportError("Unable to load the topology-repair module.")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

INPUT_GRAPH = PROJECT_ROOT / "data/intermediate/c2_sabaragamuwa_direction_normalized_graph.graphml"
CANDIDATES = PROJECT_ROOT / "outputs/component2_evaluation/c2_topology_candidates.json"
OUTPUT_GRAPH = PROJECT_ROOT / "data/intermediate/c2_sabaragamuwa_topology_repaired_graph.graphml"
REPORT_JSON = PROJECT_ROOT / "outputs/component2_evaluation/c2_topology_repair_report.json"
REPORT_MD = PROJECT_ROOT / "outputs/component2_evaluation/c2_topology_repair_report.md"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=INPUT_GRAPH)
    parser.add_argument("--candidates", type=Path, default=CANDIDATES)
    parser.add_argument("--approvals", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=OUTPUT_GRAPH)
    parser.add_argument("--report-json", type=Path, default=REPORT_JSON)
    parser.add_argument("--report-md", type=Path, default=REPORT_MD)
    parser.add_argument(
        "--real-data",
        action="store_true",
        help="Mark the run as the real study-area run; approvals are still required.",
    )
    args = parser.parse_args()
    report = MODULE.repair_file(
        args.input,
        args.candidates,
        args.output,
        args.report_json,
        args.report_md,
        args.approvals,
        real_data_run=args.real_data,
    )
    print(report["counts"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
