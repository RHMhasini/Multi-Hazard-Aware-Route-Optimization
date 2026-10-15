"""
One-time export: write frontend/public/roads.geojson

Exports ALL road types (including residential/service) so every edge is
visible in the road network graph visualisation.

Broken / isolated fragments are removed by keeping only edges whose both
endpoints belong to the MAIN weakly-connected component of the province
graph.  This is consistent with the topology-repair objective described in
the research proposal: roads that cannot be connected to the main network
are excluded from the final output.

File for Component 3:
    data/intermediate/c2_sabaragamuwa_direction_normalized_graph.graphml
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import networkx as nx

GRAPHML = ROOT / "data" / "intermediate" / "c2_sabaragamuwa_direction_normalized_graph.graphml"
OUT     = ROOT / "frontend" / "public" / "roads.geojson"


def _v(val):
    if val is None:
        return None
    if isinstance(val, float) and val != val:   # NaN
        return None
    return val


def main() -> None:
    print(f"Loading graph from {GRAPHML} …")
    graph: nx.MultiDiGraph = nx.read_graphml(GRAPHML)
    total_nodes = graph.number_of_nodes()
    total_edges = graph.number_of_edges()
    print(f"  {total_nodes:,} nodes, {total_edges:,} edges")

    # ── Keep only the main weakly-connected component ─────────────────────
    # Small isolated fragments are excluded — they are the "broken roads"
    # that the topology-repair step targets but could not connect.
    print("Finding main weakly-connected component …")
    wcc = sorted(nx.weakly_connected_components(graph), key=len, reverse=True)
    main_nodes = wcc[0]
    isolated_edges = total_edges - graph.subgraph(main_nodes).number_of_edges()
    print(f"  Main component: {len(main_nodes):,} / {total_nodes:,} nodes")
    print(f"  Removing {len(wcc) - 1:,} isolated fragments ({isolated_edges:,} edges)")

    # ── Export every edge in the main component ────────────────────────────
    features = []
    skipped_coords = 0

    for source, target, attrs in graph.edges(data=True):
        if source not in main_nodes or target not in main_nodes:
            continue
        sn = graph.nodes.get(source, {})
        tn = graph.nodes.get(target, {})
        try:
            src_lon = float(sn["x"]); src_lat = float(sn["y"])
            tgt_lon = float(tn["x"]); tgt_lat = float(tn["y"])
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
                "source":     str(source),
                "target":     str(target),
                "highway":    _v(attrs.get("highway")),
                "name":       _v(attrs.get("name")),
                "length":     _v(attrs.get("length")),
                "maxspeed":   _v(attrs.get("maxspeed")),
                "oneway_normalized": _v(attrs.get("oneway_normalized")),
                "source_lon": src_lon,
                "source_lat": src_lat,
                "target_lon": tgt_lon,
                "target_lat": tgt_lat,
            },
        })

    OUT.parent.mkdir(parents=True, exist_ok=True)
    geojson = {"type": "FeatureCollection", "features": features}
    print(f"Writing {len(features):,} features "
          f"(skipped {skipped_coords} with missing coords) to {OUT} …")
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(geojson, f, separators=(",", ":"), ensure_ascii=False)

    size_mb = OUT.stat().st_size / 1_048_576
    print(f"Done — {size_mb:.1f} MB  "
          f"({len(features):,} edges, {len(wcc)-1:,} isolated fragments removed)")
    print()
    print("Component 3 input file:")
    print(f"  {GRAPHML}")


if __name__ == "__main__":
    main()
