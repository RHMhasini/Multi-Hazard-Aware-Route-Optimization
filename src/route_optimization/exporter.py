"""GeoJSON exporter for Component 4 Emergency Dispatch Dashboard.

Transforms route solutions, coordinate traces, edge hazard metrics, and recommendations
into standard RFC 7946 GeoJSON FeatureCollections for map visualization.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
import networkx as nx

from contracts.schemas import C3RouteResultContract
from src.route_optimization.metrics import calculate_route_metrics


def export_routes_to_geojson(
    graph: nx.DiGraph,
    routes: Dict[str, Any],
    recommended_route_id: Optional[str] = None,
    origin_coord: Optional[Union[List[float], Tuple[float, float]]] = None,
    destination_coord: Optional[Union[List[float], Tuple[float, float]]] = None,
    output_filepath: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """Convert candidate and recommended routing solutions into standard GeoJSON format.

    Args:
        graph: Routing graph with node 'x' (lon) and 'y' (lat) coordinates.
        routes: Dictionary mapping route names/keys to node path lists or C3RouteResultContract objects.
        recommended_route_id: Optional identifier/key of the primary recommended route.
        origin_coord: Optional original query (lon, lat) of emergency origin.
        destination_coord: Optional original query (lon, lat) of emergency destination.
        output_filepath: Optional file path to save the resulting .geojson file.

    Returns:
        Dict: Standard GeoJSON FeatureCollection dictionary.
    """
    features: List[Dict[str, Any]] = []

    # 1. Add Point features for Origin and Destination markers if available
    if origin_coord is not None:
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [float(origin_coord[0]), float(origin_coord[1])],
            },
            "properties": {
                "marker_type": "origin",
                "label": "Emergency Origin / Dispatch Depot",
            },
        })

    if destination_coord is not None:
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [float(destination_coord[0]), float(destination_coord[1])],
            },
            "properties": {
                "marker_type": "destination",
                "label": "Incident Site / Relief Destination",
            },
        })

    # 2. Add LineString features for each candidate route
    for route_key, route_val in routes.items():
        if route_val is None:
            continue

        # Extract node sequence and metadata
        if isinstance(route_val, C3RouteResultContract):
            path = route_val.node_path
            algorithm_name = route_val.algorithm
            audit_notes = route_val.audit_notes
            metrics = route_val.metrics
            is_rec = True
        elif isinstance(route_val, dict) and "path" in route_val:
            path = route_val["path"]
            algorithm_name = route_val.get("algorithm", route_key)
            audit_notes = route_val.get("audit_notes")
            metrics = calculate_route_metrics(graph, path)
            is_rec = (route_key == recommended_route_id or route_val.get("is_recommended", False))
        elif isinstance(route_val, list):
            path = route_val
            algorithm_name = str(route_key)
            audit_notes = None
            metrics = calculate_route_metrics(graph, path)
            is_rec = (route_key == recommended_route_id)
        else:
            continue

        if len(path) < 2:
            continue

        # Build coordinate line: list of [lon, lat]
        coordinates: List[List[float]] = []
        for node in path:
            if node in graph and "x" in graph.nodes[node] and "y" in graph.nodes[node]:
                lon = float(graph.nodes[node]["x"])
                lat = float(graph.nodes[node]["y"])
                coordinates.append([round(lon, 6), round(lat, 6)])

        if len(coordinates) < 2:
            continue

        feature = {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": coordinates,
            },
            "properties": {
                "route_id": str(route_key),
                "algorithm": algorithm_name,
                "is_recommended": is_rec,
                "travel_time_sec": metrics.total_travel_time_sec,
                "travel_time_min": round(metrics.total_travel_time_sec / 60.0, 2),
                "distance_km": round(metrics.total_distance_m / 1000.0, 2),
                "distance_m": metrics.total_distance_m,
                "cumulative_hazard_exposure": metrics.cumulative_hazard_exposure,
                "max_edge_hazard": metrics.max_edge_hazard,
                "threshold_penalty": metrics.threshold_penalty,
                "edge_count": metrics.edge_count,
                "node_count": len(path),
                "audit_notes": audit_notes,
            },
        }
        features.append(feature)

    feature_collection = {
        "type": "FeatureCollection",
        "crs": {
            "type": "name",
            "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"},
        },
        "metadata": {
            "generator": "Multi-Hazard-Aware Route Optimization Engine (Component 3)",
            "target_region": "Ratnapura District, Sabaragamuwa Province, Sri Lanka",
            "feature_count": len(features),
        },
        "features": features,
    }

    if output_filepath is not None:
        out_path = Path(output_filepath)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(feature_collection, f, indent=2)

    return feature_collection
