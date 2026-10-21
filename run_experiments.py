from __future__ import annotations

import sys
import time
import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

repo_root = Path(r'C:\Multi-Hazard-Aware-Route-Optimization')
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from src.route_optimization.config import RouteOptimizationConfig, DEFAULT_CONFIG
from src.route_optimization.synthetic import (
    generate_synthetic_grid_graph,
    generate_synthetic_hazard_raster,
)
from src.route_optimization.attachment import attach_hazard_to_graph
from src.route_optimization.graph_builder import build_routing_graph
from src.route_optimization.baselines import (
    compute_shortest_time_route,
    compute_risk_min_route,
    compute_weighted_sum_route,
)
from src.route_optimization.pareto import (
    pareto_sweep_weighted_sum,
    bi_criteria_label_setting,
    filter_dominated_routes,
)
from src.route_optimization.scenarios import apply_hazard_exclusion, check_scenario_reachability
from src.route_optimization.metrics import calculate_route_metrics
from src.route_optimization.recommend import recommend_optimal_route

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SEEDS = [42, 7, 123, 999]
SCENARIO_MODES = ['flood_dominant', 'landslide_dominant', 'mixed']
GRID_ROWS = 6
GRID_COLS = 6
GRID_SPACING_M = 1200.0
EXCLUSION_THRESHOLD = 0.75
SWEEP_STEPS = 21

OUTPUT_DIR = repo_root / 'outputs' / 'component3'
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def _generate_od_pairs(graph, n_pairs: int, rng: np.random.Generator) -> List[Tuple[str, str]]:
    nodes = list(graph.nodes())
    pairs = []
    attempts = 0
    while len(pairs) < n_pairs and attempts < n_pairs * 20:
        o, d = rng.choice(nodes, 2, replace=False)
        if o != d and str(o) != str(d):
            pairs.append((str(o), str(d)))
        attempts += 1
    return pairs


def _path_objectives(graph, path) -> Tuple[float, float]:
    if not path or len(path) < 2:
        return float('inf'), float('inf')
    t = sum(float(graph[u][v].get('travel_time', 1.0)) for u, v in zip(path[:-1], path[1:]))
    r = sum(float(graph[u][v].get('hazard_exposure', 0.0)) for u, v in zip(path[:-1], path[1:]))
    return t, r


def run_single_od(graph, origin: str, destination: str, config: RouteOptimizationConfig) -> Optional[Dict[str, Any]]:
    fast_path = compute_shortest_time_route(graph, origin, destination)
    if fast_path is None:
        return None
    t_fast, r_fast = _path_objectives(graph, fast_path)
    if t_fast == float('inf'):
        return None

    safe_path = compute_risk_min_route(graph, origin, destination)
    t_safe, r_safe = _path_objectives(graph, safe_path) if safe_path else (float('inf'), float('inf'))

    t0 = time.perf_counter()
    sweep_pareto = pareto_sweep_weighted_sum(graph, origin, destination, steps=SWEEP_STEPS)
    t_sweep = time.perf_counter() - t0

    t0 = time.perf_counter()
    ls_pareto = bi_criteria_label_setting(graph, origin, destination, max_labels_per_node=80)
    t_ls = time.perf_counter() - t0

    all_combined = filter_dominated_routes(sweep_pareto + ls_pareto)

    full_obj_set = {(r['travel_time'], r['hazard_exposure']) for r in all_combined}
    sweep_obj_set = {(r['travel_time'], r['hazard_exposure']) for r in sweep_pareto}

    sweep_missed = len(full_obj_set - sweep_obj_set)
    pareto_size = max(1, len(full_obj_set))

    # Recommend optimal route using standard policy (enforcing detour budget)
    rec = recommend_optimal_route(
        graph,
        all_combined,
        config=config,
        user_max_hazard=0.70,
        user_max_detour_ratio=config.max_detour_ratio,
    )
    t_knee = rec.metrics.total_travel_time_sec
    r_knee = rec.metrics.cumulative_hazard_exposure
    constraint_flag = "NORMAL"
    for line in (rec.audit_notes or "").splitlines():
        if line.startswith("STATUS_FLAG:"):
            constraint_flag = line.split(":", 1)[1].strip()

    time_detour_pct = (t_knee - t_fast) / max(1e-4, t_fast) * 100.0
    exposure_reduction_pct = (r_fast - r_knee) / max(1e-4, r_fast) * 100.0 if r_fast > 0 else 0.0

    excl_graph = apply_hazard_exclusion(graph, hazard_threshold=EXCLUSION_THRESHOLD, inplace=False)
    is_reachable = check_scenario_reachability(excl_graph, origin, destination)

    return {
        't_fast': t_fast, 'r_fast': r_fast,
        't_safe': t_safe, 'r_safe': r_safe,
        't_knee': t_knee, 'r_knee': r_knee,
        'time_detour_pct': time_detour_pct,
        'exposure_reduction_pct': exposure_reduction_pct,
        'pareto_size': pareto_size,
        'sweep_missed_count': sweep_missed,
        'sweep_missed_frac': sweep_missed / pareto_size,
        'reachable_after_exclusion': int(is_reachable),
        'runtime_sweep_s': t_sweep,
        'runtime_ls_s': t_ls,
        'edge_count_knee': rec.metrics.edge_count,
        'constraint_flag': constraint_flag,
    }


def main():
    print('=' * 72)
    print('COMPONENT 3 - SYSTEMATIC EXPERIMENT RUNNER')
    print('Project ID: J26-DS-301  |  Hafsa A.R.F (IT23363434)')
    print(f'Grid: {GRID_ROWS}x{GRID_COLS}, spacing={GRID_SPACING_M:.0f}m')
    print(f'Seeds: {SEEDS}  |  Scenarios: {SCENARIO_MODES}')
    print(f'Exclusion threshold: {EXCLUSION_THRESHOLD}  (scenario policy setting)')
    print('NOTE: Thresholds are configurable scenario parameters for sensitivity analysis.')
    print('=' * 72)

    all_records: List[Dict[str, Any]] = []
    plot_example: Optional[Dict[str, Any]] = None

    for scenario_mode in SCENARIO_MODES:
        for seed in SEEDS:
            rng = np.random.default_rng(seed)
            print(f'\n[scenario={scenario_mode}, seed={seed}]', end=' ', flush=True)

            raw_graph = generate_synthetic_grid_graph(
                rows=GRID_ROWS, cols=GRID_COLS, spacing_m=GRID_SPACING_M,
                base_lat=6.64, base_lon=80.36, directed=True, seed=seed,
            )
            hazard_data = generate_synthetic_hazard_raster(
                min_lat=6.63, min_lon=80.35, max_lat=6.73, max_lon=80.46,
                grid_size=(80, 80), scenario_mode=scenario_mode, seed=seed,
            )
            attach_hazard_to_graph(raw_graph, hazard_data)
            graph = build_routing_graph(raw_graph)

            od_pairs = _generate_od_pairs(graph, n_pairs=40, rng=rng)

            for od_idx, (origin, destination) in enumerate(od_pairs):
                result = run_single_od(graph, origin, destination, DEFAULT_CONFIG)
                if result is None:
                    continue
                result.update({'scenario': scenario_mode, 'seed': seed, 'od_idx': od_idx,
                                'origin': origin, 'destination': destination})
                all_records.append(result)

                if plot_example is None and scenario_mode == 'mixed' and seed == 42:
                    plot_example = {'graph': graph, 'origin': origin, 'destination': destination,
                                    'result': result, 'scenario': scenario_mode, 'seed': seed}

            n_done = len([r for r in all_records if r['scenario'] == scenario_mode and r['seed'] == seed])
            print(f'ok ({n_done} ODs)')

    if not all_records:
        print('\n[ERROR] No results collected -- check graph connectivity.')
        return

    df = pd.DataFrame(all_records)

    print('\n' + '=' * 72)
    print('TABLE 1: Knee-Point -- Exposure Reduction vs Time Detour')
    print('(Recommendation is an auditable rule; knee-point depends on candidate pool & normalization)')
    print('-' * 72)
    summary = (
        df.groupby('scenario')[['exposure_reduction_pct', 'time_detour_pct']]
        .agg(['median', lambda q: q.quantile(0.25), lambda q: q.quantile(0.75)])
    )
    summary.columns = ['exp_red_median%', 'exp_red_Q1%', 'exp_red_Q3%',
                        'time_det_median%', 'time_det_Q1%', 'time_det_Q3%']
    print(summary.to_string(float_format='%.1f'))

    print('\n' + '=' * 72)
    print('TABLE 2: Pareto Objectives Missed by Weighted-Sum Sweep')
    print('-' * 72)
    sweep_tbl = (
        df.groupby('scenario')[['sweep_missed_frac', 'pareto_size']]
        .agg(['median', 'mean'])
    )
    sweep_tbl.columns = ['missed_frac_median', 'missed_frac_mean',
                          'pareto_size_median', 'pareto_size_mean']
    print(sweep_tbl.to_string(float_format='%.3f'))

    print('\n' + '=' * 72)
    print(f'TABLE 3: OD Infeasible Rate after Hazard Exclusion (threshold={EXCLUSION_THRESHOLD})')
    print('(threshold is a scenario policy setting)')
    print('-' * 72)
    infeas_tbl = df.groupby('scenario')['reachable_after_exclusion'].agg(
        reachable_count='sum',
        total_count='count',
        infeasible_rate=lambda x: 1 - x.mean(),
    )
    print(infeas_tbl.to_string(float_format='%.3f'))

    print('\n' + '=' * 72)
    print('TABLE 4: Runtime -- Label-Setting vs Weighted-Sum Sweep (seconds per OD pair)')
    print('-' * 72)
    runtime_tbl = df.groupby('scenario')[['runtime_ls_s', 'runtime_sweep_s']].agg(['median', 'mean', 'max'])
    print(runtime_tbl.to_string(float_format='%.4f'))

    csv_path = OUTPUT_DIR / 'experiment_results.csv'
    df.to_csv(csv_path, index=False)
    print(f'\nRaw results saved to {csv_path}')

    if plot_example is not None:
        _plot_pareto_and_map(plot_example, OUTPUT_DIR)

    print('\n' + '=' * 72)
    print('EXPERIMENT RUN COMPLETE')
    print('=' * 72)


def _path_objectives_for_plot(graph, path):
    if not path or len(path) < 2:
        return float('inf'), float('inf')
    t = sum(float(graph[u][v].get('travel_time', 1.0)) for u, v in zip(path[:-1], path[1:]))
    r = sum(float(graph[u][v].get('hazard_exposure', 0.0)) for u, v in zip(path[:-1], path[1:]))
    return t, r


def _plot_pareto_and_map(example: Dict[str, Any], out_dir: Path) -> None:
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError:
        print('\n[WARN] matplotlib not installed -- skipping plots.')
        _print_ascii_pareto(example)
        return

    graph = example['graph']
    origin = example['origin']
    destination = example['destination']
    scenario = example['scenario']
    seed = example['seed']

    sweep_pareto = pareto_sweep_weighted_sum(graph, origin, destination, steps=SWEEP_STEPS)
    ls_pareto = bi_criteria_label_setting(graph, origin, destination, max_labels_per_node=80)
    all_combined = filter_dominated_routes(sweep_pareto + ls_pareto)

    fast_path = compute_shortest_time_route(graph, origin, destination)
    safe_path = compute_risk_min_route(graph, origin, destination)
    t_fast, r_fast = _path_objectives_for_plot(graph, fast_path) if fast_path else (0, 0)
    t_safe, r_safe = _path_objectives_for_plot(graph, safe_path) if safe_path else (0, 0)

    fig, axes = plt.subplots(1, 2, figsize=(14, 6))
    fig.suptitle(
        f'Component 3 Evidence Plot | scenario={scenario}, seed={seed}\n'
        f'OD: {origin} -> {destination}',
        fontsize=11, fontweight='bold'
    )

    # Pareto front plot
    ax = axes[0]
    if ls_pareto:
        ax.scatter([r['travel_time']/60.0 for r in ls_pareto],
                   [r['hazard_exposure'] for r in ls_pareto],
                   c='#aaaaaa', s=40, label='Label-setting candidates', zorder=2, alpha=0.6)
    if all_combined:
        pt = sorted(all_combined, key=lambda r: r['travel_time'])
        ax.plot([r['travel_time']/60.0 for r in pt], [r['hazard_exposure'] for r in pt],
                'b-o', lw=1.5, ms=7, label='Pareto front (merged)', zorder=3)
    if sweep_pareto:
        ax.scatter([r['travel_time']/60.0 for r in sweep_pareto],
                   [r['hazard_exposure'] for r in sweep_pareto],
                   marker='^', c='#ff7700', s=60, label='Sweep-only', zorder=4, alpha=0.8)
    ax.scatter([t_fast/60.0], [r_fast], marker='*', c='green', s=180, zorder=5,
               label=f'Fastest ({t_fast/60:.1f} min)')
    ax.scatter([t_safe/60.0], [r_safe], marker='D', c='red', s=120, zorder=5,
               label=f'Safest ({r_safe:.0f} haz*m)')
    ax.set_xlabel('Travel Time (minutes)')
    ax.set_ylabel('Cumulative Exposure (hazard*metres)')
    ax.set_title('Bi-Criteria Pareto Front')
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)

    # Route map
    ax2 = axes[1]
    ax2.set_aspect('equal')
    ax2.set_title('Route Map (Fastest / Safest / Pareto Knee)')
    for u, v, edata in graph.edges(data=True):
        xu, yu = graph.nodes[u].get('x', 0), graph.nodes[u].get('y', 0)
        xv, yv = graph.nodes[v].get('x', 0), graph.nodes[v].get('y', 0)
        h = edata.get('hazard_score', 0.0)
        c = plt.cm.RdYlGn_r(h)
        ax2.plot([xu, xv], [yu, yv], color=c, lw=0.8, alpha=0.5, zorder=1)

    def draw_path(path, color, lw, label):
        if path and len(path) >= 2:
            xs = [graph.nodes[n].get('x', 0) for n in path]
            ys = [graph.nodes[n].get('y', 0) for n in path]
            ax2.plot(xs, ys, color=color, lw=lw, label=label, zorder=4)

    draw_path(fast_path, 'green', 2.5, 'Fastest')
    draw_path(safe_path, 'blue', 2.5, 'Safest')

    if all_combined:
        t_min = min(r['travel_time'] for r in all_combined)
        r_min = min(r['hazard_exposure'] for r in all_combined)
        t_max = max(r['travel_time'] for r in all_combined)
        r_max = max(r['hazard_exposure'] for r in all_combined)
        t_span = max(1e-4, t_max - t_min)
        r_span = max(1e-4, r_max - r_min)
        best = min(all_combined,
                   key=lambda c: ((c['travel_time']-t_min)/t_span)**2 + ((c['hazard_exposure']-r_min)/r_span)**2)
        draw_path(best.get('path', []), 'darkorange', 3.0, 'Pareto Knee')

    ox, oy = graph.nodes[origin].get('x', 0), graph.nodes[origin].get('y', 0)
    dx, dy = graph.nodes[destination].get('x', 0), graph.nodes[destination].get('y', 0)
    ax2.scatter([ox], [oy], c='green', s=200, zorder=6, marker='o')
    ax2.scatter([dx], [dy], c='red', s=200, zorder=6, marker='X')
    ax2.annotate('O', (ox, oy), fontsize=9, fontweight='bold', color='green')
    ax2.annotate('D', (dx, dy), fontsize=9, fontweight='bold', color='red')
    ax2.legend(fontsize=8)
    ax2.grid(True, alpha=0.3)
    sm = plt.cm.ScalarMappable(cmap='RdYlGn_r', norm=plt.Normalize(0, 1))
    sm.set_array([])
    plt.colorbar(sm, ax=ax2, shrink=0.6).set_label('Edge Hazard Score')

    plt.tight_layout()
    plot_path = out_dir / 'experiment_pareto_and_map.png'
    plt.savefig(plot_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f'Plot saved to {plot_path}')


def _print_ascii_pareto(example):
    graph = example['graph']
    origin = example['origin']
    destination = example['destination']
    sweep = pareto_sweep_weighted_sum(graph, origin, destination, steps=SWEEP_STEPS)
    ls = bi_criteria_label_setting(graph, origin, destination, max_labels_per_node=80)
    merged = filter_dominated_routes(sweep + ls)
    sc = example['scenario']
    sd = example['seed']
    print('\n--- Pareto Front (scenario=' + sc + ', seed=' + str(sd) + ') ---')
    print('  OD: ' + origin + ' -> ' + destination)
    header = '  ' + 'travel_time_min'.rjust(18) + '  ' + 'exposure_haz_m'.rjust(16) + '  ' + 'algorithm'.rjust(18)
    print(header)
    for r in sorted(merged, key=lambda x: x['travel_time']):
        row = '  ' + f"{r['travel_time']/60.0:>18.2f}  {r['hazard_exposure']:>16.1f}  " + r.get('algorithm', '?').rjust(18)
        print(row)


if __name__ == '__main__':
    main()
