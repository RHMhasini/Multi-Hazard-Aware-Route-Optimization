"""Export script generating fallback assets for preset queries.
Saves PNG map, PNG Pareto plot, and CSV table for:
1. Preset 1: Trade-off set (pareto_size >= 4, sweep_missed_count > 0)
2. Preset 2: Single Pareto route (pareto_size == 1, fastest == safest)
3. Preset 3: Closure case (reachable_after_exclusion == False)

Output Directory: outputs/component3/demo_snapshot/
"""

from __future__ import annotations

import sys
from pathlib import Path
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import networkx as nx

repo_root = Path(__file__).resolve().parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from src.route_optimization.config import DEFAULT_CONFIG
from src.route_optimization.synthetic import (
    generate_synthetic_grid_graph,
    generate_synthetic_hazard_raster,
)
from src.route_optimization.attachment import attach_hazard_to_graph
from src.route_optimization.graph_builder import build_routing_graph
from src.route_optimization.baselines import (
    compute_shortest_time_route,
    compute_risk_min_route,
    compute_shortest_distance_route,
    run_all_baselines,
)
from src.route_optimization.pareto import (
    pareto_sweep_weighted_sum,
    bi_criteria_label_setting,
    filter_dominated_routes,
)
from src.route_optimization.recommend import recommend_optimal_route
from src.route_optimization.scenarios import apply_hazard_exclusion, check_scenario_reachability
from src.route_optimization.metrics import calculate_route_metrics, compare_routes


SNAPSHOT_DIR = repo_root / "outputs" / "component3" / "demo_snapshot"
SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)


def export_preset_snapshot(preset_id: str, title: str, scenario: str, seed: int, origin: str, destination: str):
    print(f"Generating fallback snapshot for {preset_id}: {title}...")
    
    # 1. Build environment
    grid_graph = generate_synthetic_grid_graph(
        rows=6,
        cols=6,
        spacing_m=1850.0,
        base_lat=6.64,
        base_lon=80.36,
        seed=seed,
        region="synthetic",
    )
    hazard_data = generate_synthetic_hazard_raster(scenario_mode=scenario, seed=seed)
    attach_hazard_to_graph(grid_graph, hazard_data)
    routing_graph = build_routing_graph(grid_graph)

    # 2. Routes & Optimization
    baselines = run_all_baselines(routing_graph, origin, destination)
    sweep_pareto = pareto_sweep_weighted_sum(routing_graph, origin, destination, steps=21)
    label_pareto = bi_criteria_label_setting(routing_graph, origin, destination, max_labels_per_node=30)
    all_pareto = filter_dominated_routes(sweep_pareto + label_pareto)

    rec_result = recommend_optimal_route(
        routing_graph,
        all_pareto if all_pareto else [{"path": p, "algorithm": k} for k, p in baselines.items() if p],
        user_max_hazard=0.70,
        user_max_detour_ratio=1.50,
    )

    # 3. CSV Table Export
    table_rows = []
    # Identify unique routes
    route_map = {}
    if baselines.get("shortest_time"):
        route_map["Fastest"] = baselines["shortest_time"]
    if baselines.get("risk_minimal"):
        route_map["Safest"] = baselines["risk_minimal"]
    route_map["Recommended route (rule-based, pending supervisor approval)"] = rec_result.node_path

    for i, p_sol in enumerate(all_pareto):
        name = f"Pareto candidate {i+1} ({p_sol.get('algorithm', 'exact')})"
        if p_sol["path"] not in route_map.values():
            route_map[name] = p_sol["path"]

    fastest_time = calculate_route_metrics(routing_graph, baselines["shortest_time"]).total_travel_time_sec if baselines.get("shortest_time") else 1.0
    fastest_exp = calculate_route_metrics(routing_graph, baselines["shortest_time"]).cumulative_hazard_exposure if baselines.get("shortest_time") else 1.0

    pareto_paths = [tuple(p["path"]) for p in all_pareto]

    for label, path in route_map.items():
        m = calculate_route_metrics(routing_graph, path)
        t_min = m.total_travel_time_sec / 60.0
        d_km = m.total_distance_m / 1000.0
        time_penalty_pct = max(0.0, ((m.total_travel_time_sec - fastest_time) / max(1e-4, fastest_time)) * 100.0)
        exp_red_pct = max(0.0, ((fastest_exp - m.cumulative_hazard_exposure) / max(1e-4, fastest_exp)) * 100.0) if fastest_exp > 0 else 0.0
        is_p = "Y" if tuple(path) in pareto_paths else "N"

        table_rows.append({
            "route": label,
            "time (min)": round(t_min, 2),
            "distance (km)": round(d_km, 2),
            "exposure (hazard·m)": round(m.cumulative_hazard_exposure, 1),
            "max edge hazard": round(m.max_edge_hazard, 3),
            "time penalty % vs fastest": round(time_penalty_pct, 1),
            "exposure reduction % vs fastest": round(exp_red_pct, 1),
            "Pareto? (Y/N)": is_p
        })

    csv_df = pd.DataFrame(table_rows)
    csv_file = SNAPSHOT_DIR / f"{preset_id}_comparison_table.csv"
    csv_df.to_csv(csv_file, index=False)

    # 4. PNG Map (Matplotlib with plain background, no external tile servers)
    fig, ax = plt.subplots(figsize=(8, 7), dpi=150)
    ax.set_facecolor("#111827")
    fig.patch.set_facecolor("#0b0f19")

    # Draw Synthetic hazard layer raster contour / mesh
    raster = hazard_data["raster"]
    min_lon, min_lat, max_lon, max_lat = hazard_data["bounds"]
    extent = [min_lon, max_lon, min_lat, max_lat]
    im = ax.imshow(raster, extent=extent, origin="lower", cmap="YlOrRd", alpha=0.35, aspect="auto")
    cbar = plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cbar.set_label("hazard exposure index (0-1)", color="#9ca3af", fontsize=9)
    cbar.ax.yaxis.set_tick_params(color="#9ca3af")
    plt.setp(plt.getp(cbar.ax.axes, 'yticklabels'), color="#9ca3af")

    # Draw Synthetic road network edges coloured by hazard score
    for u, v, d in routing_graph.edges(data=True):
        ux, uy = routing_graph.nodes[u]["x"], routing_graph.nodes[u]["y"]
        vx, vy = routing_graph.nodes[v]["x"], routing_graph.nodes[v]["y"]
        hz = float(d.get("hazard_score", 0.0))
        ecol = "#ef4444" if hz >= 0.75 else "#4b5563"
        ax.plot([ux, vx], [uy, vy], color=ecol, lw=1.2, alpha=0.6, zorder=2)

    # Draw routes
    styles = {
        "Fastest": ("#3b82f6", 3.0, "-"),
        "Safest": ("#06b6d4", 3.0, "--"),
        "Recommended route (rule-based, pending supervisor approval)": ("#10b981", 4.5, "-"),
    }

    for r_name, r_path in route_map.items():
        col, lw, ls = styles.get(r_name, ("#a855f7", 2.0, ":"))
        xs = [routing_graph.nodes[n]["x"] for n in r_path]
        ys = [routing_graph.nodes[n]["y"] for n in r_path]
        ax.plot(xs, ys, color=col, lw=lw, ls=ls, label=r_name, zorder=5)

    # Mark origin and destination with neutral role-based labels
    ox, oy = routing_graph.nodes[origin]["x"], routing_graph.nodes[origin]["y"]
    dx, dy = routing_graph.nodes[destination]["x"], routing_graph.nodes[destination]["y"]
    orig_name = routing_graph.nodes[origin].get("name", origin)
    dest_name = routing_graph.nodes[destination].get("name", destination)
    ax.scatter([ox], [oy], color="#3b82f6", s=140, edgecolors="white", lw=2, zorder=10, label=f"Origin: {orig_name} ({origin})")
    ax.scatter([dx], [dy], color="#ef4444", s=140, edgecolors="white", lw=2, zorder=10, label=f"Destination: {dest_name} ({destination})")

    ax.set_title(
        f"Synthetic Road Network (Component 3)\n{title}\n{orig_name} -> {dest_name}\n"
        "Synthetic network and synthetic hazard. Place names are illustrative placeholders, not real locations.",
        color="white",
        fontsize=9,
        pad=12,
    )
    ax.set_xlabel("Longitude (deg E)", color="#9ca3af", fontsize=9)
    ax.set_ylabel("Latitude (deg N)", color="#9ca3af", fontsize=9)
    ax.tick_params(colors="#9ca3af")
    for spine in ax.spines.values():
        spine.set_color("#374151")
    ax.legend(loc="upper right", fontsize=7.5, facecolor="#1f2937", edgecolor="#374151", labelcolor="white")
    
    map_file = SNAPSHOT_DIR / f"{preset_id}_map.png"
    plt.tight_layout()
    plt.savefig(map_file, facecolor=fig.get_facecolor())
    plt.close()

    # 5. PNG Pareto Plot: time vs exposure
    fig_p, ax_p = plt.subplots(figsize=(7, 5), dpi=150)
    ax_p.set_facecolor("#111827")
    fig_p.patch.set_facecolor("#0b0f19")

    sweep_pts = {(round(s["travel_time"] / 60.0, 2), round(s["hazard_exposure"], 1)) for s in sweep_pareto}
    
    ls_x_exact, ls_y_exact = [], []
    ls_x_sweep, ls_y_sweep = [], []

    for sol in label_pareto:
        pt = (round(sol["travel_time"] / 60.0, 2), round(sol["hazard_exposure"], 1))
        if pt in sweep_pts:
            ls_x_sweep.append(pt[0])
            ls_y_sweep.append(pt[1])
        else:
            ls_x_exact.append(pt[0])
            ls_y_exact.append(pt[1])

    if ls_x_sweep:
        ax_p.scatter(ls_x_sweep, ls_y_sweep, color="#3b82f6", s=70, label="Exact (also found by sweep)", zorder=4)
    if ls_x_exact:
        ax_p.scatter(ls_x_exact, ls_y_exact, color="#f59e0b", marker="^", s=90, label="Exact-method-only (missed by sweep)", zorder=5)

    rec_pt = (rec_result.metrics.total_travel_time_sec / 60.0, rec_result.metrics.cumulative_hazard_exposure)
    ax_p.scatter([rec_pt[0]], [rec_pt[1]], color="#10b981", marker="*", s=220, edgecolors="white", lw=1.5,
                 label="Recommended route (rule-based, pending supervisor approval)", zorder=10)

    ax_p.set_title(
        f"Pareto Frontier: Travel Time vs Hazard Exposure\n{title}\n"
        "Synthetic network and synthetic hazard. Place names are illustrative placeholders, not real locations.",
        color="white",
        fontsize=9,
        pad=12,
    )
    ax_p.set_xlabel("Time (min)", color="#9ca3af", fontsize=9)
    ax_p.set_ylabel("Exposure (hazard·m)", color="#9ca3af", fontsize=9)
    ax_p.tick_params(colors="#9ca3af")
    for spine in ax_p.spines.values():
        spine.set_color("#374151")
    ax_p.grid(color="#374151", linestyle="--", linewidth=0.5, alpha=0.7)
    ax_p.legend(loc="upper right", fontsize=8, facecolor="#1f2937", edgecolor="#374151", labelcolor="white")
    
    # Caption required: points the sweep cannot find are exact-method-only
    fig_p.text(0.5, 0.01, "Note: points the sweep cannot find are exact-method-only.", ha="center", fontsize=8, color="#9ca3af", style="italic")

    pareto_file = SNAPSHOT_DIR / f"{preset_id}_pareto_plot.png"
    plt.tight_layout(rect=[0, 0.04, 1, 1])
    plt.savefig(pareto_file, facecolor=fig_p.get_facecolor())
    plt.close()

    print(f"  -> Successfully exported: {csv_file.name}, {map_file.name}, {pareto_file.name}")


def main():
    presets = [
        ("preset1_tradeoff", "Preset 1: Trade-off Set (Pareto >= 4, Sweep Missed)", "flood_dominant", 42, "node_4_1", "node_0_3"),
        ("preset2_single_pareto", "Preset 2: Single Pareto Route (Fastest = Safest)", "flood_dominant", 42, "node_4_1", "node_4_3"),
        ("preset3_closure", "Preset 3: Disaster Closure Scenario (Isolated / Severed)", "flood_dominant", 42, "node_0_3", "node_4_3"),
    ]
    for pid, title, scen, seed, orig, dest in presets:
        export_preset_snapshot(pid, title, scen, seed, orig, dest)
    print("\nAll fallback snapshot assets generated in outputs/component3/demo_snapshot/")


if __name__ == "__main__":
    main()
