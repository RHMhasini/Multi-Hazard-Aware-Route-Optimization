"""FastAPI Web Application server for Component 3 Multi-Hazard-Aware Route Optimization.
Provides API endpoints to:
1. Serve the UI dashboard and static files
2. Load existing demo benchmark GeoJSON and experimental data
3. Execute real-time routing simulations with configurable origin/destinations, hazard thresholds, and objective weights
4. Retrieve Pareto frontier comparisons and resilience degradation metrics
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Set repo root in path
REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

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

app = FastAPI(
    title="Component 3: Disaster-Aware Route Optimization Dashboard",
    description="Multi-Hazard-Aware Route Optimization for Emergency Response (Ratnapura, Sri Lanka)",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Cache shared demo environment
CACHED_ENV: Dict[str, Any] = {}


def get_or_create_routing_environment():
    if "routing_graph" in CACHED_ENV:
        return CACHED_ENV

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
    attach_hazard_to_graph(grid_graph, hazard_data)
    routing_graph = build_routing_graph(grid_graph)

    # Format nodes for frontend
    nodes_summary = []
    for node_id, data in routing_graph.nodes(data=True):
        nodes_summary.append({
            "id": str(node_id),
            "lon": float(data.get("x", 0.0)),
            "lat": float(data.get("y", 0.0)),
        })

    # Pre-extract road network edges with hazard info
    edges_geojson = {
        "type": "FeatureCollection",
        "features": []
    }
    for u, v, d in routing_graph.edges(data=True):
        u_node = routing_graph.nodes[u]
        v_node = routing_graph.nodes[v]
        edges_geojson["features"].append({
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [
                    [round(float(u_node["x"]), 6), round(float(u_node["y"]), 6)],
                    [round(float(v_node["x"]), 6), round(float(v_node["y"]), 6)]
                ]
            },
            "properties": {
                "source": str(u),
                "target": str(v),
                "hazard_score": round(float(d.get("hazard_score", 0.0)), 3),
                "length_m": round(float(d.get("length", 0.0)), 1),
                "travel_time_s": round(float(d.get("travel_time", 0.0)), 1),
                "highway": d.get("highway", "primary")
            }
        })

    CACHED_ENV["grid_graph"] = grid_graph
    CACHED_ENV["hazard_data"] = hazard_data
    CACHED_ENV["routing_graph"] = routing_graph
    CACHED_ENV["nodes_summary"] = nodes_summary
    CACHED_ENV["edges_geojson"] = edges_geojson

    return CACHED_ENV


class RouteSimulationRequest(BaseModel):
    origin_lon: float = 80.3620
    origin_lat: float = 6.7020
    dest_lon: float = 80.4550
    dest_lat: float = 6.6450
    max_hazard_threshold: float = 0.70
    max_detour_ratio: float = 1.50
    hazard_exclusion_threshold: Optional[float] = 0.75


@app.get("/api/environment")
def get_environment_info():
    """Return road network nodes, hazard raster bounds, and network edges GeoJSON."""
    env = get_or_create_routing_environment()
    h_data = env["hazard_data"]
    
    # Generate raster cells GeoJSON grid for heatmap rendering
    # Downsample the 80x80 raster to 20x20 polygons for fast, smooth web rendering
    matrix = h_data["raster"]
    bounds = h_data["bounds"] # min_lon, min_lat, max_lon, max_lat
    min_lon, min_lat, max_lon, max_lat = bounds
    rows, cols = matrix.shape
    step_r = max(1, rows // 20)
    step_c = max(1, cols // 20)
    
    d_lon = (max_lon - min_lon) / cols
    d_lat = (max_lat - min_lat) / rows
    
    heat_features = []
    for r in range(0, rows, step_r):
        for c in range(0, cols, step_c):
            val = float(matrix[r:r+step_r, c:c+step_c].mean())
            c_min_lon = min_lon + c * d_lon
            c_max_lon = min_lon + min(c + step_c, cols) * d_lon
            # Lat is reversed or bottom-up
            c_max_lat = max_lat - r * d_lat
            c_min_lat = max_lat - min(r + step_r, rows) * d_lat
            
            heat_features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [round(c_min_lon, 5), round(c_min_lat, 5)],
                        [round(c_max_lon, 5), round(c_min_lat, 5)],
                        [round(c_max_lon, 5), round(c_max_lat, 5)],
                        [round(c_min_lon, 5), round(c_max_lat, 5)],
                        [round(c_min_lon, 5), round(c_min_lat, 5)]
                    ]]
                },
                "properties": {
                    "hazard": round(val, 3)
                }
            })

    return {
        "study_area": "Ratnapura District, Sabaragamuwa Province, Sri Lanka",
        "bounds": bounds,
        "nodes": env["nodes_summary"],
        "edges_geojson": env["edges_geojson"],
        "hazard_grid_geojson": {
            "type": "FeatureCollection",
            "features": heat_features
        }
    }


@app.get("/api/demo-routes")
def get_demo_geojson():
    """Retrieve the standard baseline GeoJSON produced by the 50% milestone pipeline."""
    geojson_path = REPO_ROOT / "outputs" / "component3" / "routes_50_percent_demo.geojson"
    if not geojson_path.exists():
        raise HTTPException(status_code=404, detail="Demo GeoJSON not found. Run demo.py first.")
    with open(geojson_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data


@app.get("/api/experiment-summary")
def get_experiment_summary():
    """Retrieve statistical aggregations from the Monte Carlo research experiment results."""
    csv_path = REPO_ROOT / "outputs" / "component3" / "experiment_results.csv"
    if not csv_path.exists():
        return {"count": 0, "summary": []}
    
    df = pd.read_csv(csv_path)
    total_runs = len(df)
    reachable_pct = round(float(df["reachable_after_exclusion"].mean()) * 100, 2)
    avg_pareto_size = round(float(df["pareto_size"].mean()), 2)
    avg_detour_pct = round(float(df["time_detour_pct"].mean()), 2)
    avg_exposure_reduction_pct = round(float(df["exposure_reduction_pct"].mean()), 2)
    avg_runtime_sweep = round(float(df["runtime_sweep_s"].mean() * 1000), 2)
    avg_runtime_ls = round(float(df["runtime_ls_s"].mean() * 1000), 2)
    
    # By scenario
    by_scenario = []
    if "scenario" in df.columns:
        for scen, group in df.groupby("scenario"):
            by_scenario.append({
                "scenario": str(scen),
                "count": int(len(group)),
                "reachable_pct": round(float(group["reachable_after_exclusion"].mean()) * 100, 1),
                "avg_exposure_reduction_pct": round(float(group["exposure_reduction_pct"].mean()), 1),
                "avg_detour_pct": round(float(group["time_detour_pct"].mean()), 1),
                "avg_pareto_size": round(float(group["pareto_size"].mean()), 1),
            })

    return {
        "total_simulations": total_runs,
        "reachable_pct": reachable_pct,
        "avg_pareto_size": avg_pareto_size,
        "avg_detour_pct": avg_detour_pct,
        "avg_exposure_reduction_pct": avg_exposure_reduction_pct,
        "avg_runtime_sweep_ms": avg_runtime_sweep,
        "avg_runtime_ls_ms": avg_runtime_ls,
        "by_scenario": by_scenario,
        "sample_records": df.head(10).to_dict(orient="records"),
    }


@app.post("/api/optimize-route")
def optimize_route(req: RouteSimulationRequest):
    """Execute live end-to-end multi-objective routing optimization."""
    env = get_or_create_routing_environment()
    routing_graph = env["routing_graph"]

    origin_coord = (req.origin_lon, req.origin_lat)
    dest_coord = (req.dest_lon, req.dest_lat)

    try:
        snap_info = snap_od_coordinates(
            routing_graph,
            origin_coord=origin_coord,
            destination_coord=dest_coord,
            max_distance_m=10000.0,
            require_connected=True,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Snapping error: {str(e)}")

    origin_node = snap_info["origin_node"]
    dest_node = snap_info["destination_node"]

    # 1. Run Baselines
    baselines = run_all_baselines(routing_graph, origin_node, dest_node, intermediate_alphas=[0.75, 0.50, 0.25])
    comparison_df = compare_routes(routing_graph, baselines)

    # 2. Multi-Objective Pareto Frontier
    sweep_pareto = pareto_sweep_weighted_sum(routing_graph, origin_node, dest_node, steps=21)
    label_pareto = bi_criteria_label_setting(routing_graph, origin_node, dest_node, max_labels_per_node=30)
    all_pareto = filter_dominated_routes(sweep_pareto + label_pareto)

    # 3. Knee-Point Recommendation
    candidate_pool = all_pareto if all_pareto else [{"path": p, "algorithm": k} for k, p in baselines.items() if p]
    recommended = recommend_optimal_route(
        routing_graph,
        candidate_pool,
        user_max_hazard=req.max_hazard_threshold,
        user_max_detour_ratio=req.max_detour_ratio,
    )

    # 4. Resilience Analysis
    resilience_report = evaluate_network_resilience(
        routing_graph, 
        thresholds=[0.90, 0.80, 0.75, 0.60]
    )

    post_exclusion_reachable = True
    if req.hazard_exclusion_threshold is not None:
        pruned_graph = apply_hazard_exclusion(routing_graph, hazard_threshold=req.hazard_exclusion_threshold, inplace=False)
        post_exclusion_reachable = check_scenario_reachability(pruned_graph, origin_node, dest_node)

    # 5. Export GeoJSON
    export_payload = dict(baselines)
    export_payload["recommended_compromise"] = recommended
    geojson_result = export_routes_to_geojson(
        graph=routing_graph,
        routes=export_payload,
        recommended_route_id="recommended_compromise",
        origin_coord=origin_coord,
        destination_coord=dest_coord,
    )

    # Format pareto points for 2D plot
    pareto_points = []
    for r in all_pareto:
        pareto_points.append({
            "algorithm": r.get("algorithm", "pareto"),
            "travel_time_sec": round(float(r["travel_time"]), 1),
            "travel_time_min": round(float(r["travel_time"]) / 60.0, 2),
            "hazard_exposure": round(float(r["hazard_exposure"]), 1),
            "distance_km": round(float(r["distance_m"]) / 1000.0, 2),
            "max_hazard": round(float(r["max_hazard"]), 3),
            "is_recommended": (r.get("path") == recommended.node_path)
        })

    return {
        "snap_info": {
            "origin_node": origin_node,
            "origin_dist_m": round(snap_info["origin_dist_m"], 1),
            "destination_node": dest_node,
            "destination_dist_m": round(snap_info["destination_dist_m"], 1),
        },
        "recommendation": {
            "route_id": recommended.route_id,
            "algorithm": recommended.algorithm,
            "travel_time_min": round(recommended.metrics.total_travel_time_sec / 60.0, 2),
            "travel_time_sec": round(recommended.metrics.total_travel_time_sec, 1),
            "distance_km": round(recommended.metrics.total_distance_m / 1000.0, 2),
            "cumulative_exposure": round(recommended.metrics.cumulative_hazard_exposure, 1),
            "max_edge_hazard": round(recommended.metrics.max_edge_hazard, 3),
            "edge_count": recommended.metrics.edge_count,
            "audit_notes": recommended.audit_notes,
        },
        "baselines_table": comparison_df.to_dict(orient="records"),
        "pareto_points": pareto_points,
        "resilience_table": resilience_report,
        "post_exclusion_reachable": post_exclusion_reachable,
        "geojson": geojson_result,
    }


# Static web UI files
UI_DIR = REPO_ROOT / "ui"
if UI_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(UI_DIR)), name="static")


@app.get("/")
def serve_ui():
    index_file = UI_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return HTMLResponse("<h1>Multi-Hazard Route Optimization UI (Building...)</h1>")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
