"""Hazard attachment engine for Component 3.

Computes the maximum intersecting hazard score (and mean/exposure) along each road edge
by spatial intersection with Component 1 hazard surfaces (rasters or vector zones).
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple, Union
import numpy as np
import networkx as nx
from shapely.geometry import LineString, Point


def sample_raster_at_point(
    lon: float,
    lat: float,
    raster: np.ndarray,
    bounds: Tuple[float, float, float, float],
) -> float:
    """Sample the raster cell containing a (lon, lat) coordinate.

    Args:
        lon: Longitude.
        lat: Latitude.
        raster: 2D numpy array [rows, cols].
        bounds: (min_lon, min_lat, max_lon, max_lat).

    Returns:
        float: Normalized hazard value in [0.0, 1.0].
    """
    min_lon, min_lat, max_lon, max_lat = bounds
    if not (min_lon <= lon <= max_lon and min_lat <= lat <= max_lat):
        return 0.0

    rows, cols = raster.shape
    frac_x = (lon - min_lon) / (max_lon - min_lon) if max_lon > min_lon else 0.0
    frac_y = (lat - min_lat) / (max_lat - min_lat) if max_lat > min_lat else 0.0

    col_idx = int(frac_x * cols)
    row_idx = int(frac_y * rows)

    col_idx = max(0, min(cols - 1, col_idx))
    row_idx = max(0, min(rows - 1, row_idx))

    return float(raster[row_idx, col_idx])


def compute_edge_raster_hazard(
    u_coord: Tuple[float, float],
    v_coord: Tuple[float, float],
    hazard_raster_data: Dict[str, Any],
    num_samples: int = 15,
) -> Tuple[float, float]:
    """Sample points along a straight or segmented edge and compute (max_hazard, mean_hazard).

    Args:
        u_coord: (lon, lat) of start node.
        v_coord: (lon, lat) of end node.
        hazard_raster_data: Dictionary containing 'raster' and 'bounds'.
        num_samples: Number of equidistant sampling points along the edge segment.

    Returns:
        Tuple of (max_hazard, mean_hazard), each in [0.0, 1.0].
    """
    u_lon, u_lat = u_coord
    v_lon, v_lat = v_coord

    raster = hazard_raster_data["raster"]
    bounds = hazard_raster_data["bounds"]

    lons = np.linspace(u_lon, v_lon, num_samples)
    lats = np.linspace(u_lat, v_lat, num_samples)

    sampled_values = [
        sample_raster_at_point(lon, lat, raster, bounds)
        for lon, lat in zip(lons, lats)
    ]

    max_val = float(np.max(sampled_values)) if sampled_values else 0.0
    mean_val = float(np.mean(sampled_values)) if sampled_values else 0.0

    return max_val, mean_val


def attach_hazard_to_graph(
    graph: Union[nx.Graph, nx.DiGraph, nx.MultiDiGraph],
    hazard_data: Dict[str, Any],
    num_samples_per_edge: int = 12,
    default_hazard: float = 0.0,
) -> Union[nx.Graph, nx.DiGraph, nx.MultiDiGraph]:
    """Attach the maximum intersecting hazard score to every edge in the graph.

    Mutates and returns the graph with the following edge attributes:
    - `max_hazard`: Peak hazard value intersecting the edge in [0.0, 1.0]
    - `mean_hazard`: Mean hazard value along the edge
    - `hazard_score`: Primary hazard metric (defaults to max_hazard per project requirements)
    - `hazard_exposure`: Edge length (m) * hazard_score

    Args:
        graph: NetworkX graph with node 'x' (lon) and 'y' (lat).
        hazard_data: Standardized hazard raster or polygon layer.
        num_samples_per_edge: Sampling density along each road edge.
        default_hazard: Baseline hazard if an edge falls outside raster bounds.

    Returns:
        The mutated graph with attached hazard attributes.
    """
    is_multigraph = isinstance(graph, (nx.MultiGraph, nx.MultiDiGraph))

    edge_iter = graph.edges(keys=True, data=True) if is_multigraph else graph.edges(data=True)

    for edge_tuple in edge_iter:
        if is_multigraph:
            u, v, k, data = edge_tuple
        else:
            u, v, data = edge_tuple

        u_node = graph.nodes[u]
        v_node = graph.nodes[v]

        # Get endpoints coordinates
        if "x" in u_node and "y" in u_node and "x" in v_node and "y" in v_node:
            u_coord = (float(u_node["x"]), float(u_node["y"]))
            v_coord = (float(v_node["x"]), float(v_node["y"]))

            # Optional edge geometry support if line polyline is attached
            if "geometry" in data and isinstance(data["geometry"], LineString):
                coords = list(data["geometry"].coords)
                max_h = 0.0
                mean_h_acc = []
                for i in range(len(coords) - 1):
                    seg_max, seg_mean = compute_edge_raster_hazard(
                        coords[i][:2], coords[i+1][:2], hazard_data, num_samples=max(3, num_samples_per_edge // len(coords))
                    )
                    max_h = max(max_h, seg_max)
                    mean_h_acc.append(seg_mean)
                mean_h = float(np.mean(mean_h_acc)) if mean_h_acc else 0.0
            else:
                max_h, mean_h = compute_edge_raster_hazard(
                    u_coord, v_coord, hazard_data, num_samples=num_samples_per_edge
                )
        else:
            max_h, mean_h = default_hazard, default_hazard

        edge_length = float(data.get("length", 100.0))

        # Attach per-edge hazard attributes
        data["max_hazard"] = round(float(max_h), 4)
        data["mean_hazard"] = round(float(mean_h), 4)
        data["hazard_score"] = data["max_hazard"] # Max-intersecting hazard per edge requirement
        data["hazard_exposure"] = round(edge_length * data["hazard_score"], 2)

    return graph
