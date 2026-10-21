"""Regression test: verify no fake claims or invented place names exist in Component 3."""

import os
import re
import json
from pathlib import Path
import pytest

from src.route_optimization.synthetic import (
    generate_synthetic_grid_graph,
    generate_synthetic_hazard_raster,
)
from src.route_optimization.attachment import attach_hazard_to_graph
from src.route_optimization.graph_builder import build_routing_graph
from src.route_optimization.baselines import run_all_baselines
from src.route_optimization.recommend import recommend_optimal_route
from src.route_optimization.exporter import export_routes_to_geojson, SYNTHETIC_BANNER
import export_demo_snapshot

REPO_ROOT = Path(__file__).resolve().parents[2]

FORBIDDEN_TERMS = [
    "Ratnapura General",
    "Teaching Hospital",
    "Clock Tower",
    "Getangama",
    "Muwaagama",
    "Tiruwanaketiya",
    "Pelmadulla",
    "Kahawatta",
    "Elapatha",
    "Kuruwita",
    "Wewelwatta",
    "Weralupa",
    "Kalu Ganga",
    "A4",
    "A8",
    "A18",
    "B391",
    "Hotspot",
    "High-Risk Corridor",
    "Landslide Zone",
    "100% COMPLIANT",
    "Ratnapura District",
]


def test_exporter_and_snapshot_in_demo_mode(tmp_path):
    """Test that GeoJSON export in demo mode contains no fake claims and omits forbidden keys."""
    grid_graph = generate_synthetic_grid_graph(rows=6, cols=6, seed=42, label_mode="demo")
    hazard_data = generate_synthetic_hazard_raster(seed=42)
    attach_hazard_to_graph(grid_graph, hazard_data)
    routing_graph = build_routing_graph(grid_graph)

    origin = "node_4_0"
    destination = "node_0_5"
    baselines = run_all_baselines(routing_graph, origin, destination)
    rec = recommend_optimal_route(routing_graph, [{"path": p, "algorithm": k} for k, p in baselines.items() if p])

    routes_payload = dict(baselines)
    routes_payload["recommended"] = rec

    out_file = tmp_path / "test_demo_mode.geojson"
    geojson = export_routes_to_geojson(
        graph=routing_graph,
        routes=routes_payload,
        recommended_route_id="recommended",
        output_filepath=out_file,
        label_mode="demo",
    )

    # 1. Assert metadata banner
    assert "banner" in geojson["metadata"]
    assert "Synthetic network and synthetic hazard. Place names are illustrative placeholders, not real locations." in geojson["metadata"]["banner"]
    assert geojson["metadata"]["data_mode"] == "synthetic"
    assert geojson["metadata"]["label_mode"] == "demo"
    assert geojson["metadata"]["target_region"] == "synthetic network over an illustrative extent"

    # 2. Assert omitted keys in LineString features in demo mode
    lines = [f for f in geojson["features"] if f["geometry"]["type"] == "LineString"]
    assert len(lines) >= 1
    for f in lines:
        props = f["properties"]
        assert "waypoint_names" not in props, "waypoint_names must be absent in demo mode GeoJSON"
        assert "road_name" not in props, "road_name must be absent in demo mode GeoJSON"
        assert "corridor_description" not in props, "corridor_description must be absent in demo mode GeoJSON"


def test_no_forbidden_terms_in_codebase_and_outputs():
    """Scan all generated GeoJSON, CSV, text outputs and source files (excluding this test) for forbidden terms."""
    targets = [
        REPO_ROOT / "src",
        REPO_ROOT / "demo.py",
        REPO_ROOT / "demo_app.py",
        REPO_ROOT / "app.py",
        REPO_ROOT / "ui",
        REPO_ROOT / "export_demo_snapshot.py",
        REPO_ROOT / "README.md",
        REPO_ROOT / "contracts",
        REPO_ROOT / "outputs" / "component3",
    ]

    pattern = re.compile(r"\b(" + "|".join(re.escape(t) for t in FORBIDDEN_TERMS) + r")\b", re.IGNORECASE)

    violations = []
    this_file = Path(__file__).resolve()

    for target in targets:
        if not target.exists():
            continue
        if target.is_file():
            files = [target]
        else:
            files = [p for p in target.rglob("*") if p.is_file()]

        for file_path in files:
            if file_path.resolve() == this_file:
                continue
            if file_path.suffix.lower() in [".png", ".jpg", ".jpeg", ".pyc", ".git", ".log"]:
                continue
            if "audit_hits" in file_path.name or ".pytest_cache" in str(file_path):
                continue

            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
                for line_idx, line in enumerate(content.splitlines(), start=1):
                    for match in pattern.finditer(line):
                        violations.append(
                            f"{file_path}:{line_idx}: matched '{match.group(0)}' in line: {line.strip()}"
                        )
            except Exception as e:
                violations.append(f"Could not read {file_path}: {e}")

    assert not violations, f"Found forbidden invented terms in files:\n" + "\n".join(violations)
