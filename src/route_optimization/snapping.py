"""Origin-Destination coordinate snapping engine for Component 3.

Connects arbitrary geographic coordinate queries (e.g. Ratnapura General Hospital,
relief centers, emergency shelters) to the closest valid nodes or edges in the road network graph.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple, Union, Any
import numpy as np
import networkx as nx
from scipy.spatial import cKDTree


class CoordinateSnapper:
    """Fast spatial index for snapping (lon, lat) queries to graph vertices."""

    def __init__(self, graph: nx.DiGraph):
        """Build a spatial KD-Tree from graph node coordinates.

        Args:
            graph: Directed road network with 'x' (lon) and 'y' (lat) node attributes.
        """
        self.graph = graph
        self.node_ids: List[Union[str, int]] = []
        coords: List[Tuple[float, float]] = []

        for node_id, data in graph.nodes(data=True):
            if "x" in data and "y" in data:
                self.node_ids.append(node_id)
                coords.append((float(data["x"]), float(data["y"])))

        if not coords:
            raise ValueError("Cannot initialize CoordinateSnapper: graph contains no nodes with 'x' and 'y' attributes.")

        self.coords_array = np.array(coords, dtype=np.float64)  # shape (N, 2): [lon, lat]
        # Reference latitude for metric scaling
        self.ref_lat = float(np.mean(self.coords_array[:, 1]))
        self.meters_per_deg_lat = 110500.0
        self.meters_per_deg_lon = 110500.0 * math.cos(math.radians(self.ref_lat))

        # Project coordinates to approximate planar metric coordinates for KD-Tree search
        self.projected_coords = np.column_stack([
            self.coords_array[:, 0] * self.meters_per_deg_lon,
            self.coords_array[:, 1] * self.meters_per_deg_lat,
        ])
        self.kdtree = cKDTree(self.projected_coords)

    def snap_coordinate(
        self,
        lon: float,
        lat: float,
        max_distance_m: float = 5000.0,
    ) -> Tuple[Union[str, int], float]:
        """Snap a single (lon, lat) coordinate to the nearest road network node.

        Args:
            lon: Query longitude (EPSG:4326).
            lat: Query latitude (EPSG:4326).
            max_distance_m: Maximum acceptable snapping distance in meters (default: 5000m).

        Returns:
            Tuple of (nearest_node_id, distance_in_meters).

        Raises:
            ValueError: If the closest node exceeds max_distance_m.
        """
        query_proj = np.array([lon * self.meters_per_deg_lon, lat * self.meters_per_deg_lat])
        dist_m, idx = self.kdtree.query(query_proj)

        if dist_m > max_distance_m:
            raise ValueError(
                f"Nearest graph node is {dist_m:.1f}m away from ({lon:.5f}, {lat:.5f}), "
                f"which exceeds the maximum snapping threshold of {max_distance_m:.1f}m."
            )

        snapped_node = self.node_ids[idx]
        return snapped_node, float(dist_m)


def snap_coordinate_to_node(
    graph: nx.DiGraph,
    lon: float,
    lat: float,
    max_distance_m: float = 5000.0,
) -> Tuple[Union[str, int], float]:
    """Find the nearest graph node to a given (lon, lat) coordinate.

    Uses Euclidean approximation adjusted for latitude:
        dx = (node_lon - lon) * 110500.0 * cos(radians(lat))
        dy = (node_lat - lat) * 110500.0
        dist_m = sqrt(dx^2 + dy^2)

    Args:
        graph: NetworkX DiGraph with node 'x' (lon) and 'y' (lat).
        lon: Target longitude.
        lat: Target latitude.
        max_distance_m: Snapping distance threshold in meters.

    Returns:
        Tuple of (nearest_node_id, distance_in_meters).

    Raises:
        ValueError: If graph has no nodes or nearest node exceeds max_distance_m.
    """
    if len(graph) == 0:
        raise ValueError("Cannot snap coordinate: graph is empty.")

    meters_per_deg_lat = 110500.0
    meters_per_deg_lon = 110500.0 * math.cos(math.radians(lat))

    best_node = None
    min_dist_m = float("inf")

    for node_id, data in graph.nodes(data=True):
        if "x" not in data or "y" not in data:
            continue
        nx_lon = float(data["x"])
        ny_lat = float(data["y"])

        dx = (nx_lon - lon) * meters_per_deg_lon
        dy = (ny_lat - lat) * meters_per_deg_lat
        dist_m = math.hypot(dx, dy)

        if dist_m < min_dist_m:
            min_dist_m = dist_m
            best_node = node_id

    if best_node is None:
        raise ValueError("No valid nodes with 'x' and 'y' coordinate attributes found in graph.")

    if min_dist_m > max_distance_m:
        raise ValueError(
            f"Nearest node {best_node} is {min_dist_m:.1f}m from ({lon:.5f}, {lat:.5f}), "
            f"exceeding max allowed radius of {max_distance_m:.1f}m."
        )

    return best_node, round(min_dist_m, 2)


def snap_od_coordinates(
    graph: nx.DiGraph,
    origin_coord: Tuple[float, float],
    destination_coord: Tuple[float, float],
    max_distance_m: float = 5000.0,
) -> Dict[str, Any]:
    """Snap an Origin-Destination coordinate query pair to road network nodes.

    Args:
        graph: Road network graph.
        origin_coord: (lon, lat) of emergency origin (e.g. dispatch depot).
        destination_coord: (lon, lat) of destination (e.g. disaster site / hospital).
        max_distance_m: Max snapping distance in meters.

    Returns:
        Dict with keys:
            - 'origin_node': Snapped origin node ID
            - 'origin_dist_m': Snapping distance to origin node
            - 'destination_node': Snapped destination node ID
            - 'destination_dist_m': Snapping distance to destination node
            - 'origin_coord': Original origin (lon, lat)
            - 'destination_coord': Original destination (lon, lat)
    """
    orig_node, orig_dist = snap_coordinate_to_node(
        graph, origin_coord[0], origin_coord[1], max_distance_m=max_distance_m
    )
    dest_node, dest_dist = snap_coordinate_to_node(
        graph, destination_coord[0], destination_coord[1], max_distance_m=max_distance_m
    )

    return {
        "origin_node": orig_node,
        "origin_dist_m": orig_dist,
        "destination_node": dest_node,
        "destination_dist_m": dest_dist,
        "origin_coord": origin_coord,
        "destination_coord": destination_coord,
    }
