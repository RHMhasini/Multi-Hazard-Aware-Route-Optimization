"""
One-time export: write frontend/public/roads.geojson
Only major road types (motorway → tertiary) are included — residential and
service roads are invisible at province scale and would bloat the file.
The frontend loads this static file directly — no API tile-fetching needed.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import networkx as nx

GRAPHML = ROOT / "data" / "intermediate" / "c2_sabaragamuwa_direction_normalized_graph.graphml"
OUT     = ROOT / "frontend" / "public" / "roads.geojson"

# Only include road types that are visible at province scale
MAJOR_PREFIXES = ("motorway", "trunk", "primary", "secondary", "tertiary")

def _v(val):
    if val is None:
        return None
    if isinstance(val, float) and (val != val):   # NaN
        return None
    return val

def is_major(highway: str | None) -> bool:
    if not highway:
        return False
    h = highway.lower().strip()
    return any(h.startswith(p) for p in MAJOR_PREFIXES)

def main() -> None:
    print(f"Loading graph from {GRAPHML} …")
    graph: nx.MultiDiGraph = nx.read_graphml(GRAPHML)
    print(f"  {graph.number_of_nodes():,} nodes, {graph.number_of_edges():,} edges")

    features = []
    skipped_coords = 0
    skipped_minor  = 0

    for source, target, attrs in graph.edges(data=True):
        hw = attrs.get("highway")
        if not is_major(hw):
            skipped_minor += 1
            continue

        sn = graph.nodes.get(source, {})
        tn = graph.nodes.get(target, {})
        try:
            src_lon, src_lat = float(sn["x"]), float(sn["y"])
            tgt_lon, tgt_lat = float(tn["x"]), float(tn["y"])
        except (KeyError, TypeError, ValueError):
            skipped_coords += 1
            continue

        features.append({
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [[src_lon, src_lat], [tgt_lon, tgt_lat]],
            },
            "properties": {
                "source":    str(source),
                "target":    str(target),
                "highway":   _v(hw),
                "name":      _v(attrs.get("name")),
                "length":    _v(attrs.get("length")),
                "maxspeed":  _v(attrs.get("maxspeed")),
                "oneway_normalized": _v(attrs.get("oneway_normalized")),
                "source_lon": src_lon,
                "source_lat": src_lat,
                "target_lon": tgt_lon,
                "target_lat": tgt_lat,
            },
        })

    OUT.parent.mkdir(parents=True, exist_ok=True)
    geojson = {"type": "FeatureCollection", "features": features}
    print(f"Writing {len(features):,} major-road features "
          f"(skipped {skipped_minor:,} minor, {skipped_coords} missing coords) to {OUT} …")
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(geojson, f, separators=(",", ":"), ensure_ascii=False)

    size_mb = OUT.stat().st_size / 1_048_576
    print(f"Done — {size_mb:.1f} MB")

if __name__ == "__main__":
    main()
