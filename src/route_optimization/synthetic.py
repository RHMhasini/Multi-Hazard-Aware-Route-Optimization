"""Synthetic data generator for Component 3.

Generates:
- Synthetic road network graphs (grid / lattice / planar topologies)
- Synthetic hazard rasters (multi-modal Gaussian hazard fields simulating flood & landslide zones)
- Origin-Destination (OD) pairs traversing hazard-affected areas
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple, Any
import numpy as np
import networkx as nx

from src.route_optimization.config import RouteOptimizationConfig, DEFAULT_CONFIG


def generate_synthetic_grid_graph(
    rows: int = 6,
    cols: int = 6,
    spacing_m: float = 1000.0,
    base_lat: float = 6.68,
    base_lon: float = 80.40,
    directed: bool = True,
    seed: Optional[int] = None,
    oneway_probability: float = 0.15,
) -> nx.DiGraph:
    """Generate a synthetic 2D grid road network with realistic coordinates, highway classes, and one-way streets.

    Args:
        rows: Number of grid rows (y direction).
        cols: Number of grid columns (x direction).
        spacing_m: Distance between adjacent nodes in meters.
        base_lat: Latitude of bottom-left origin node (approx. Sabaragamuwa, Sri Lanka).
        base_lon: Longitude of bottom-left origin node.
        directed: Whether to generate a directed graph.
        seed: Random seed for edge classification and perturbations.
        oneway_probability: Probability of a street being strictly one-way.

    Returns:
        nx.DiGraph: Directed network with node coords ('x', 'y') and edge attributes ('length', 'highway', 'oneway').
    """
    rng = np.random.default_rng(seed if seed is not None else DEFAULT_CONFIG.random_seed)
    
    meters_per_deg_lat = 110500.0
    meters_per_deg_lon = 110500.0 * math.cos(math.radians(base_lat))

    delta_lat = spacing_m / meters_per_deg_lat
    delta_lon = spacing_m / meters_per_deg_lon

    G = nx.DiGraph() if directed else nx.Graph()

    # Create nodes
    for r in range(rows):
        for c in range(cols):
            node_id = f"node_{r}_{c}"
            lat_jitter = float(rng.uniform(-0.04, 0.04) * delta_lat)
            lon_jitter = float(rng.uniform(-0.04, 0.04) * delta_lon)
            lat = base_lat + r * delta_lat + lat_jitter
            lon = base_lon + c * delta_lon + lon_jitter

            G.add_node(
                node_id,
                x=round(lon, 6),
                y=round(lat, 6),
                row=r,
                col=c,
            )

    highway_types = ["primary", "secondary", "tertiary", "residential"]
    highway_weights = [0.15, 0.25, 0.35, 0.25]

    def add_edge_pair(u: str, v: str):
        u_data = G.nodes[u]
        v_data = G.nodes[v]
        dlat_m = (v_data["y"] - u_data["y"]) * meters_per_deg_lat
        dlon_m = (v_data["x"] - u_data["x"]) * meters_per_deg_lon
        length = float(math.hypot(dlat_m, dlon_m))

        hw = str(rng.choice(highway_types, p=highway_weights))
        is_oneway = bool(rng.uniform(0.0, 1.0) < oneway_probability)

        if not is_oneway or not directed:
            G.add_edge(u, v, length=round(length, 2), highway=hw, oneway=False)
            if directed:
                G.add_edge(v, u, length=round(length, 2), highway=hw, oneway=False)
        else:
            # Deterministic one-way direction based on node coordinate comparison
            if (u_data["x"] + u_data["y"]) <= (v_data["x"] + v_data["y"]):
                G.add_edge(u, v, length=round(length, 2), highway=hw, oneway=True)
            else:
                G.add_edge(v, u, length=round(length, 2), highway=hw, oneway=True)

    # Horizontal and vertical edges
    for r in range(rows):
        for c in range(cols):
            u = f"node_{r}_{c}"
            if c + 1 < cols:
                v_right = f"node_{r}_{c+1}"
                add_edge_pair(u, v_right)
            if r + 1 < rows:
                v_up = f"node_{r+1}_{c}"
                add_edge_pair(u, v_up)

    return G


def generate_synthetic_hazard_raster(
    min_lat: float = 6.63,
    min_lon: float = 80.35,
    max_lat: float = 6.73,
    max_lon: float = 80.46,
    grid_size: Tuple[int, int] = (80, 80),
    hazard_centers: Optional[List[Dict[str, Any]]] = None,
    scenario_mode: str = "mixed",
    seed: Optional[int] = None,
) -> Dict[str, Any]:
    """Generate a synthetic 2D hazard raster with flood and/or landslide hotspots.

    Args:
        min_lat: Minimum latitude bound.
        min_lon: Minimum longitude bound.
        max_lat: Maximum latitude bound.
        max_lon: Maximum longitude bound.
        grid_size: (rows, cols) resolution of the raster.
        hazard_centers: Optional explicit list of hotspot center dicts.
        scenario_mode: 'mixed' (flood + landslide), 'flood_dominant', or 'landslide_dominant'.
        seed: Random seed.

    Returns:
        Dict with keys:
            - 'raster': 2D numpy array with values in [0.0, 1.0]
            - 'bounds': (min_lon, min_lat, max_lon, max_lat)
            - 'lat_coords': 1D array of latitude grid coordinates
            - 'lon_coords': 1D array of longitude grid coordinates
            - 'crs': "EPSG:4326"
            - 'scenario_mode': Active scenario
    """
    rng = np.random.default_rng(seed if seed is not None else DEFAULT_CONFIG.random_seed)
    rows, cols = grid_size

    lat_coords = np.linspace(min_lat, max_lat, rows)
    lon_coords = np.linspace(min_lon, max_lon, cols)

    mid_lat = (min_lat + max_lat) / 2.0
    mid_lon = (min_lon + max_lon) / 2.0

    if hazard_centers is None:
        if scenario_mode == "flood_dominant":
            # Broad lowland river valley flood across middle
            hazard_centers = [
                {"lat": mid_lat, "lon": mid_lon, "intensity": 0.95, "sigma": 0.022, "type": "flood"},
                {"lat": mid_lat - 0.015, "lon": mid_lon + 0.015, "intensity": 0.85, "sigma": 0.018, "type": "flood"},
            ]
        elif scenario_mode == "landslide_dominant":
            # Steep slope localized landslide hotspots
            hazard_centers = [
                {"lat": mid_lat + 0.025, "lon": mid_lon - 0.02, "intensity": 0.90, "sigma": 0.008, "type": "landslide"},
                {"lat": mid_lat - 0.02, "lon": mid_lon - 0.025, "intensity": 0.85, "sigma": 0.007, "type": "landslide"},
                {"lat": mid_lat + 0.01, "lon": mid_lon + 0.025, "intensity": 0.80, "sigma": 0.009, "type": "landslide"},
            ]
        else: # mixed
            # Hotspot placed along the central corridor leaving eastern and western detours safe
            hazard_centers = [
                {"lat": mid_lat, "lon": mid_lon, "intensity": 0.92, "sigma": 0.014, "type": "flood"},
                {"lat": mid_lat + 0.015, "lon": mid_lon - 0.01, "intensity": 0.78, "sigma": 0.010, "type": "landslide"},
            ]

    # Compute 2D hazard surface
    Lon, Lat = np.meshgrid(lon_coords, lat_coords)
    hazard_grid = np.zeros((rows, cols), dtype=np.float64)

    for center in hazard_centers:
        c_lat = center["lat"]
        c_lon = center["lon"]
        intensity = center.get("intensity", 0.8)
        sigma = center.get("sigma", 0.015)

        dist_sq = (Lat - c_lat) ** 2 + (Lon - c_lon) ** 2
        blob = intensity * np.exp(-dist_sq / (2.0 * sigma ** 2))
        hazard_grid = np.maximum(hazard_grid, blob)

    # Low ambient baseline noise
    noise = rng.uniform(0.0, 0.03, size=(rows, cols))
    hazard_grid = np.clip(hazard_grid + noise, 0.0, 1.0)

    return {
        "raster": hazard_grid,
        "bounds": (min_lon, min_lat, max_lon, max_lat),
        "lat_coords": lat_coords,
        "lon_coords": lon_coords,
        "crs": DEFAULT_CONFIG.crs_source,
        "dimensions": (rows, cols),
        "scenario_mode": scenario_mode,
        "origin_upper": False,
    }


def generate_synthetic_od_pairs(
    graph: nx.DiGraph,
    num_pairs: int = 5,
    min_hop_distance: int = 3,
    seed: Optional[int] = None,
) -> List[Tuple[str, str]]:
    """Sample realistic Origin-Destination node pairs separated by at least min_hop_distance.

    Args:
        graph: NetworkX graph.
        num_pairs: Number of OD pairs to generate.
        min_hop_distance: Minimum shortest-path hop count required between origin and destination.
        seed: Random seed.

    Returns:
        List of (origin_node_id, destination_node_id) tuples.
    """
    rng = np.random.default_rng(seed if seed is not None else DEFAULT_CONFIG.random_seed)
    nodes = list(graph.nodes())
    if len(nodes) < 2:
        return []

    od_pairs: List[Tuple[str, str]] = []
    attempts = 0
    max_attempts = num_pairs * 50

    while len(od_pairs) < num_pairs and attempts < max_attempts:
        attempts += 1
        origin, dest = rng.choice(nodes, size=2, replace=False)
        origin, dest = str(origin), str(dest)

        if nx.has_path(graph, origin, dest):
            # Check topological distance
            shortest_hops = nx.shortest_path_length(graph, origin, dest)
            if shortest_hops >= min_hop_distance:
                if (origin, dest) not in od_pairs:
                    od_pairs.append((origin, dest))

    # Fallback to corner pairs if not enough random pairs found
    if not od_pairs and len(nodes) >= 2:
        od_pairs.append((nodes[0], nodes[-1]))

    return od_pairs
