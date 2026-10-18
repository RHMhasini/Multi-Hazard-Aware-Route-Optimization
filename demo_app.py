"""Streamlit Live Demo Application for Component 3.
Road-Risk Attachment and Disaster-Aware Route Optimization.

Designed specifically for final-year project panel presentation.
Runs 100% offline with zero external tile server dependencies.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import streamlit as st
import networkx as nx

# Ensure repo root is on sys.path
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
from src.route_optimization.scenarios import (
    apply_hazard_exclusion,
    check_scenario_reachability,
)
from src.route_optimization.metrics import calculate_route_metrics, compare_routes
from src.route_optimization.recommend import recommend_optimal_route
from src.route_optimization.acceptance_checks import run_acceptance_checks


# -----------------------------------------------------------------------------
# Page Configuration & Styling
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="Component 3 | Disaster-Aware Route Optimization",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom sleek dark-mode CSS
st.markdown(
    """
    <style>
    .reportview-container {
        background: #0b0f19;
    }
    .banner-warning {
        background: linear-gradient(90deg, rgba(239, 68, 68, 0.2), rgba(245, 158, 11, 0.2));
        border: 1px solid #f59e0b;
        color: #fef3c7;
        padding: 10px 16px;
        border-radius: 6px;
        font-weight: 600;
        font-size: 14px;
        margin-bottom: 16px;
        text-align: center;
    }
    .metric-box {
        background: #111827;
        border: 1px solid rgba(255, 255, 255, 0.1);
        border-radius: 8px;
        padding: 12px;
        margin-bottom: 10px;
    }
    .audit-pre {
        background: #111827;
        border: 1px solid #3b82f6;
        border-radius: 6px;
        padding: 12px;
        font-family: monospace;
        font-size: 12px;
        color: #93c5fd;
        white-space: pre-wrap;
    }
    .stTable > div {
        border-radius: 6px;
        overflow: hidden;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# -----------------------------------------------------------------------------
# Section A: Always-Visible Prototype Banner
# -----------------------------------------------------------------------------
st.markdown(
    '<div class="banner-warning">Prototype. Synthetic data. Components 1 and 2 integration pending.</div>',
    unsafe_allow_html=True,
)


# -----------------------------------------------------------------------------
# Cache Road Network & Hazard Data
# -----------------------------------------------------------------------------
@st.cache_resource(show_spinner=False)
def get_routing_graph(scenario_mode: str, seed: int):
    # Generates 6x6 directed grid graph
    grid_graph = generate_synthetic_grid_graph(
        rows=6,
        cols=6,
        spacing_m=1200.0,
        base_lat=6.64,
        base_lon=80.36,
        directed=True,
        seed=seed,
    )
    hazard_data = generate_synthetic_hazard_raster(
        min_lat=6.63,
        min_lon=80.35,
        max_lat=6.73,
        max_lon=80.46,
        grid_size=(80, 80),
        scenario_mode=scenario_mode,
        seed=seed,
    )
    attach_hazard_to_graph(grid_graph, hazard_data)
    routing_graph = build_routing_graph(grid_graph)
    return grid_graph, hazard_data, routing_graph


# Illustrative points pool (Honest labeling: illustrative points, not verified facility coordinates)
ILLUSTRATIVE_POINTS = {
    "Ratnapura General Hospital (illustrative)": (80.3620, 6.7020),
    "Kahawatta Relief Depot (illustrative)": (80.4550, 6.6450),
    "Elapatha Emergency Outpost (illustrative)": (80.3800, 6.6400),
    "Kuruwita Evacuation Center (illustrative)": (80.3700, 6.7200),
    "Pelmadulla Aid Station (illustrative)": (80.4500, 6.7100),
    "Central Valley Junction (illustrative)": (80.4000, 6.6800),
}


# -----------------------------------------------------------------------------
# Section B: Sidebar Controls
# -----------------------------------------------------------------------------
st.sidebar.title("Simulation Controls")

# Preset Query Selector (from experiment_results.csv)
preset_choice = st.sidebar.selectbox(
    "Load Preset Panel Query",
    [
        "Custom Selection",
        "Preset 1: Trade-off Set (Pareto >= 4, Sweep Missed)",
        "Preset 2: Single Pareto Route (Fastest = Safest)",
        "Preset 3: Disaster Closure Scenario (Isolated / Severed)",
    ],
    index=0,
)

if preset_choice == "Preset 1: Trade-off Set (Pareto >= 4, Sweep Missed)":
    default_scenario = "flood_dominant"
    default_seed = 42
    default_orig_node = "node_4_1"
    default_dest_node = "node_0_3"
    default_closure_active = False
elif preset_choice == "Preset 2: Single Pareto Route (Fastest = Safest)":
    default_scenario = "flood_dominant"
    default_seed = 42
    default_orig_node = "node_4_1"
    default_dest_node = "node_4_3"
    default_closure_active = False
elif preset_choice == "Preset 3: Disaster Closure Scenario (Isolated / Severed)":
    default_scenario = "flood_dominant"
    default_seed = 42
    default_orig_node = "node_0_3"
    default_dest_node = "node_4_3"
    default_closure_active = True
else:
    default_scenario = "mixed"
    default_seed = 42
    default_orig_node = None
    default_dest_node = None
    default_closure_active = False

scen_options = ["flood_dominant", "landslide_dominant", "mixed"]
scenario_mode = st.sidebar.selectbox(
    "Scenario",
    scen_options,
    index=scen_options.index(default_scenario),
)

seed = st.sidebar.number_input("Seed", min_value=1, max_value=9999, value=default_seed, step=1)

# Build graph
_, hazard_data, routing_graph = get_routing_graph(scenario_mode, seed)
node_list = sorted(list(routing_graph.nodes()))

st.sidebar.markdown("---")
st.sidebar.subheader("Origin & Destination")
st.sidebar.caption("Named places are illustrative points, not verified facility coordinates")

selection_mode = st.sidebar.radio("Select by", ["Illustrative Points", "Direct Graph Nodes"], index=0 if default_orig_node is None else 1)

if selection_mode == "Illustrative Points":
    pt_names = list(ILLUSTRATIVE_POINTS.keys())
    orig_pt_name = st.sidebar.selectbox("Origin Location", pt_names, index=0)
    dest_pt_name = st.sidebar.selectbox("Destination Location", pt_names, index=1)

    orig_coord = ILLUSTRATIVE_POINTS[orig_pt_name]
    dest_coord = ILLUSTRATIVE_POINTS[dest_pt_name]

    # Snap coordinates
    snap_info = snap_od_coordinates(
        routing_graph,
        origin_coord=orig_coord,
        destination_coord=dest_coord,
        max_distance_m=10000.0,
        require_connected=True,
    )
    origin_node = snap_info["origin_node"]
    dest_node = snap_info["destination_node"]
    orig_snap_dist = snap_info["origin_dist_m"]
    dest_snap_dist = snap_info["destination_dist_m"]

    # Snapping distance indicators and warnings
    st.sidebar.info(f"Origin snapped: **{origin_node}** ({orig_snap_dist:.1f} m)")
    if orig_snap_dist > 1000.0:
        st.sidebar.warning(f"⚠️ Origin snapping distance exceeds 1 km ({orig_snap_dist/1000:.2f} km)!")

    st.sidebar.info(f"Destination snapped: **{dest_node}** ({dest_snap_dist:.1f} m)")
    if dest_snap_dist > 1000.0:
        st.sidebar.warning(f"⚠️ Destination snapping distance exceeds 1 km ({dest_snap_dist/1000:.2f} km)!")

else:
    o_idx = node_list.index(default_orig_node) if (default_orig_node and default_orig_node in node_list) else 0
    d_idx = node_list.index(default_dest_node) if (default_dest_node and default_dest_node in node_list) else min(len(node_list)-1, 35)
    origin_node = st.sidebar.selectbox("Origin Node", node_list, index=o_idx)
    dest_node = st.sidebar.selectbox("Destination Node", node_list, index=d_idx)
    orig_coord = (routing_graph.nodes[origin_node]["x"], routing_graph.nodes[origin_node]["y"])
    dest_coord = (routing_graph.nodes[dest_node]["x"], routing_graph.nodes[dest_node]["y"])
    orig_snap_dist = 0.0
    dest_snap_dist = 0.0

st.sidebar.markdown("---")
st.sidebar.subheader("Optimization Parameters")
hazard_ceiling = st.sidebar.slider("Hazard Ceiling", min_value=0.10, max_value=0.95, value=0.70, step=0.05)
detour_budget = st.sidebar.slider("Detour Budget (Max Detour Ratio)", min_value=1.05, max_value=2.00, value=1.50, step=0.05)

st.sidebar.markdown("---")
st.sidebar.subheader("Disaster Closure Settings")
closure_enabled = st.sidebar.checkbox("Enable Hazard Closure Exclusion", value=default_closure_active)
closure_threshold = st.sidebar.slider("Closure Threshold (R_e >=)", min_value=0.50, max_value=0.95, value=0.75, step=0.05)


# -----------------------------------------------------------------------------
# Compute Routing Solutions via Core Modules
# -----------------------------------------------------------------------------
# 1. Baselines
baselines = run_all_baselines(routing_graph, origin_node, dest_node)

# 2. Pareto Frontier (Sweep + Label Setting)
sweep_pareto = pareto_sweep_weighted_sum(routing_graph, origin_node, dest_node, steps=21)
label_pareto = bi_criteria_label_setting(routing_graph, origin_node, dest_node, max_labels_per_node=30)
all_pareto = filter_dominated_routes(sweep_pareto + label_pareto)

# 3. Recommendation
candidate_pool = all_pareto if all_pareto else [{"path": p, "algorithm": k} for k, p in baselines.items() if p]
recommended_result = recommend_optimal_route(
    routing_graph,
    candidate_pool,
    user_max_hazard=hazard_ceiling,
    user_max_detour_ratio=detour_budget,
)

# Startup acceptance checks printed to terminal (one-time per session state)
if "acceptance_checked" not in st.session_state:
    st.session_state.acceptance_checked = True
    run_acceptance_checks(routing_graph, all_pareto)


# -----------------------------------------------------------------------------
# Main Header & Layout
# -----------------------------------------------------------------------------
st.title("Component 3: Disaster-Aware Route Optimization")
st.caption(
    "Road-Risk Attachment and Disaster-Aware Bi-Criteria Route Optimization | Ratnapura District Study Region"
)

col_map, col_pareto = st.columns([1.1, 0.9])

# -----------------------------------------------------------------------------
# Section C: Map (Plain background, offline, roads coloured by hazard)
# -----------------------------------------------------------------------------
with col_map:
    st.subheader("Synthetic road network & Synthetic hazard layer")

    fig_map, ax_map = plt.subplots(figsize=(7.5, 6.8), dpi=140)
    fig_map.patch.set_facecolor("#0b0f19")
    ax_map.set_facecolor("#111827")

    # Draw hazard raster
    raster = hazard_data["raster"]
    min_lon, min_lat, max_lon, max_lat = hazard_data["bounds"]
    extent = [min_lon, max_lon, min_lat, max_lat]
    im = ax_map.imshow(raster, extent=extent, origin="lower", cmap="YlOrRd", alpha=0.35, aspect="auto")
    cbar = plt.colorbar(im, ax=ax_map, fraction=0.046, pad=0.04)
    cbar.set_label("hazard exposure index (0-1)", color="#9ca3af", fontsize=8)
    cbar.ax.yaxis.set_tick_params(color="#9ca3af")
    plt.setp(plt.getp(cbar.ax.axes, 'yticklabels'), color="#9ca3af")

    # Draw Road Network Edges
    for u, v, d in routing_graph.edges(data=True):
        ux, uy = routing_graph.nodes[u]["x"], routing_graph.nodes[u]["y"]
        vx, vy = routing_graph.nodes[v]["x"], routing_graph.nodes[v]["y"]
        hz = float(d.get("hazard_score", 0.0))
        ecol = "#ef4444" if hz >= closure_threshold else "#4b5563"
        ax_map.plot([ux, vx], [uy, vy], color=ecol, lw=1.2, alpha=0.6, zorder=2)

    # Compile routes
    route_display_map = {}
    if baselines.get("shortest_time"):
        route_display_map["Fastest"] = baselines["shortest_time"]
    if baselines.get("risk_minimal"):
        route_display_map["Safest"] = baselines["risk_minimal"]
    route_display_map["Recommended route (rule-based, pending supervisor approval)"] = recommended_result.node_path

    # Other non-dominated Pareto routes
    for idx, p_sol in enumerate(all_pareto):
        label = f"Pareto candidate {idx+1} ({p_sol.get('algorithm', 'exact')})"
        if p_sol["path"] not in route_display_map.values():
            route_display_map[label] = p_sol["path"]

    styles = {
        "Fastest": ("#3b82f6", 2.8, "-"),
        "Safest": ("#06b6d4", 2.8, "--"),
        "Recommended route (rule-based, pending supervisor approval)": ("#10b981", 4.2, "-"),
    }

    for r_name, r_path in route_display_map.items():
        col, lw, ls = styles.get(r_name, ("#a855f7", 1.8, ":"))
        xs = [routing_graph.nodes[n]["x"] for n in r_path]
        ys = [routing_graph.nodes[n]["y"] for n in r_path]
        ax_map.plot(xs, ys, color=col, lw=lw, ls=ls, label=r_name, zorder=5)

    # Origin and Destination Points
    ox, oy = routing_graph.nodes[origin_node]["x"], routing_graph.nodes[origin_node]["y"]
    dx, dy = routing_graph.nodes[dest_node]["x"], routing_graph.nodes[dest_node]["y"]
    ax_map.scatter([ox], [oy], color="#3b82f6", s=130, edgecolors="white", lw=2, zorder=10, label=f"Origin: {origin_node}")
    ax_map.scatter([dx], [dy], color="#ef4444", s=130, edgecolors="white", lw=2, zorder=10, label=f"Destination: {dest_node}")

    ax_map.set_xlabel("Longitude (deg E)", color="#9ca3af", fontsize=9)
    ax_map.set_ylabel("Latitude (deg N)", color="#9ca3af", fontsize=9)
    ax_map.tick_params(colors="#9ca3af")
    for spine in ax_map.spines.values():
        spine.set_color("#374151")
    ax_map.legend(loc="upper right", fontsize=7.5, facecolor="#1f2937", edgecolor="#374151", labelcolor="white")

    plt.tight_layout()
    st.pyplot(fig_map)
    st.caption("Map rendered completely offline with synthetic grid graph and hazard surface attachment.")


# -----------------------------------------------------------------------------
# Section E: Pareto Plot: Time vs Hazard Exposure
# -----------------------------------------------------------------------------
with col_pareto:
    st.subheader("Bi-Criteria Pareto Frontier: min [T(p), R(p)]")

    fig_p, ax_p = plt.subplots(figsize=(6.5, 6.0), dpi=140)
    fig_p.patch.set_facecolor("#0b0f19")
    ax_p.set_facecolor("#111827")

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

    rec_t = recommended_result.metrics.total_travel_time_sec / 60.0
    rec_r = recommended_result.metrics.cumulative_hazard_exposure
    ax_p.scatter([rec_t], [rec_r], color="#10b981", marker="*", s=230, edgecolors="white", lw=1.5,
                 label="Recommended route (rule-based, pending supervisor approval)", zorder=10)

    ax_p.set_xlabel("Travel Time (minutes)", color="#9ca3af", fontsize=9)
    ax_p.set_ylabel("Cumulative Hazard Exposure (hazard·metres)", color="#9ca3af", fontsize=9)
    ax_p.tick_params(colors="#9ca3af")
    for spine in ax_p.spines.values():
        spine.set_color("#374151")
    ax_p.grid(color="#374151", linestyle="--", linewidth=0.5, alpha=0.7)
    ax_p.legend(loc="upper right", fontsize=8, facecolor="#1f2937", edgecolor="#374151", labelcolor="white")

    plt.tight_layout()
    st.pyplot(fig_p)
    # Required caption E: points the sweep cannot find are exact-method-only
    st.caption("Note: points the sweep cannot find are exact-method-only.")


# -----------------------------------------------------------------------------
# Section D: Comparison Table
# -----------------------------------------------------------------------------
st.markdown("---")
st.subheader("Route Alternatives Comparison Table")

fastest_path = baselines.get("shortest_time")
fastest_m = calculate_route_metrics(routing_graph, fastest_path) if fastest_path else None
fastest_t = fastest_m.total_travel_time_sec if fastest_m else 1.0
fastest_exp = fastest_m.cumulative_hazard_exposure if fastest_m else 1.0

pareto_node_tuples = {tuple(p["path"]) for p in all_pareto}

comp_rows = []
for label, path in route_display_map.items():
    m = calculate_route_metrics(routing_graph, path)
    t_min = m.total_travel_time_sec / 60.0
    d_km = m.total_distance_m / 1000.0
    time_penalty_pct = max(0.0, ((m.total_travel_time_sec - fastest_t) / max(1e-4, fastest_t)) * 100.0)
    exp_red_pct = max(0.0, ((fastest_exp - m.cumulative_hazard_exposure) / max(1e-4, fastest_exp)) * 100.0) if fastest_exp > 0 else 0.0
    is_pareto = "Y" if tuple(path) in pareto_node_tuples else "N"

    comp_rows.append({
        "route": label,
        "time (min)": round(t_min, 2),
        "distance (km)": round(d_km, 2),
        "exposure (hazard·m)": round(m.cumulative_hazard_exposure, 1),
        "max edge hazard": round(m.max_edge_hazard, 3),
        "time penalty % vs fastest": round(time_penalty_pct, 1),
        "exposure reduction % vs fastest": round(exp_red_pct, 1),
        "Pareto? (Y/N)": is_pareto,
    })

comp_df = pd.DataFrame(comp_rows)
st.dataframe(comp_df, use_container_width=True)


# -----------------------------------------------------------------------------
# Section F: Recommendation Explanation
# -----------------------------------------------------------------------------
st.markdown("---")
st.subheader("Recommendation Explanation")
st.markdown(f'<div class="audit-pre">{recommended_result.audit_notes}</div>', unsafe_allow_html=True)


# -----------------------------------------------------------------------------
# Section G: Disaster Closure Scenario (when toggled)
# -----------------------------------------------------------------------------
if closure_enabled:
    st.markdown("---")
    st.subheader(f"Disaster Closure Scenario (Hazard Exclusion Threshold R_e >= {closure_threshold:.2f})")

    pruned_graph = apply_hazard_exclusion(routing_graph, hazard_threshold=closure_threshold, inplace=False)
    is_reachable = check_scenario_reachability(pruned_graph, origin_node, dest_node)

    if is_reachable:
        st.success(f"Network Status: **REACHABLE**. Detour path remains available between {origin_node} and {dest_node} post-closure.")
    else:
        # Diagnostic check for origin, destination, or network severing
        orig_deg = pruned_graph.out_degree(origin_node) if origin_node in pruned_graph else 0
        dest_deg = pruned_graph.in_degree(dest_node) if dest_node in pruned_graph else 0

        if orig_deg == 0 and dest_deg == 0:
            diag_msg = f"Both origin ({origin_node}) and destination ({dest_node}) are isolated with zero incident edges."
        elif orig_deg == 0:
            diag_msg = f"Origin node ({origin_node}) is isolated: all outbound road segments exceed threshold {closure_threshold:.2f}."
        elif dest_deg == 0:
            diag_msg = f"Destination node ({dest_node}) is isolated: all inbound road segments exceed threshold {closure_threshold:.2f}."
        else:
            diag_msg = f"Network is severed: road segments between origin ({origin_node}) and destination ({dest_node}) are partitioned into disconnected components."

        st.error(f"Network Status: **NOT REACHABLE**. {diag_msg}")


# -----------------------------------------------------------------------------
# Section H: Experiment Summary (computed from CSV, not hard-coded)
# -----------------------------------------------------------------------------
st.markdown("---")
st.subheader("Research Experiment Summary")
st.caption("Computed dynamically from outputs/component3/experiment_results.csv (Unit of analysis: synthetic Origin-Destination query)")

csv_path = repo_root / "outputs" / "component3" / "experiment_results.csv"
if csv_path.exists():
    exp_df = pd.read_csv(csv_path)

    summary_rows = []
    for scen_name, group in exp_df.groupby("scenario"):
        total_q = len(group)
        single_p_share = (group["pareto_size"] == 1).mean() * 100.0
        tradeoff_g = group[group["pareto_size"] > 1]
        mean_exp_red = tradeoff_g["exposure_reduction_pct"].mean() if len(tradeoff_g) > 0 else 0.0
        mean_time_det = tradeoff_g["time_detour_pct"].mean() if len(tradeoff_g) > 0 else 0.0
        sweep_miss_share = (group["sweep_missed_count"] > 0).mean() * 100.0
        infeas_closure_share = (group["reachable_after_exclusion"] == 0).mean() * 100.0

        summary_rows.append({
            "Scenario": scen_name,
            "Total Queries": total_q,
            "Single Pareto Route (%)": round(single_p_share, 1),
            "Trade-off Mean Exposure Reduction (%)": round(mean_exp_red, 1),
            "Trade-off Mean Time Detour (%)": round(mean_time_det, 1),
            "Sweep Missed Pareto Route (%)": round(sweep_miss_share, 1),
            "Infeasible Under Closure (%)": round(infeas_closure_share, 1),
        })

    summary_df = pd.DataFrame(summary_rows)
    st.table(summary_df)
    st.caption("Based on 480 synthetic experiment runs (40 OD pairs × 3 scenarios × 4 seeds).")
else:
    st.warning("outputs/component3/experiment_results.csv not found.")


# -----------------------------------------------------------------------------
# Section I: Status Panel Mapped to 16-Week Plan
# -----------------------------------------------------------------------------
st.markdown("---")
st.subheader("Project Status & Milestone Alignment")

col_status1, col_status2, col_status3 = st.columns(3)

with col_status1:
    st.markdown("### Done (50% Milestone: End of Week 8)")
    st.markdown(
        """
        - Road-risk spatial attachment formula ($R_e = \max M(x)$)
        - Bi-criteria Pareto frontier solver (Sweep + Label Setting)
        - Pareto dominance filtering & baseline benchmark suite
        - Auditable Utopian Knee-Point route recommendation rule
        - Disaster scenario edge exclusion & network reachability
        - GeoJSON export pipeline for Component 4 dashboard
        """
    )

with col_status2:
    st.markdown("### In Progress (Weeks 9 - 12)")
    st.markdown(
        """
        - Dynamic edge travel speed degradation modeling
        - Supervisor policy tuning on utopian distance weights
        - Automated real-time re-routing under cascading closures
        - Component 4 emergency dispatch API schema integration
        """
    )

with col_status3:
    st.markdown("### Pending (Weeks 13 - 16)")
    st.markdown(
        """
        - Real Component 1 raster integration (Ratnapura live hazards)
        - Real Component 2 OSM network graph ingestion
        - Historical flood & landslide disaster event validation
        - Final defense viva, thesis report, and system handover
        """
    )
