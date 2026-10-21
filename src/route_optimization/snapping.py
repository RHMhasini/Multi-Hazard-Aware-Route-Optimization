"""Origin-Destination coordinate snapping engine for Component 3.

Connects arbitrary geographic coordinate queries (e.g. illustrative facility coordinates,
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
        require_largest_component: bool = False,
    ) -> Tuple[Union[str, int], float]:
        """Snap a single (lon, lat) coordinate to the nearest road network node.

        Args:
            lon: Query longitude (EPSG:4326).
            lat: Query latitude (EPSG:4326).
            max_distance_m: Maximum acceptable snapping distance in meters (default: 5000m).
            require_largest_component: If True, restricts snapping candidate to nodes in the largest strongly connected component.

        Returns:
            Tuple of (nearest_node_id, distance_in_meters).

        Raises:
            ValueError: If the closest node exceeds max_distance_m.
        """
        query_proj = np.array([lon * self.meters_per_deg_lon, lat * self.meters_per_deg_lat])
        
        if require_largest_component and len(self.graph) > 0:
            sccs = list(nx.strongly_connected_components(self.graph))
            largest_scc = max(sccs, key=len) if sccs else set(self.graph.nodes())
            valid_indices = [i for i, nid in enumerate(self.node_ids) if nid in largest_scc]
            if valid_indices:
                sub_pts = self.projected_coords[valid_indices]
                dists = np.linalg.norm(sub_pts - query_proj, axis=1)
                min_i = int(np.argmin(dists))
                dist_m = float(dists[min_i])
                idx = valid_indices[min_i]
            else:
                dist_m, idx = self.kdtree.query(query_proj)
        else:
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
    require_largest_component: bool = False,
) -> Tuple[Union[str, int], float]:
    """Find the nearest graph node to a given (lon, lat) coordinate using spatial KD-Tree.

    Args:
        graph: NetworkX DiGraph with node 'x' (lon) and 'y' (lat).
        lon: Target longitude.
        lat: Target latitude.
        max_distance_m: Snapping distance threshold in meters.
        require_largest_component: If True, snaps only to nodes in the largest strongly connected component.

    Returns:
        Tuple of (nearest_node_id, distance_in_meters).

    Raises:
        ValueError: If graph has no nodes or nearest node exceeds max_distance_m.
    """
    if len(graph) == 0:
        raise ValueError("Cannot snap coordinate: graph is empty.")

    snapper = CoordinateSnapper(graph)
    node_id, dist_m = snapper.snap_coordinate(
        lon, lat, max_distance_m=max_distance_m, require_largest_component=require_largest_component
    )
    return node_id, round(dist_m, 2)


def snap_od_coordinates(
    graph: nx.DiGraph,
    origin_coord: Tuple[float, float],
    destination_coord: Tuple[float, float],
    max_distance_m: float = 5000.0,
    require_connected: bool = True,
) -> Dict[str, Any]:
    """Snap an Origin-Destination coordinate query pair to road network nodes using KD-Tree.

    Args:
        graph: Road network graph.
        origin_coord: (lon, lat) of emergency origin (e.g. dispatch depot).
        destination_coord: (lon, lat) of destination (e.g. disaster site / hospital).
        max_distance_m: Max snapping distance in meters.
        require_connected: If True, snaps to the largest strongly connected component to guarantee path reachability.

    Returns:
        Dict with keys:
            - 'origin_node': Snapped origin node ID
            - 'origin_dist_m': Snapping distance to origin node
            - 'destination_node': Snapped destination node ID
            - 'destination_dist_m': Snapping distance to destination node
            - 'origin_coord': Original origin (lon, lat)
            - 'destination_coord': Original destination (lon, lat)
    """
    snapper = CoordinateSnapper(graph)
    orig_node, orig_dist = snapper.snap_coordinate(
        origin_coord[0], origin_coord[1],
        max_distance_m=max_distance_m,
        require_largest_component=require_connected,
    )
    dest_node, dest_dist = snapper.snap_coordinate(
        destination_coord[0], destination_coord[1],
        max_distance_m=max_distance_m,
        require_largest_component=require_connected,
    )

    return {
        "origin_node": orig_node,
        "origin_dist_m": round(orig_dist, 2),
        "destination_node": dest_node,
        "destination_dist_m": round(dest_dist, 2),
        "origin_coord": origin_coord,
        "destination_coord": destination_coord,
    }
