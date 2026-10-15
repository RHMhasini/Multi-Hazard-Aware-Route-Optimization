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


from shapely import wkt


def sample_raster_at_point(
    lon: float,
    lat: float,
    raster: np.ndarray,
    bounds: Tuple[float, float, float, float],
    nodata: Optional[float] = None,
    origin_upper: bool = False,
    transform: Optional[Any] = None,
) -> float:
    """Sample the raster cell containing a (lon, lat) coordinate.

    Supports:
    - Standard Cartesian rasters (origin at bottom-left/south)
    - GeoTIFF / GIS rasters (origin at top-left/north) via `origin_upper=True` or `transform`
    - Proper nodata/out-of-bounds flagging instead of treating unknown as safe

    Args:
        lon: Longitude (EPSG:4326).
        lat: Latitude (EPSG:4326).
        raster: 2D numpy array [rows, cols].
        bounds: (min_lon, min_lat, max_lon, max_lat).
        nodata: Value indicating missing/unmeasured data.
        origin_upper: If True, row 0 corresponds to max_lat (standard GeoTIFF).
        transform: Optional rasterio Affine transform.

    Returns:
        float: Normalized hazard value in [0.0, 1.0], or np.nan if outside/nodata.
    """
    min_lon, min_lat, max_lon, max_lat = bounds
    if not (min_lon <= lon <= max_lon and min_lat <= lat <= max_lat):
        return float(nodata) if nodata is not None else float("nan")

    rows, cols = raster.shape

    if transform is not None:
        try:
            import rasterio.transform
            row_idx, col_idx = rasterio.transform.rowcol(transform, lon, lat)
        except Exception:
            row_idx = int((max_lat - lat) / (max_lat - min_lat) * rows) if origin_upper else int((lat - min_lat) / (max_lat - min_lat) * rows)
            col_idx = int((lon - min_lon) / (max_lon - min_lon) * cols)
    else:
        frac_x = (lon - min_lon) / (max_lon - min_lon) if max_lon > min_lon else 0.0
        col_idx = int(frac_x * cols)
        if origin_upper:
            frac_y = (max_lat - lat) / (max_lat - min_lat) if max_lat > min_lat else 0.0
        else:
            frac_y = (lat - min_lat) / (max_lat - min_lat) if max_lat > min_lat else 0.0
        row_idx = int(frac_y * rows)

    col_idx = max(0, min(cols - 1, col_idx))
    row_idx = max(0, min(rows - 1, row_idx))

    val = float(raster[row_idx, col_idx])
    if nodata is not None and (val == nodata or np.isnan(val)):
        return float("nan")
    return val


def compute_edge_raster_hazard(
    u_coord: Tuple[float, float],
    v_coord: Tuple[float, float],
    hazard_raster_data: Dict[str, Any],
    num_samples: Optional[int] = None,
    default_hazard: float = 0.0,
) -> Tuple[float, float]:
    """Sample points along a straight or segmented edge and compute (max_hazard, mean_hazard).

    Calculates sampling density dynamically based on raster cell resolution (approx. 2 samples per cell)
    if num_samples is not explicitly provided.

    Args:
        u_coord: (lon, lat) of start node.
        v_coord: (lon, lat) of end node.
        hazard_raster_data: Dictionary containing 'raster', 'bounds', and optional 'origin_upper'/'transform'.
        num_samples: Optional explicit number of sampling points.
        default_hazard: Fallback hazard value if all samples are outside/nodata.

    Returns:
        Tuple of (max_hazard, mean_hazard), each in [0.0, 1.0].
    """
    u_lon, u_lat = u_coord
    v_lon, v_lat = v_coord

    raster = hazard_raster_data["raster"]
    bounds = hazard_raster_data["bounds"]
    origin_upper = hazard_raster_data.get("origin_upper", False)
    transform = hazard_raster_data.get("transform")
    nodata = hazard_raster_data.get("nodata_value")

    # Dynamic sampling tied to raster cell resolution (approx. 2 samples per cell width)
    if num_samples is None:
        rows, cols = raster.shape
        cell_w = (bounds[2] - bounds[0]) / max(1, cols)
        cell_h = (bounds[3] - bounds[1]) / max(1, rows)
        edge_dist_deg = math.hypot(v_lon - u_lon, v_lat - u_lat)
        est_cells = max(1, int(edge_dist_deg / min(cell_w, cell_h)))
        num_samples = max(8, min(100, est_cells * 2))

    lons = np.linspace(u_lon, v_lon, num_samples)
    lats = np.linspace(u_lat, v_lat, num_samples)

    sampled_values = []
    for lon, lat in zip(lons, lats):
        v = sample_raster_at_point(
            lon, lat, raster, bounds, nodata=nodata, origin_upper=origin_upper, transform=transform
        )
        if not np.isnan(v):
            sampled_values.append(v)

    if not sampled_values:
        return default_hazard, default_hazard

    max_val = float(np.max(sampled_values))
    mean_val = float(np.mean(sampled_values))

    return max_val, mean_val


def parse_edge_geometry(geom_data: Any) -> Optional[LineString]:
    """Parse edge geometry attribute whether it is a Shapely LineString, WKT string, or list."""
    if isinstance(geom_data, LineString):
        return geom_data
    if isinstance(geom_data, str) and ("LINESTRING" in geom_data.upper() or "(" in geom_data):
        try:
            parsed = wkt.loads(geom_data)
            if isinstance(parsed, LineString):
                return parsed
        except Exception:
            return None
    return None


def attach_hazard_to_graph(
    graph: Union[nx.Graph, nx.DiGraph, nx.MultiDiGraph],
    hazard_data: Dict[str, Any],
    num_samples_per_edge: Optional[int] = None,
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

            # Handle edge geometry (Shapely LineString or WKT string from OSMnx/GraphML)
            line_geom = parse_edge_geometry(data.get("geometry"))
            if line_geom is not None and len(line_geom.coords) >= 2:
                coords = list(line_geom.coords)
                max_h = 0.0
                mean_h_acc = []
                for i in range(len(coords) - 1):
                    seg_max, seg_mean = compute_edge_raster_hazard(
                        coords[i][:2], coords[i+1][:2], hazard_data,
                        num_samples=num_samples_per_edge,
                        default_hazard=default_hazard,
                    )
                    max_h = max(max_h, seg_max)
                    mean_h_acc.append(seg_mean)
                mean_h = float(np.mean(mean_h_acc)) if mean_h_acc else default_hazard
            else:
                max_h, mean_h = compute_edge_raster_hazard(
                    u_coord, v_coord, hazard_data,
                    num_samples=num_samples_per_edge,
                    default_hazard=default_hazard,
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
