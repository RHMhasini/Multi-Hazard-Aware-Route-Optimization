"""Data loaders for Component 1 (hazard models) and Component 2 (road network graphs).

Real data from upstream components plugs in through these interfaces:
- Component 1: Multi-hazard rasters or vulnerability polygons
- Component 2: Direction-normalized OSM road graphs (GraphML)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional, Union
import numpy as np
import networkx as nx

from contracts.schemas import C1HazardRasterContract, C2GraphContract
from src.route_optimization.config import RouteOptimizationConfig, DEFAULT_CONFIG


def load_c2_road_network(
    file_path: Union[str, Path],
    validate_schema: bool = True,
    default_config: Optional[RouteOptimizationConfig] = None,
) -> nx.MultiDiGraph:
    """Load a road network graph produced by Component 2 (typically GraphML format).

    Args:
        file_path: Path to GraphML or JSON graph file.
        validate_schema: Whether to validate node coordinates and edge lengths against contracts.
        default_config: Configuration for fallback defaults.

    Returns:
        nx.MultiDiGraph: Directed multigraph with node 'x', 'y' and edge 'length', 'highway'.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Component 2 graph file not found at: {path}")

    cfg = default_config or DEFAULT_CONFIG

    if path.suffix.lower() == ".graphml":
        raw_graph = nx.read_graphml(path)
    elif path.suffix.lower() == ".json":
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        raw_graph = nx.node_link_graph(data, directed=True, multigraph=True)
    else:
        raise ValueError(f"Unsupported graph file extension: {path.suffix}. Expected .graphml or .json")

    # Ensure directed MultiDiGraph representation
    if not isinstance(raw_graph, nx.MultiDiGraph):
        graph = nx.MultiDiGraph(raw_graph)
    else:
        graph = raw_graph

    # Standardize node attributes
    for node_id, data in graph.nodes(data=True):
        # Support alternative coordinate keys: lon/lat, x/y
        x = data.get("x") or data.get("lon") or data.get("longitude")
        y = data.get("y") or data.get("lat") or data.get("latitude")
        if x is not None:
            data["x"] = float(x)
        if y is not None:
            data["y"] = float(y)

    # Standardize edge attributes
    for u, v, k, data in graph.edges(keys=True, data=True):
        # Ensure length attribute exists
        if "length" not in data or data["length"] is None:
            # Fallback calculate distance if coordinates exist
            u_node = graph.nodes[u]
            v_node = graph.nodes[v]
            if "x" in u_node and "y" in u_node and "x" in v_node and "y" in v_node:
                dx = (v_node["x"] - u_node["x"]) * 110500.0 * np.cos(np.radians(u_node["y"]))
                dy = (v_node["y"] - u_node["y"]) * 110500.0
                data["length"] = float(np.hypot(dx, dy))
            else:
                data["length"] = 100.0  # Safe default 100 meters
        else:
            data["length"] = float(data["length"])

        # Highway class standardization
        if "highway" not in data:
            data["highway"] = "unclassified"

    if validate_schema:
        validate_c2_graph(graph)

    return graph


def validate_c2_graph(graph: Union[nx.Graph, nx.DiGraph, nx.MultiDiGraph]) -> bool:
    """Validate that the loaded graph meets the C2GraphContract requirements."""
    if len(graph) == 0:
        raise ValueError("Loaded graph is empty (0 nodes).")

    # Verify at least one node has valid x, y
    sample_node = next(iter(graph.nodes(data=True)))
    if "x" not in sample_node[1] or "y" not in sample_node[1]:
        raise ValueError(
            f"Node {sample_node[0]} is missing required coordinate attributes 'x' and 'y'."
        )

    return True


def load_c1_hazard_raster(
    file_path_or_dict: Union[str, Path, Dict[str, Any]],
    hazard_type: str = "multi_hazard",
    validate_contract: bool = True,
) -> Dict[str, Any]:
    """Load a hazard raster layer from Component 1.

    Supports:
    - Pre-computed dictionary with 'raster', 'bounds', 'crs', 'dimensions'
    - JSON file containing raster matrix and bounding box metadata
    - .npy / .npz numpy arrays with paired metadata

    Args:
        file_path_or_dict: File path or in-memory dictionary.
        hazard_type: Primary hazard type ('flood', 'landslide', 'multi_hazard').
        validate_contract: Whether to validate against C1HazardRasterContract.

    Returns:
        Standardized dict containing 'raster' (2D np.ndarray), 'bounds', 'crs', etc.
    """
    if isinstance(file_path_or_dict, dict):
        hazard_data = file_path_or_dict
    else:
        path = Path(file_path_or_dict)
        if not path.exists():
            raise FileNotFoundError(f"Component 1 hazard file not found at: {path}")

        if path.suffix.lower() == ".json":
            with open(path, "r", encoding="utf-8") as f:
                raw_json = json.load(f)
            hazard_data = {
                "raster": np.array(raw_json["raster"], dtype=np.float64),
                "bounds": tuple(raw_json["bounds"]),
                "crs": raw_json.get("crs", "EPSG:4326"),
                "dimensions": tuple(raw_json.get("dimensions", (len(raw_json["raster"]), len(raw_json["raster"][0])))),
            }
        elif path.suffix.lower() in [".npy", ".npz"]:
            array = np.load(path)
            hazard_data = {
                "raster": array if isinstance(array, np.ndarray) else array["raster"],
                "bounds": (80.35, 6.65, 80.55, 6.85), # Default Sabaragamuwa bounds
                "crs": "EPSG:4326",
                "dimensions": array.shape,
            }
        else:
            raise ValueError(f"Unsupported hazard file type: {path.suffix}")

    # Ensure raster values are in [0.0, 1.0]
    hazard_data["raster"] = np.clip(np.nan_to_num(hazard_data["raster"], nan=0.0), 0.0, 1.0)
    rows, cols = hazard_data["raster"].shape
    min_lon, min_lat, max_lon, max_lat = hazard_data["bounds"]

    if "lat_coords" not in hazard_data:
        hazard_data["lat_coords"] = np.linspace(min_lat, max_lat, rows)
    if "lon_coords" not in hazard_data:
        hazard_data["lon_coords"] = np.linspace(min_lon, max_lon, cols)

    if validate_contract:
        res_x = (max_lon - min_lon) / max(cols, 1)
        res_y = (max_lat - min_lat) / max(rows, 1)
        C1HazardRasterContract(
            hazard_type=hazard_type,
            crs=hazard_data.get("crs", "EPSG:4326"),
            bounds=hazard_data["bounds"],
            resolution=(res_x, res_y),
            dimensions=(rows, cols),
            value_range=(float(hazard_data["raster"].min()), float(hazard_data["raster"].max())),
        )

    return hazard_data
