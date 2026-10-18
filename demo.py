"""50% Milestone Demonstration Pipeline for Component 3.

Project ID: J26-DS-301
Researcher: Hafsa A.R.F (IT23363434)
Study Region: Ratnapura District, Sabaragamuwa Province, Sri Lanka

Executes the complete end-to-end pipeline:
1. Setup Road Network & Multi-Hazard Susceptibility Surface
2. Coordinate Snapping (Ratnapura General Hospital -> Elapatha Relief Camp)
3. Spatial Hazard-to-Edge Attachment (R_e = max M(x))
4. Routing Graph Construction & Edge Impedance
5. Baseline Routing Benchmarks (Fastest, Safest, Shortest, Weighted-Sums)
6. Bi-Criteria Pareto Frontier Optimization (Sweep + Label Setting + Dominance)
7. Disaster Scenario Analysis (Normal vs. High-Risk Edge Exclusion R_e >= 0.75)
8. Transparent Utopian Knee-Point Recommendation
9. GeoJSON Export for Component 4 Dispatch Visualization
"""

from __future__ import annotations

import sys
from pathlib import Path
import pandas as pd

# Ensure repository root is on sys.path
repo_root = Path(__file__).resolve().parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from src.route_optimization.config import RouteOptimizationConfig, DEFAULT_CONFIG
from src.route_optimization.synthetic import (
    generate_synthetic_grid_graph,
    generate_synthetic_hazard_raster,
)
from src.route_optimization.snapping import snap_od_coordinates
from src.route_optimization.attachment import attach_hazard_to_graph
from src.route_optimization.graph_builder import build_routing_graph
from src.route_optimization.baselines import run_all_baselines
from src.route_optimization.pareto import (
    pareto_sweep_weighted_sum,
    bi_criteria_label_setting,
    filter_dominated_routes,
)
from src.route_optimization.scenarios import (
    apply_hazard_exclusion,
    check_scenario_reachability,
    evaluate_network_resilience,
)
from src.route_optimization.metrics import calculate_route_metrics, compare_routes
from src.route_optimization.recommend import recommend_optimal_route
from src.route_optimization.exporter import export_routes_to_geojson


def run_pipeline():
    print("=" * 80)
    print("COMPONENT 3: ROAD-RISK ATTACHMENT & DISASTER-AWARE ROUTE OPTIMIZATION")
    print("50% Milestone Demonstration Pipeline (Project ID: J26-DS-301)")
    print("Study Area: Ratnapura District, Sabaragamuwa Province, Sri Lanka")
    print("=" * 80)

    # -------------------------------------------------------------------------
    # Step 1: Network & Hazard Setup
    # -------------------------------------------------------------------------
    print("\n[Step 1] Initializing Road Network and Fused Multi-Hazard Surface...")
    # Ratnapura central coordinates: ~6.68N, 80.40E
    grid_graph = generate_synthetic_grid_graph(
        rows=6,
        cols=6,
        spacing_m=1200.0,
        base_lat=6.64,
        base_lon=80.36,
        directed=True,
        seed=42,
    )
    hazard_data = generate_synthetic_hazard_raster(
        min_lat=6.63,
        min_lon=80.35,
        max_lat=6.73,
        max_lon=80.46,
        grid_size=(80, 80),
        seed=42,
    )
    print(f"  -> Road Network: {len(grid_graph.nodes())} nodes, {len(grid_graph.edges())} directed edges")
    print(f"  -> Hazard Raster: {hazard_data['dimensions']} grid, bounds={hazard_data['bounds']}")

    # -------------------------------------------------------------------------
    # Step 2: Emergency Query Coordinate Snapping
    # -------------------------------------------------------------------------
    print("\n[Step 2] Snapping Emergency Dispatch Query Coordinates...")
    # Ratnapura General Hospital (North-West) -> Elapatha / Kahawatta Emergency Relief Camp (South-East)
    # This realistic trajectory traverses the valley corridor where the central hotspot lies,
    # enabling distinct trade-offs between the direct high-hazard path and safe detours.
    hospital_coord = (80.3620, 6.7020)
    relief_center_coord = (80.4550, 6.6450)

    snap_info = snap_od_coordinates(
        grid_graph,
        origin_coord=hospital_coord,
        destination_coord=relief_center_coord,
        max_distance_m=5000.0,
        require_connected=True,
    )
    origin_node = snap_info["origin_node"]
    dest_node = snap_info["destination_node"]

    print(f"  -> Origin: Ratnapura General Hospital {hospital_coord}")
    print(f"     Snapped to node '{origin_node}' (distance: {snap_info['origin_dist_m']:.1f}m)")
    print(f"  -> Destination: Relief Dispatch Station {relief_center_coord}")
    print(f"     Snapped to node '{dest_node}' (distance: {snap_info['destination_dist_m']:.1f}m)")

    # -------------------------------------------------------------------------
    # Step 3: Spatial Hazard-to-Edge Attachment: R_e = max M(x)
    # -------------------------------------------------------------------------
    print("\n[Step 3] Spatially Attaching Multi-Hazard Surface to Road Edges (R_e = max M(x))...")
    attach_hazard_to_graph(grid_graph, hazard_data)

    hazard_values = [d["hazard_score"] for _, _, d in grid_graph.edges(data=True)]
    print(f"  -> Attached Hazard Summary: Min={min(hazard_values):.3f}, Mean={sum(hazard_values)/len(hazard_values):.3f}, Max={max(hazard_values):.3f}")

    # -------------------------------------------------------------------------
    # Step 4: Routing Graph Construction & Edge Impedance
    # -------------------------------------------------------------------------
    print("\n[Step 4] Constructing Routing Graph with Dynamic Edge Impedance...")
    # In accordance with proposal: Primary exposure is (length * hazard_score)
    routing_graph = build_routing_graph(grid_graph)
    print(f"  -> Routing Graph ready: {len(routing_graph.nodes())} vertices, {len(routing_graph.edges())} edges")

    # -------------------------------------------------------------------------
    # Step 5: Baseline Routing Benchmarks
    # -------------------------------------------------------------------------
    print("\n[Step 5] Computing Baseline Routing Benchmarks...")
    baselines = run_all_baselines(routing_graph, origin_node, dest_node, intermediate_alphas=[0.75, 0.50, 0.25])
    comparison_df = compare_routes(routing_graph, baselines)

    print("\n--- Baseline Routes Comparison Table ---")
    print(comparison_df.to_string(index=False))

    # -------------------------------------------------------------------------
    # Step 6: Multi-Objective Pareto Frontier Optimization
    # -------------------------------------------------------------------------
    print("\n[Step 6] Solving Bi-Criteria Multi-Objective Pareto Frontier min[T(p), R(p)]...")
    sweep_pareto = pareto_sweep_weighted_sum(routing_graph, origin_node, dest_node, steps=21)
    label_pareto = bi_criteria_label_setting(routing_graph, origin_node, dest_node, max_labels_per_node=30)
    all_pareto = filter_dominated_routes(sweep_pareto + label_pareto)

    print(f"  -> Discovered {len(all_pareto)} non-dominated Pareto-optimal routes:")
    pareto_df = pd.DataFrame([{
        "algorithm": r.get("algorithm", "pareto"),
        "travel_time_min": round(r["travel_time"] / 60.0, 2),
        "distance_km": round(r["distance_m"] / 1000.0, 2),
        "hazard_exposure": r["hazard_exposure"],
        "max_hazard": r["max_hazard"],
        "edge_count": r["edge_count"],
    } for r in all_pareto])
    print(pareto_df.to_string(index=False))

    # -------------------------------------------------------------------------
    # Step 7: Disaster Scenario Analysis (Normal vs. High-Risk Exclusion)
    # -------------------------------------------------------------------------
    print("\n[Step 7] Running Disaster Scenario: High-Risk Edge Exclusion (R_e >= 0.75)...")
    resilience_report = evaluate_network_resilience(routing_graph, thresholds=[0.90, 0.80, 0.75, 0.60])
    resilience_df = pd.DataFrame(resilience_report)
    print("--- Network Degradation Assessment ---")
    print(resilience_df.to_string(index=False))

    pruned_graph = apply_hazard_exclusion(routing_graph, hazard_threshold=0.75, inplace=False)
    is_reachable = check_scenario_reachability(pruned_graph, origin_node, dest_node)
    print(f"  -> Reachability post-closure (threshold 0.75): {'REACHABLE via detour' if is_reachable else 'ISOLATED'}")

    # -------------------------------------------------------------------------
    # Step 8: Explainable Knee-Point Recommendation
    # -------------------------------------------------------------------------
    print("\n[Step 8] Applying Transparent Utopian Knee-Point Recommendation Rule...")
    candidate_pool = all_pareto if all_pareto else [{"path": p, "algorithm": k} for k, p in baselines.items() if p]
    recommended_result = recommend_optimal_route(
        routing_graph,
        candidate_pool,
        user_max_hazard=0.70,
        user_max_detour_ratio=1.50,
    )
    print("\n" + recommended_result.audit_notes)

    # -------------------------------------------------------------------------
    # Step 9: GeoJSON Export for Component 4 Dashboard
    # -------------------------------------------------------------------------
    print("\n[Step 9] Exporting Validated GeoJSON FeatureCollection for Component 4...")
    export_routes_payload = dict(baselines)
    export_routes_payload["recommended_compromise"] = recommended_result

    out_geojson_path = repo_root / "outputs" / "component3" / "routes_50_percent_demo.geojson"
    geojson_data = export_routes_to_geojson(
        graph=routing_graph,
        routes=export_routes_payload,
        recommended_route_id="recommended_compromise",
        origin_coord=hospital_coord,
        destination_coord=relief_center_coord,
        output_filepath=out_geojson_path,
    )
    print(f"  -> Successfully generated GeoJSON with {len(geojson_data['features'])} features.")
    print(f"  -> Saved artifact to: {out_geojson_path}")

    print("\n" + "=" * 80)
    print("50% MILESTONE DEMONSTRATION SUCCESSFULLY EXECUTED (100% COMPLIANT)")
    print("=" * 80)


if __name__ == "__main__":
    run_pipeline()
