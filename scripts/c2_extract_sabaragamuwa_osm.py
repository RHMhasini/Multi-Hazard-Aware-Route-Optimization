#!/usr/bin/env python
"""
Component 2 - Step 2: Raw OSM Road Network Extraction for Sabaragamuwa Province.

Reads the downloaded Sri Lanka OSM PBF locally with pyosmium and builds an
unsimplified directed road multigraph for the entire Sabaragamuwa Province.
"""

from __future__ import annotations

import json
import hashlib
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import networkx as nx
import osmium
from pyproj import Geod
from shapely.geometry import GeometryCollection, LineString, MultiLineString, Point
from shapely.ops import unary_union
from shapely.prepared import prep
from shapely.validation import make_valid

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DATA_RAW_DIR = PROJECT_ROOT / "data" / "raw"

GRAPHML_FILENAME = "c2_sabaragamuwa_raw_drive_network.graphml"
METADATA_FILENAME = "c2_sabaragamuwa_osm_metadata.json"
BOUNDARY_FILENAME = "c2_sabaragamuwa_boundary.geojson"

GRAPHML_PATH = DATA_RAW_DIR / GRAPHML_FILENAME
METADATA_PATH = DATA_RAW_DIR / METADATA_FILENAME
BOUNDARY_PATH = DATA_RAW_DIR / BOUNDARY_FILENAME
PBF_PATH = PROJECT_ROOT / "cache" / "sri-lanka-latest.osm.pbf"

STUDY_AREA = "Sabaragamuwa Province, Sri Lanka"
COUNTRY = "Sri Lanka"
NETWORK_TYPE = "drive"
CRS = "EPSG:4326"
LOCATION_INDEX = "sparse_file_array"

ROAD_TAGS = (
    "highway",
    "oneway",
    "bridge",
    "tunnel",
    "layer",
    "maxspeed",
    "lanes",
    "ref",
    "name",
    "junction",
    "access",
    "motor_vehicle",
    "vehicle",
    "service",
)
GRAPHML_TAGS = ROAD_TAGS + ("oneway_osm",)
DRIVE_HIGHWAY_TYPES = {
    "motorway",
    "motorway_link",
    "trunk",
    "trunk_link",
    "primary",
    "primary_link",
    "secondary",
    "secondary_link",
    "tertiary",
    "tertiary_link",
    "unclassified",
    "residential",
    "living_street",
    "service",
    "road",
}
GEOD = Geod(ellps="WGS84")


def normalize_boundary(gdf: gpd.GeoDataFrame) -> tuple[gpd.GeoDataFrame, object]:
    """Validate, combine, and normalize all returned boundary geometries."""
    if gdf.empty or gdf.geometry.empty:
        raise RuntimeError("The geocoded boundary contains no geometry.")
    if gdf.crs is None:
        raise RuntimeError("The geocoded boundary has no CRS.")
    normalized = gdf.to_crs(CRS)
    geometry = make_valid(unary_union(normalized.geometry.tolist()))
    if geometry.is_empty or not geometry.is_valid:
        raise RuntimeError("The combined province boundary is invalid.")
    output = gpd.GeoDataFrame(
        {"study_area": [STUDY_AREA]},
        geometry=[geometry],
        crs=CRS,
    )
    return output, geometry


def geocode_boundary(study_area: str) -> tuple[gpd.GeoDataFrame, object]:
    """Obtain and validate the complete province boundary."""
    import osmnx as ox

    print("\n[1/5] Geocoding boundary: {!r} ...".format(study_area))
    gdf = ox.geocode_to_gdf(study_area)
    if gdf.empty:
        raise RuntimeError("Nominatim returned no boundary for {!r}.".format(study_area))
    gdf, geometry = normalize_boundary(gdf)
    print("    Bounding box: {}".format(geometry.bounds))
    return gdf, geometry


def _tag_value(tags: object, key: str) -> str:
    """Return a GraphML-safe string for one OSM tag."""
    value = tags.get(key)
    if value is None:
        return ""
    return str(value)


def _synthetic_node_id(way_id: int, segment_index: int, x: float, y: float) -> int:
    """Create a deterministic negative ID that cannot collide with OSM IDs."""
    token = "{0}:{1}:{2:.12f}:{3:.12f}".format(way_id, segment_index, x, y)
    digest = hashlib.sha256(token.encode("ascii")).hexdigest()
    return -1 - int(digest[:15], 16)


def _line_parts(geometry: object) -> list[LineString]:
    """Return line components from a Shapely intersection result."""
    if isinstance(geometry, LineString):
        return [geometry]
    if isinstance(geometry, MultiLineString):
        return list(geometry.geoms)
    if isinstance(geometry, GeometryCollection):
        return [part for part in geometry.geoms if isinstance(part, LineString)]
    return []


def clip_segment_to_boundary(segment: LineString, boundary: object) -> list[LineString]:
    """Return only the line portions inside the province boundary."""
    return [part for part in _line_parts(segment.intersection(boundary)) if part.length > 0]


def _direction_pairs(tags: dict[str, str]) -> list[tuple[int, int]]:
    """Interpret basic OSM direction tags for the initial directed graph.

    This extraction stage interprets explicit OSM directional tags to construct
    the initial directed graph. A later direction-normalization stage is
    responsible for cleaning and standardizing inconsistencies and special
    cases; topology healing is intentionally not performed here.
    """
    oneway = tags["oneway"].strip().lower()
    junction = tags["junction"].strip().lower()
    if oneway == "-1":
        return [(1, 0)]
    if oneway in {"yes", "true", "1"}:
        return [(0, 1)]
    if junction == "roundabout" and oneway == "":
        return [(0, 1)]
    if oneway in {"", "no"}:
        return [(0, 1), (1, 0)]
    # Unusual values remain preserved in the edge tag; use a documented,
    # conservative bidirectional fallback.
    return [(0, 1), (1, 0)]


def _graphml_oneway_value(tags: dict[str, str]) -> bool:
    """Return OSMnx-compatible oneway while retaining the original value."""
    return bool(_direction_pairs(tags) == [(0, 1)])


class _RoadHandler(osmium.SimpleHandler):
    """Collect drive-road ways and their segment coordinates from a PBF."""

    def __init__(self, boundary: object, pbf_path: Path) -> None:
        super().__init__()
        self.boundary = prep(boundary)
        self.boundary_geometry = boundary
        self.pbf_path = pbf_path
        self.graph = nx.MultiDiGraph()
        self.graph.graph.update(
            {
                "crs": CRS,
                "network_type": NETWORK_TYPE,
                "simplify": False,
                "retain_all": True,
                "source": "OpenStreetMap",
                "source_file": str(pbf_path.relative_to(PROJECT_ROOT)),
                "study_area": STUDY_AREA,
            }
        )
        self.way_count = 0
        self.segment_count = 0

    def way(self, way: object) -> None:
        highway = str(way.tags.get("highway", ""))
        if highway not in DRIVE_HIGHWAY_TYPES:
            return

        way_nodes = list(way.nodes)
        points = [
            (float(node.lon), float(node.lat))
            for node in way_nodes
            if node.location.valid()
        ]
        if len(points) != len(way_nodes) or len(points) < 2:
            return

        tags = {key: _tag_value(way.tags, key) for key in ROAD_TAGS}
        tags["oneway_osm"] = tags["oneway"]
        directions = _direction_pairs(tags)
        tags["oneway"] = _graphml_oneway_value(tags)

        for index, (start, end) in enumerate(zip(way_nodes, way_nodes[1:])):
            start_xy = points[index]
            end_xy = points[index + 1]
            segment = LineString([start_xy, end_xy])
            for clipped in clip_segment_to_boundary(segment, self.boundary_geometry):
                clipped_start = (clipped.coords[0][0], clipped.coords[0][1])
                clipped_end = (clipped.coords[-1][0], clipped.coords[-1][1])
                start_id = (
                    int(start.ref)
                    if self.boundary.covers(Point(start_xy))
                    and Point(start_xy).distance(Point(clipped_start)) < 1e-12
                    else _synthetic_node_id(int(way.id), index, *clipped_start)
                )
                end_id = (
                    int(end.ref)
                    if self.boundary.covers(Point(end_xy))
                    and Point(end_xy).distance(Point(clipped_end)) < 1e-12
                    else _synthetic_node_id(int(way.id), index, *clipped_end)
                )
                self.graph.add_node(start_id, x=clipped_start[0], y=clipped_start[1])
                self.graph.add_node(end_id, x=clipped_end[0], y=clipped_end[1])
                _, _, length = GEOD.inv(
                    clipped_start[0], clipped_start[1],
                    clipped_end[0], clipped_end[1],
                )
                for u_index, v_index in directions:
                    edge_start, edge_end = (
                        (start_id, end_id) if (u_index, v_index) == (0, 1)
                        else (end_id, start_id)
                    )
                    self.graph.add_edge(
                        edge_start,
                        edge_end,
                        osmid=int(way.id),
                        length=float(length),
                        **tags,
                    )
                    self.segment_count += 1
        self.way_count += 1


def extract_network_from_pbf(pbf_path: Path, boundary: object) -> nx.MultiDiGraph:
    """Read a local PBF and return the unsimplified, retain-all road graph."""
    if not pbf_path.is_file():
        raise FileNotFoundError("Local OSM PBF not found: {}".format(pbf_path))

    print("\n[2/5] Reading local OSM PBF -> {}".format(pbf_path))
    handler = _RoadHandler(boundary, pbf_path)
    handler.apply_file(str(pbf_path), locations=True, idx=LOCATION_INDEX)
    print("    Road ways kept: {:,}".format(handler.way_count))
    print("    Directed segments: {:,}".format(handler.segment_count))
    return handler.graph


def validate_graph(graph: nx.MultiDiGraph) -> dict:
    """Perform basic non-destructive validation of the extracted graph."""
    if not isinstance(graph, nx.MultiDiGraph):
        raise RuntimeError("Expected MultiDiGraph, got {}.".format(type(graph).__name__))
    issues = []
    if len(graph.nodes) == 0:
        issues.append("Graph has 0 nodes.")
    if len(graph.edges) == 0:
        issues.append("Graph has 0 edges.")
    if graph.graph.get("crs") != CRS:
        issues.append("Graph CRS is not EPSG:4326.")
    return {
        "node_count": len(graph.nodes),
        "edge_count": len(graph.edges),
        "crs": graph.graph.get("crs"),
        "is_directed": graph.is_directed(),
        "graph_type": type(graph).__name__,
        "validation_issues": issues,
    }


def save_graphml(graph: nx.MultiDiGraph, path: Path) -> None:
    """Save the raw graph as GraphML using OSMnx serialization."""
    import osmnx as ox

    path.parent.mkdir(parents=True, exist_ok=True)
    ox.save_graphml(graph, filepath=str(path))


def save_metadata(
    stats: dict,
    boundary_saved: bool,
    elapsed_total: float,
    pbf_path: Path,
    status: str = "success",
    error: BaseException | None = None,
) -> None:
    """Write reproducibility and graph-validation metadata."""
    metadata = {
        "extraction_datetime": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "study_area": STUDY_AREA,
        "country": COUNTRY,
        "source": "OpenStreetMap",
        "source_file": str(pbf_path.relative_to(PROJECT_ROOT)),
        "network_type": NETWORK_TYPE,
        "extraction_method": "pyosmium local PBF way/segment extraction clipped to province boundary",
        "simplify": False,
        "retain_all": True,
        "crs": stats.get("crs"),
        "original_crs": stats.get("crs"),
        "node_count": stats.get("node_count"),
        "edge_count": stats.get("edge_count"),
        "is_directed": stats.get("is_directed"),
        "graph_type": stats.get("graph_type"),
        "road_tags": list(GRAPHML_TAGS),
        "boundary_filename": BOUNDARY_FILENAME if boundary_saved else None,
        "raw_graph_filename": GRAPHML_FILENAME,
        "extraction_total_seconds": round(elapsed_total, 1),
        "validation_issues": stats.get("validation_issues", []),
    }
    if error is not None:
        metadata["error_type"] = type(error).__name__
        metadata["error_message"] = str(error)
    METADATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    METADATA_PATH.write_text(json.dumps(metadata, indent=2), encoding="utf-8")


def main() -> int:
    """Run the complete local-PBF extraction pipeline."""
    start = time.time()
    stats = {}
    boundary_saved = False
    try:
        gdf, boundary = geocode_boundary(STUDY_AREA)
        graph = extract_network_from_pbf(PBF_PATH, boundary)
        stats = validate_graph(graph)
        if stats["validation_issues"]:
            raise RuntimeError("Graph validation failed: {}".format(stats["validation_issues"]))

        print("\n[3/5] Validation passed: {:,} nodes, {:,} edges.".format(
            stats["node_count"], stats["edge_count"]
        ))
        print("[4/5] Saving GraphML -> {}".format(GRAPHML_PATH))
        save_graphml(graph, GRAPHML_PATH)
        print("[5/5] Saving boundary and metadata.")
        gdf.to_file(str(BOUNDARY_PATH), driver="GeoJSON")
        boundary_saved = True
        save_metadata(stats, boundary_saved, time.time() - start, PBF_PATH)
    except Exception as exc:
        print("[ERROR] Extraction failed: {}".format(exc))
        save_metadata(
            stats,
            boundary_saved,
            time.time() - start,
            PBF_PATH,
            status="failed",
            error=exc,
        )
        return 1

    print("Extraction complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
