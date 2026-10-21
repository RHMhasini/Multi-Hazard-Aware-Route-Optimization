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


LABEL_MODE = "demo"  # Default mode: "demo" (role-based neutral labels) or "real" (loaded data attributes)


def _build_demo_node_metadata(r: int, c: int, rows: int, cols: int) -> Dict[str, str]:
    """Generate neutral, role-based metadata for synthetic grid nodes in demo mode.

    Labels are built from facility_type and an index:
    'Illustrative hospital', 'Illustrative relief depot', 'Illustrative shelter',
    'Illustrative bridge crossing', 'Illustrative highway junction',
    'Synthetic flood-prone zone', 'Synthetic landslide-prone zone', otherwise 'Node r_c'.
    """
    node_id = f"node_{r}_{c}"
    # Role assignment across 6x6 lattice or general grid:
    # Corner top-left: hospital
    # Corner bottom-right: relief depot
    # Corner bottom-left: shelter
    # River crossing / valley mid-points: bridge crossing, synthetic flood-prone zone
    # Mountain / upper perimeter: synthetic landslide-prone zone
    # Key junctions: highway junction
    if r == rows - 1 and c == 0:
        facility_type = "hospital"
        label = "Illustrative hospital"
    elif r == 0 and c == cols - 1:
        facility_type = "relief_depot"
        label = "Illustrative relief depot"
    elif r == 0 and c == 0:
        facility_type = "shelter"
        label = "Illustrative shelter"
    elif (r, c) == (rows // 2, cols // 2) or (r == 3 and c == 2):
        facility_type = "flood_prone_zone"
        label = "Synthetic flood-prone zone"
    elif (r, c) == (rows - 1, cols - 1) or (r == 5 and c == 5):
        facility_type = "landslide_prone_zone"
        label = "Synthetic landslide-prone zone"
    elif (r, c) in [(rows // 2, 1), (2, 1), (1, 1)]:
        facility_type = "bridge_crossing"
        label = "Illustrative bridge crossing"
    elif (r, c) in [(rows - 1, 0), (rows // 2, cols - 2), (3, 4), (2, 3), (1, 5)]:
        facility_type = "highway_junction"
        label = "Illustrative highway junction"
    elif (r, c) in [(1, 4), (0, 3)]:
        facility_type = "shelter"
        label = "Illustrative shelter"
    else:
        facility_type = "junction"
        label = f"Node {r}_{c}"

    name = label
    return {
        "name": name,
        "label": label,
        "facility_type": facility_type,
    }


def generate_synthetic_grid_graph(
    rows: int = 6,
    cols: int = 6,
    spacing_m: Optional[float] = None,
    base_lat: Optional[float] = None,
    base_lon: Optional[float] = None,
    bounds: Optional[Tuple[float, float, float, float]] = None,
    directed: bool = True,
    seed: Optional[int] = None,
    oneway_probability: float = 0.15,
    region: str = "synthetic",
    label_mode: Optional[str] = None,
) -> nx.DiGraph:
    """Generate a synthetic 2D road network graph.

    Constructs a synthetic grid network with neutral role-based labels (in demo mode)
    or loaded attributes (in real mode), highway classifications, and directed edges.

    Args:
        rows: Number of grid rows (latitude direction).
        cols: Number of grid columns (longitude direction).
        spacing_m: Distance between adjacent nodes in meters.
        base_lat: Latitude of bottom-left origin node.
        base_lon: Longitude of bottom-left origin node.
        bounds: Optional (min_lon, min_lat, max_lon, max_lat) to stretch grid across exact bounding box.
        directed: Whether to generate a directed graph.
        seed: Random seed for edge classification and perturbations.
        oneway_probability: Probability of a street being strictly one-way.
        region: Geographic target region (default: "synthetic").
        label_mode: "demo" (default) or "real".

    Returns:
        nx.DiGraph: Directed network with node coords ('x', 'y'), neutral labels ('name', 'label',
            'facility_type', 'district', 'province'), and edge attributes ('length', 'highway', 'oneway').
    """
    mode = (label_mode or LABEL_MODE).lower()
    rng = np.random.default_rng(seed if seed is not None else DEFAULT_CONFIG.random_seed)

    meters_per_deg_lat = 110500.0

    if bounds is not None:
        min_lon, min_lat, max_lon, max_lat = bounds
        actual_base_lat = min_lat
        actual_base_lon = min_lon
        delta_lat = (max_lat - min_lat) / max(1, rows - 1)
        delta_lon = (max_lon - min_lon) / max(1, cols - 1)
        actual_spacing_m = delta_lat * meters_per_deg_lat
    else:
        if base_lat is not None:
            actual_base_lat = float(base_lat)
        else:
            actual_base_lat = 6.64 if rows >= 6 else 6.68

        if base_lon is not None:
            actual_base_lon = float(base_lon)
        else:
            actual_base_lon = 80.36 if cols >= 6 else 80.40

        meters_per_deg_lon = meters_per_deg_lat * math.cos(math.radians(actual_base_lat))

        if spacing_m is not None:
            actual_spacing_m = float(spacing_m)
            delta_lat = actual_spacing_m / meters_per_deg_lat
            delta_lon = actual_spacing_m / meters_per_deg_lon
        else:
            actual_spacing_m = 1850.0 if rows >= 6 else 1000.0
            delta_lat = actual_spacing_m / meters_per_deg_lat
            delta_lon = actual_spacing_m / meters_per_deg_lon

    meters_per_deg_lon = meters_per_deg_lat * math.cos(math.radians(actual_base_lat))

    G = nx.DiGraph() if directed else nx.Graph()

    for r in range(rows):
        for c in range(cols):
            node_id = f"node_{r}_{c}"
            lat_jitter = float(rng.uniform(-0.03, 0.03) * delta_lat)
            lon_jitter = float(rng.uniform(-0.03, 0.03) * delta_lon)
            lat = actual_base_lat + r * delta_lat + lat_jitter
            lon = actual_base_lon + c * delta_lon + lon_jitter

            if mode == "demo":
                meta = _build_demo_node_metadata(r, c, rows, cols)
                node_attrs = {
                    "x": round(lon, 6),
                    "y": round(lat, 6),
                    "row": r,
                    "col": c,
                    "name": meta["name"],
                    "label": meta["label"],
                    "facility_type": meta["facility_type"],
                    "district": "synthetic",
                    "province": "synthetic",
                }
            else:
                # real mode: labels come only from attributes already present in the loaded data.
                # If an attribute is missing, fall back to the node ID. Never invent names.
                node_attrs = {
                    "x": round(lon, 6),
                    "y": round(lat, 6),
                    "row": r,
                    "col": c,
                    "name": node_id,
                    "label": node_id,
                    "facility_type": "junction",
                    "district": "synthetic",
                    "province": "synthetic",
                }

            G.add_node(node_id, **node_attrs)

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

        edge_attrs = {
            "length": round(length, 2),
            "highway": hw,
            "oneway": False,
        }

        # In real mode, use edge name/ref only if already present; never invent road_name in demo mode
        if mode == "real":
            # Real mode: only include if present
            pass

        if not is_oneway or not directed:
            G.add_edge(u, v, **edge_attrs)
            if directed:
                G.add_edge(v, u, **edge_attrs)
        else:
            edge_attrs["oneway"] = True
            if (u_data["x"] + u_data["y"]) <= (v_data["x"] + v_data["y"]):
                G.add_edge(u, v, **edge_attrs)
            else:
                G.add_edge(v, u, **edge_attrs)

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


def generate_ratnapura_road_network(
    rows: int = 6,
    cols: int = 6,
    seed: Optional[int] = None,
    label_mode: Optional[str] = None,
) -> nx.DiGraph:
    """Convenience factory to generate synthetic road network."""
    return generate_synthetic_grid_graph(
        rows=rows,
        cols=cols,
        spacing_m=1850.0,
        base_lat=6.64,
        base_lon=80.36,
        directed=True,
        seed=seed,
        region="synthetic",
        label_mode=label_mode,
    )


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
        hazard_centers: Optional explicit list of hazard center dicts.
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
            # Steep slope localized landslide hazard peaks
            hazard_centers = [
                {"lat": mid_lat + 0.025, "lon": mid_lon - 0.02, "intensity": 0.90, "sigma": 0.008, "type": "landslide"},
                {"lat": mid_lat - 0.02, "lon": mid_lon - 0.025, "intensity": 0.85, "sigma": 0.007, "type": "landslide"},
                {"lat": mid_lat + 0.01, "lon": mid_lon + 0.025, "intensity": 0.80, "sigma": 0.009, "type": "landslide"},
            ]
        else: # mixed
            # Hazard concentration placed along the central corridor leaving eastern and western detours safe
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
