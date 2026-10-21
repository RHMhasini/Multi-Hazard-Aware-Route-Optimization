"""Multi-Hazard-Aware Route Optimization Package (Component 3).

Provides end-to-end multi-objective routing:
- Dynamic hazard impedance and spatial raster attachment
- Multi-criteria Dijkstra and Pareto frontier exploration
- Baseline benchmarks (shortest distance, quickest, risk-minimal, weighted-sum)
- Origin-Destination coordinate snapping (KD-Tree & Euclidean)
- Disaster scenario analysis and high-risk edge exclusion
- Transparent, audit-traceable route recommendation rules
- GeoJSON export for Component 4 dispatch visualization
"""

from src.route_optimization.config import (
    RiskFormulaChoice,
    RouteOptimizationConfig,
    DEFAULT_CONFIG,
)
from src.route_optimization.synthetic import (
    LABEL_MODE,
    generate_synthetic_grid_graph,
    generate_synthetic_hazard_raster,
    generate_synthetic_od_pairs,
    generate_ratnapura_road_network,
)
from src.route_optimization.loaders import (
    load_c1_hazard_raster,
    load_c2_road_network,
    validate_c2_graph,
)
from src.route_optimization.attachment import (
    attach_hazard_to_graph,
    compute_edge_raster_hazard,
    sample_raster_at_point,
)
from src.route_optimization.graph_builder import (
    build_routing_graph,
    compute_edge_impedance,
)
from src.route_optimization.baselines import (
    compute_shortest_distance_route,
    compute_shortest_time_route,
    compute_risk_min_route,
    compute_weighted_sum_route,
    run_all_baselines,
)
from src.route_optimization.pareto import (
    is_dominated,
    filter_dominated_routes,
    pareto_sweep_weighted_sum,
    bi_criteria_label_setting,
)
from src.route_optimization.metrics import (
    calculate_route_metrics,
    compare_routes,
)
from src.route_optimization.recommend import (
    recommend_optimal_route,
)
from src.route_optimization.snapping import (
    CoordinateSnapper,
    snap_coordinate_to_node,
    snap_od_coordinates,
)
from src.route_optimization.scenarios import (
    apply_hazard_exclusion,
    apply_hazard_penalty_scenario,
    check_scenario_reachability,
    evaluate_network_resilience,
)
from src.route_optimization.exporter import (
    export_routes_to_geojson,
)

__all__ = [
    "RiskFormulaChoice",
    "RouteOptimizationConfig",
    "DEFAULT_CONFIG",
    "generate_synthetic_grid_graph",
    "generate_synthetic_hazard_raster",
    "generate_synthetic_od_pairs",
    "load_c1_hazard_raster",
    "load_c2_road_network",
    "validate_c2_graph",
    "attach_hazard_to_graph",
    "compute_edge_raster_hazard",
    "sample_raster_at_point",
    "build_routing_graph",
    "compute_edge_impedance",
    "compute_shortest_distance_route",
    "compute_shortest_time_route",
    "compute_risk_min_route",
    "compute_weighted_sum_route",
    "run_all_baselines",
    "is_dominated",
    "filter_dominated_routes",
    "pareto_sweep_weighted_sum",
    "bi_criteria_label_setting",
    "calculate_route_metrics",
    "compare_routes",
    "recommend_optimal_route",
    "CoordinateSnapper",
    "snap_coordinate_to_node",
    "snap_od_coordinates",
    "apply_hazard_exclusion",
    "apply_hazard_penalty_scenario",
    "check_scenario_reachability",
    "evaluate_network_resilience",
    "export_routes_to_geojson",
    "LABEL_MODE",
    "generate_ratnapura_road_network",
]
