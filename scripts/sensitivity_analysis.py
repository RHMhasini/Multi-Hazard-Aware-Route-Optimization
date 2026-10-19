"""
Sensitivity analysis for topology detection thresholds.

Tests proximity snapping distances (5–30 m) and angular thresholds (30–60°)
to empirically validate the chosen 20 m / 45° defaults.

Run from repo root:
    .venv\Scripts\python.exe -m scripts.sensitivity_analysis
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import networkx as nx

from src.road_network.c2_topology_detection import detect_candidates

GRAPHML = ROOT / "data" / "intermediate" / "c2_sabaragamuwa_direction_normalized_graph.graphml"

DISTANCES_M   = [5, 10, 15, 20, 25, 30]
ANGULAR_FIXED = 45.0   # keep angular fixed while varying distance

ANGLES_DEG      = [30, 35, 40, 45, 50, 55, 60]
DISTANCE_FIXED  = 20.0  # keep distance fixed while varying angle


def wcc_stats(graph: nx.MultiDiGraph) -> tuple[int, float]:
    """Return (component_count, largest_component_pct)."""
    wccs = list(nx.weakly_connected_components(graph))
    if not wccs:
        return 0, 0.0
    largest = max(len(c) for c in wccs)
    total = graph.number_of_nodes()
    return len(wccs), round(largest / total * 100, 2)


def count_by_status(report: dict) -> dict:
    counts = {"eligible": 0, "needs_review": 0, "rejected": 0}
    for c in report.get("candidates", []):
        s = c.get("status", "rejected")
        counts[s] = counts.get(s, 0) + 1
    return counts


def run_distance_sweep(graph: nx.MultiDiGraph) -> None:
    base_components, base_largest_pct = wcc_stats(graph)
    total_nodes = graph.number_of_nodes()

    print("\n" + "=" * 80)
    print("PART 1 — Proximity distance sweep  (angular threshold fixed at 45°)")
    print("=" * 80)
    print(f"{'Dist(m)':>8} | {'Candidates':>10} | {'Eligible':>8} | {'NeedsRev':>8} | {'Rejected':>8} | {'WCC':>5} | {'Largest CC%':>11}")
    print("-" * 80)

    for dist in DISTANCES_M:
        report, _ = detect_candidates(
            graph,
            search_distance_m=dist,
            max_heading_deviation_degrees=ANGULAR_FIXED,
        )
        total_cands = len(report.get("candidates", []))
        sc = count_by_status(report)
        n_components, largest_pct = wcc_stats(graph)
        marker = " ◄ chosen" if dist == 20 else ""
        print(
            f"{dist:>8} | {total_cands:>10} | {sc['eligible']:>8} | {sc['needs_review']:>8} | "
            f"{sc['rejected']:>8} | {n_components:>5} | {largest_pct:>10.2f}%{marker}"
        )

    print(f"\n  Baseline WCC count : {base_components}")
    print(f"  Total graph nodes  : {total_nodes:,}")
    print()


def run_angular_sweep(graph: nx.MultiDiGraph) -> None:
    print("=" * 80)
    print("PART 2 — Angular threshold sweep  (distance fixed at 20 m)")
    print("=" * 80)
    print(f"{'Angle(°)':>9} | {'Candidates':>10} | {'Eligible':>8} | {'NeedsRev':>8} | {'Rejected':>8}")
    print("-" * 60)

    for angle in ANGLES_DEG:
        report, _ = detect_candidates(
            graph,
            search_distance_m=DISTANCE_FIXED,
            max_heading_deviation_degrees=angle,
        )
        total_cands = len(report.get("candidates", []))
        sc = count_by_status(report)
        marker = " ◄ chosen" if angle == 45 else ""
        print(
            f"{angle:>9} | {total_cands:>10} | {sc['eligible']:>8} | {sc['needs_review']:>8} | "
            f"{sc['rejected']:>8}{marker}"
        )
    print()


def main() -> None:
    print(f"Loading graph from:\n  {GRAPHML}\n")
    graph: nx.MultiDiGraph = nx.read_graphml(GRAPHML, force_multigraph=True)
    print(f"  {graph.number_of_nodes():,} nodes, {graph.number_of_edges():,} edges\n")

    run_distance_sweep(graph)
    run_angular_sweep(graph)

    print("=" * 80)
    print("Interpretation guide")
    print("=" * 80)
    print("  Candidates  — total endpoint pairs within threshold (proximity + angular pass)")
    print("  Eligible    — passed ALL semantic checks (repair-ready if approved)")
    print("  NeedsRev    — passed proximity+angular but ambiguous semantics")
    print("  Rejected    — failed heading or semantic conflict check")
    print("  WCC         — weakly connected components (lower = better connectivity)")
    print("  Largest CC% — % of nodes in main component (higher = better)")
    print()
    print("  Optimal threshold = point where Candidates stabilise AND Largest CC% peaks.")
    print("  Beyond that point extra candidates are likely false positives.")
    print("=" * 80)


if __name__ == "__main__":
    main()
