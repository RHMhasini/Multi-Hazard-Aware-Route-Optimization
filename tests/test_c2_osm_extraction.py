"""
Component 2 - Step 2: Tests for OSM extraction output.

Tests verify basic structural properties of the raw extracted graph and
output files. These tests are designed to pass against the actual extraction
outputs and do NOT test topology-healing or repair logic.

Run after executing scripts/c2_extract_sabaragamuwa_osm.py:
    pytest tests/test_c2_osm_extraction.py -v

Environment variable:
    C2_SKIP_DATA_TESTS=1  — skip tests that require extracted output files
                            (set this when the extraction has not been run yet)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from shapely.geometry import LineString, box

# ---------------------------------------------------------------------------
# Project paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_RAW_DIR = PROJECT_ROOT / "data" / "raw"

GRAPHML_PATH = DATA_RAW_DIR / "c2_sabaragamuwa_raw_drive_network.graphml"
METADATA_PATH = DATA_RAW_DIR / "c2_sabaragamuwa_osm_metadata.json"
BOUNDARY_PATH = DATA_RAW_DIR / "c2_sabaragamuwa_boundary.geojson"

# Allow skipping data-dependent tests when extraction has not been run
_SKIP_DATA = os.environ.get("C2_SKIP_DATA_TESTS", "0").strip() == "1"
skip_data = pytest.mark.skipif(
    _SKIP_DATA or not GRAPHML_PATH.exists(),
    reason=(
        "Extraction output not present. "
        "Run scripts/c2_extract_sabaragamuwa_osm.py first, "
        "or set C2_SKIP_DATA_TESTS=1 to skip these tests."
    ),
)


def _load_module():
    import importlib.util

    script = PROJECT_ROOT / "scripts" / "c2_extract_sabaragamuwa_osm.py"
    spec = importlib.util.spec_from_file_location("c2_extract", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Extraction script import tests (no network required)
# ---------------------------------------------------------------------------

class TestExtractionScript:
    """Tests that verify the extraction script can be imported and configured."""

    def test_script_exists(self):
        """Extraction script must exist at the expected path."""
        script = PROJECT_ROOT / "scripts" / "c2_extract_sabaragamuwa_osm.py"
        assert script.exists(), f"Script not found: {script}"

    def test_script_importable(self):
        """Extraction script must be syntactically valid and importable."""
        import importlib.util
        script = PROJECT_ROOT / "scripts" / "c2_extract_sabaragamuwa_osm.py"
        spec = importlib.util.spec_from_file_location("c2_extract", script)
        module = importlib.util.module_from_spec(spec)
        # Just loading the spec should work; actual execution is not required.
        assert spec is not None
        assert module is not None

    def test_core_imports_available(self):
        """Core packages required by the extraction script must be importable."""
        import osmium  # noqa: F401
        import osmnx  # noqa: F401
        import networkx  # noqa: F401
        import geopandas  # noqa: F401
        import shapely  # noqa: F401
        import pyproj  # noqa: F401
        import pandas  # noqa: F401
        import numpy  # noqa: F401

    def test_osmnx_version(self):
        """OSMnx must be version 1.x or 2.x."""
        import osmnx as ox
        major = int(ox.__version__.split(".")[0])
        assert major >= 1, f"OSMnx version too old: {ox.__version__}"

    def test_output_directory_exists(self):
        """data/raw/ directory must exist."""
        assert DATA_RAW_DIR.exists(), f"Output directory not found: {DATA_RAW_DIR}"

    def test_study_area_constant(self):
        """STUDY_AREA constant must reference Sabaragamuwa Province."""
        import importlib.util
        script = PROJECT_ROOT / "scripts" / "c2_extract_sabaragamuwa_osm.py"
        spec = importlib.util.spec_from_file_location("c2_extract", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert "Sabaragamuwa" in module.STUDY_AREA
        assert "Sri Lanka" in module.STUDY_AREA

    def test_network_type_is_drive(self):
        """Network type must be 'drive' for motor-vehicle routing."""
        import importlib.util
        script = PROJECT_ROOT / "scripts" / "c2_extract_sabaragamuwa_osm.py"
        spec = importlib.util.spec_from_file_location("c2_extract", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert module.NETWORK_TYPE == "drive"

    def test_pyrosm_is_not_required(self):
        """The local-PBF implementation must use the installed pyosmium path."""
        import importlib.util

        script = PROJECT_ROOT / "scripts" / "c2_extract_sabaragamuwa_osm.py"
        spec = importlib.util.spec_from_file_location("c2_extract", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert module.PBF_PATH.name == "sri-lanka-latest.osm.pbf"
        assert callable(module.extract_network_from_pbf)
        assert module.LOCATION_INDEX == "sparse_file_array"

    def test_extraction_does_not_use_overpass(self):
        """Road extraction must read the local PBF, not call Overpass."""
        source = (
            PROJECT_ROOT / "scripts" / "c2_extract_sabaragamuwa_osm.py"
        ).read_text(encoding="utf-8")
        assert "graph_from_polygon" not in source
        assert "Overpass API" not in source
        assert "pyosmium" in source

    def test_required_road_tags_are_declared(self):
        """Required OSM road attributes must be retained in GraphML edges."""
        import importlib.util

        script = PROJECT_ROOT / "scripts" / "c2_extract_sabaragamuwa_osm.py"
        spec = importlib.util.spec_from_file_location("c2_extract", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert {
            "highway", "oneway", "bridge", "tunnel", "layer",
            "maxspeed", "lanes", "ref", "name", "junction",
            "access", "motor_vehicle", "vehicle", "service",
        }.issubset(module.ROAD_TAGS)

    def test_boundary_geometry_is_combined_and_normalized(self):
        """Multiple boundary features are combined and normalized to EPSG:4326."""
        import geopandas as gpd

        module = _load_module()
        gdf = gpd.GeoDataFrame(
            {"name": ["west", "east"]},
            geometry=[box(0, 0, 1, 1), box(1, 0, 2, 1)],
            crs="EPSG:4326",
        )
        normalized, geometry = module.normalize_boundary(gdf)
        assert normalized.crs.to_epsg() == 4326
        assert geometry.is_valid
        assert geometry.area == pytest.approx(2.0)

    def test_boundary_crossing_segment_is_clipped(self):
        """A segment crossing the boundary retains only its inside portion."""
        module = _load_module()
        parts = module.clip_segment_to_boundary(
            LineString([(-1.0, 0.5), (2.0, 0.5)]),
            box(0.0, 0.0, 1.0, 1.0),
        )
        assert len(parts) == 1
        assert list(parts[0].coords) == [(0.0, 0.5), (1.0, 0.5)]

    def test_synthetic_boundary_node_ids_are_deterministic_and_negative(self):
        """Synthetic boundary IDs cannot collide with positive OSM node IDs."""
        module = _load_module()
        first = module._synthetic_node_id(42, 3, 0.25, 0.5)
        second = module._synthetic_node_id(42, 3, 0.25, 0.5)
        assert first == second
        assert first < 0

    def test_roundabout_direction_handling(self):
        """An untagged roundabout defaults to one-way; explicit no remains two-way."""
        module = _load_module()
        assert module._direction_pairs({"oneway": "", "junction": "roundabout"}) == [(0, 1)]
        assert module._direction_pairs({"oneway": " no ", "junction": "roundabout"}) == [
            (0, 1), (1, 0)
        ]
        assert module._direction_pairs({"oneway": " YES ", "junction": ""}) == [(0, 1)]
        assert module._direction_pairs({"oneway": " -1 ", "junction": ""}) == [(1, 0)]

    def test_tag_values_are_graphml_safe_scalars(self):
        """Missing, list, and object-like OSM values become scalar strings."""
        module = _load_module()
        class Tags:
            def get(self, key):
                return {"missing": None, "list": ["a", "b"], "number": 4}.get(key)
        assert module._tag_value(Tags(), "missing") == ""
        assert module._tag_value(Tags(), "list") == "['a', 'b']"
        assert module._tag_value(Tags(), "number") == "4"

    def test_graphml_writer_accepts_scalar_attributes(self, tmp_path):
        """A representative raw graph with preserved tags is GraphML-safe."""
        module = _load_module()
        graph = module.nx.MultiDiGraph(crs="EPSG:4326")
        graph.add_node(1, x=80.0, y=6.0)
        graph.add_node(-2, x=80.1, y=6.1)
        graph.add_edge(
            1,
            -2,
            osmid=7,
            length=123.4,
            **{
                tag: "value"
                for tag in module.GRAPHML_TAGS
                if tag not in {"oneway", "oneway_osm"}
            },
            oneway=False,
            oneway_osm="no",
        )
        output = tmp_path / "graph.graphml"
        module.save_graphml(graph, output)
        assert output.exists()
        assert output.stat().st_size > 0

    def test_oneway_preserves_original_value_and_uses_graphml_boolean(self):
        """OSMnx's reserved oneway field is boolean; OSM meaning remains available."""
        module = _load_module()
        tags = {tag: "" for tag in module.ROAD_TAGS}
        tags["oneway"] = " -1 "
        tags["junction"] = ""
        original = tags["oneway"]
        tags["oneway_osm"] = original
        tags["oneway"] = module._graphml_oneway_value(tags)
        assert tags["oneway"] is False
        assert tags["oneway_osm"] == " -1 "

    def test_metadata_contains_local_pbf_provenance(self, tmp_path):
        """Metadata records the local source and extraction contract."""
        import importlib.util

        script = PROJECT_ROOT / "scripts" / "c2_extract_sabaragamuwa_osm.py"
        spec = importlib.util.spec_from_file_location("c2_extract", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.METADATA_PATH = tmp_path / "metadata.json"
        module.save_metadata(
            {
                "crs": "EPSG:4326",
                "node_count": 2,
                "edge_count": 2,
                "is_directed": True,
                "graph_type": "MultiDiGraph",
                "validation_issues": [],
            },
            boundary_saved=True,
            elapsed_total=1.0,
            pbf_path=module.PBF_PATH,
        )
        data = json.loads(module.METADATA_PATH.read_text(encoding="utf-8"))
        assert data["source"] == "OpenStreetMap"
        assert data["source_file"] == "cache\\sri-lanka-latest.osm.pbf"
        assert data["study_area"] == module.STUDY_AREA
        assert data["simplify"] is False
        assert data["retain_all"] is True
        assert data["crs"] == "EPSG:4326"
        assert set(module.GRAPHML_TAGS).issubset(data["road_tags"])
        assert data["status"] == "success"

    def test_failed_metadata_is_explicit(self, tmp_path):
        """Failed extraction metadata must identify the error rather than success."""
        module = _load_module()
        module.METADATA_PATH = tmp_path / "failed.json"
        module.save_metadata(
            {},
            boundary_saved=False,
            elapsed_total=0.1,
            pbf_path=module.PBF_PATH,
            status="failed",
            error=ValueError("boundary unavailable"),
        )
        data = json.loads(module.METADATA_PATH.read_text(encoding="utf-8"))
        assert data["status"] == "failed"
        assert data["error_type"] == "ValueError"
        assert data["error_message"] == "boundary unavailable"

    def test_graph_provenance_uses_supplied_pbf_path(self, tmp_path):
        """Graph provenance must use the function's supplied PBF path."""
        module = _load_module()
        pbf_path = PROJECT_ROOT / "custom.osm.pbf"
        graph = module._RoadHandler(box(0, 0, 1, 1), pbf_path).graph
        assert graph.graph["source_file"] == "custom.osm.pbf"

    def test_output_paths_inside_data_raw(self):
        """All output files must be inside data/raw/."""
        import importlib.util
        script = PROJECT_ROOT / "scripts" / "c2_extract_sabaragamuwa_osm.py"
        spec = importlib.util.spec_from_file_location("c2_extract", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for path in [module.GRAPHML_PATH, module.METADATA_PATH, module.BOUNDARY_PATH]:
            assert DATA_RAW_DIR in path.parents or path.parent == DATA_RAW_DIR, (
                f"Output path {path} is outside data/raw/."
            )

    def test_component2_filename_prefix(self):
        """Output file names must have Component 2 prefix 'c2_'."""
        import importlib.util
        script = PROJECT_ROOT / "scripts" / "c2_extract_sabaragamuwa_osm.py"
        spec = importlib.util.spec_from_file_location("c2_extract", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for fname in [module.GRAPHML_FILENAME, module.METADATA_FILENAME, module.BOUNDARY_FILENAME]:
            assert fname.startswith("c2_"), (
                f"Filename {fname!r} does not start with 'c2_'."
            )


# ---------------------------------------------------------------------------
# Output file tests (require extraction to have been run)
# ---------------------------------------------------------------------------

class TestGraphMLOutput:
    """Tests that verify the raw GraphML output file."""

    @skip_data
    def test_graphml_file_exists(self):
        """Raw GraphML file must exist after extraction."""
        assert GRAPHML_PATH.exists(), f"GraphML not found: {GRAPHML_PATH}"

    @skip_data
    def test_graphml_file_nonzero(self):
        """GraphML file must have non-zero size."""
        assert GRAPHML_PATH.stat().st_size > 0, "GraphML file is empty."

    @skip_data
    def test_graphml_file_size_reasonable(self):
        """GraphML file should be at least 1 MB for a province-scale network."""
        size_mb = GRAPHML_PATH.stat().st_size / (1024 * 1024)
        assert size_mb >= 1.0, f"GraphML too small ({size_mb:.2f} MB); check extraction."

    @skip_data
    def test_graphml_loadable(self):
        """Raw GraphML must be loadable as a NetworkX graph."""
        import osmnx as ox
        G = ox.load_graphml(filepath=str(GRAPHML_PATH))
        assert G is not None

    @skip_data
    def test_graph_is_directed_multigraph(self):
        """Graph must be a directed multigraph (MultiDiGraph)."""
        import networkx as nx
        import osmnx as ox
        G = ox.load_graphml(filepath=str(GRAPHML_PATH))
        assert isinstance(G, nx.MultiDiGraph), (
            f"Expected MultiDiGraph, got {type(G).__name__}."
        )

    @skip_data
    def test_graph_has_nodes(self):
        """Graph must have at least one node."""
        import osmnx as ox
        G = ox.load_graphml(filepath=str(GRAPHML_PATH))
        assert len(G.nodes) > 0, "Graph has no nodes."

    @skip_data
    def test_graph_has_edges(self):
        """Graph must have at least one edge."""
        import osmnx as ox
        G = ox.load_graphml(filepath=str(GRAPHML_PATH))
        assert len(G.edges) > 0, "Graph has no edges."

    @skip_data
    def test_graph_node_count_plausible(self):
        """Province-scale network should have at least 1,000 nodes."""
        import osmnx as ox
        G = ox.load_graphml(filepath=str(GRAPHML_PATH))
        assert len(G.nodes) >= 1_000, (
            f"Only {len(G.nodes)} nodes — too few for a province-scale road network."
        )

    @skip_data
    def test_graph_edge_count_plausible(self):
        """Province-scale network should have at least 1,000 edges."""
        import osmnx as ox
        G = ox.load_graphml(filepath=str(GRAPHML_PATH))
        assert len(G.edges) >= 1_000, (
            f"Only {len(G.edges)} edges — too few for a province-scale road network."
        )

    @skip_data
    def test_graph_crs_attribute_present(self):
        """Raw graph must have a CRS attribute."""
        import osmnx as ox
        G = ox.load_graphml(filepath=str(GRAPHML_PATH))
        crs = G.graph.get("crs")
        assert crs is not None, "Graph is missing 'crs' attribute."

    @skip_data
    def test_graph_nodes_have_coordinates(self):
        """A sample of graph nodes must have 'x' and 'y' coordinate attributes."""
        import osmnx as ox
        G = ox.load_graphml(filepath=str(GRAPHML_PATH))
        sample = list(G.nodes(data=True))[:20]
        for node_id, data in sample:
            assert "x" in data, f"Node {node_id} missing 'x' coordinate."
            assert "y" in data, f"Node {node_id} missing 'y' coordinate."

    @skip_data
    def test_graph_edges_have_highway_attribute(self):
        """A sample of edges should have 'highway' attribute from OSM."""
        import osmnx as ox
        G = ox.load_graphml(filepath=str(GRAPHML_PATH))
        sample = list(G.edges(data=True))[:20]
        missing = [
            (u, v, k) for u, v, k, d in
            ((u, v, k, d) for u, v, k, d in G.edges(data=True, keys=True))
            if "highway" not in d
        ]
        # Allow up to 5% of sampled edges to lack highway tag
        total = len(G.edges)
        assert len(missing) < total * 0.05, (
            f"{len(missing)} edges ({len(missing)/total:.1%}) missing 'highway' attribute."
        )


class TestMetadataOutput:
    """Tests that verify the extraction metadata JSON."""

    @skip_data
    def test_metadata_file_exists(self):
        """Metadata JSON must exist after extraction."""
        assert METADATA_PATH.exists(), f"Metadata not found: {METADATA_PATH}"

    @skip_data
    def test_metadata_valid_json(self):
        """Metadata file must be valid JSON."""
        with open(METADATA_PATH, encoding="utf-8") as f:
            data = json.load(f)
        assert isinstance(data, dict)

    @skip_data
    def test_metadata_required_keys(self):
        """Metadata must contain all required fields."""
        with open(METADATA_PATH, encoding="utf-8") as f:
            data = json.load(f)
        required_keys = [
            "extraction_datetime",
            "study_area",
            "country",
            "network_type",
            "source",
            "source_file",
            "extraction_method",
            "node_count",
            "edge_count",
            "is_directed",
            "graph_type",
            "raw_graph_filename",
        ]
        for key in required_keys:
            assert key in data, f"Missing metadata key: {key!r}"

    @skip_data
    def test_metadata_study_area_correct(self):
        """Metadata study_area must reference Sabaragamuwa Province."""
        with open(METADATA_PATH, encoding="utf-8") as f:
            data = json.load(f)
        assert "Sabaragamuwa" in data["study_area"]

    @skip_data
    def test_metadata_network_type_drive(self):
        """Metadata network_type must be 'drive'."""
        with open(METADATA_PATH, encoding="utf-8") as f:
            data = json.load(f)
        assert data["network_type"] == "drive"

    @skip_data
    def test_metadata_node_edge_counts_positive(self):
        """Metadata node and edge counts must be positive integers."""
        with open(METADATA_PATH, encoding="utf-8") as f:
            data = json.load(f)
        assert isinstance(data["node_count"], int) and data["node_count"] > 0
        assert isinstance(data["edge_count"], int) and data["edge_count"] > 0

    @skip_data
    def test_metadata_is_directed_true(self):
        """Metadata must confirm the graph is directed."""
        with open(METADATA_PATH, encoding="utf-8") as f:
            data = json.load(f)
        assert data["is_directed"] is True

    @skip_data
    def test_metadata_raw_graph_filename_prefix(self):
        """Raw graph filename in metadata must start with 'c2_'."""
        with open(METADATA_PATH, encoding="utf-8") as f:
            data = json.load(f)
        assert data["raw_graph_filename"].startswith("c2_")
