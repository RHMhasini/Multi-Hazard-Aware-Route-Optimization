import argparse
import xml.etree.ElementTree as ET
from pathlib import Path

import geopandas as gpd
from shapely.geometry import LineString


GRAPHML_NS = "{http://graphml.graphdrawing.org/xmlns}"


def read_nodes(graphml_path: Path):
    """
    First streaming pass:
    read node longitude/latitude coordinates only.
    """

    print("Reading node coordinates...")

    key_names = {}
    nodes = {}

    for _, elem in ET.iterparse(
        graphml_path,
        events=("end",),
    ):
        if elem.tag == GRAPHML_NS + "key":
            key_id = elem.attrib.get("id")
            key_name = elem.attrib.get("attr.name")

            if key_id and key_name:
                key_names[key_id] = key_name

        elif elem.tag == GRAPHML_NS + "node":
            node_id = elem.attrib["id"]

            x = None
            y = None

            for data in elem.findall(
                GRAPHML_NS + "data"
            ):
                field_name = key_names.get(
                    data.attrib.get("key")
                )

                if field_name == "x":
                    x = float(data.text)

                elif field_name == "y":
                    y = float(data.text)

            if x is not None and y is not None:
                nodes[node_id] = (x, y)

            elem.clear()

    print(
        f"Loaded {len(nodes):,} node coordinates."
    )

    return nodes


def write_batch(
    records,
    output_path: Path,
    layer_name: str,
    first_batch: bool,
):
    """
    Convert one batch from EPSG:4326 to EPSG:32644
    and append it to the GeoPackage.
    """

    if not records:
        return

    gdf = gpd.GeoDataFrame(
        records,
        geometry="geometry",
        crs="EPSG:4326",
    )

    # Match Component 1 susceptibility rasters
    gdf = gdf.to_crs("EPSG:32644")

    mode = "w" if first_batch else "a"

    gdf.to_file(
        output_path,
        layer=layer_name,
        driver="GPKG",
        mode=mode,
    )


def convert_graphml(
    graphml_path: Path,
    output_path: Path,
    batch_size: int = 20000,
):
    """
    Convert Component 2 directed GraphML edges into
    a projected GeoPackage road-edge layer.

    Stable road ID:
        road_id = u_v_key
    """

    nodes = read_nodes(graphml_path)

    if output_path.exists():
        print(
            f"Removing existing output: "
            f"{output_path}"
        )
        output_path.unlink()

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    print("\nReading directed edges...")

    key_names = {}
    batch = []

    edge_count = 0
    missing_node_count = 0
    first_batch = True

    fields_to_keep = {
        "osmid",
        "length",
        "highway",
        "oneway",
        "maxspeed",
        "name",
        "ref",
        "oneway_normalized",
        "direction_source",
    }

    for _, elem in ET.iterparse(
        graphml_path,
        events=("end",),
    ):
        if elem.tag == GRAPHML_NS + "key":
            key_id = elem.attrib.get("id")
            key_name = elem.attrib.get("attr.name")

            if key_id and key_name:
                key_names[key_id] = key_name

        elif elem.tag == GRAPHML_NS + "edge":
            u = elem.attrib["source"]
            v = elem.attrib["target"]

            # GraphML edge id acts as the edge key
            edge_key = elem.attrib.get(
                "id",
                "0",
            )

            if u not in nodes or v not in nodes:
                missing_node_count += 1
                elem.clear()
                continue

            attributes = {}

            for data in elem.findall(
                GRAPHML_NS + "data"
            ):
                field_name = key_names.get(
                    data.attrib.get("key")
                )

                if field_name in fields_to_keep:
                    attributes[field_name] = (
                        data.text
                        if data.text is not None
                        else ""
                    )

            # Stable directed road-edge identifier
            road_id = f"{u}_{v}_{edge_key}"

            geometry = LineString(
                [
                    nodes[u],
                    nodes[v],
                ]
            )

            record = {
                "road_id": road_id,
                "u": u,
                "v": v,
                "key": edge_key,
                "geometry": geometry,
            }

            for field in fields_to_keep:
                record[field] = attributes.get(
                    field,
                    ""
                )

            batch.append(record)
            edge_count += 1

            if len(batch) >= batch_size:
                write_batch(
                    batch,
                    output_path,
                    layer_name="road_edges",
                    first_batch=first_batch,
                )

                first_batch = False
                batch.clear()

                print(
                    f"Written {edge_count:,} edges..."
                )

            elem.clear()

    # Final incomplete batch
    if batch:
        write_batch(
            batch,
            output_path,
            layer_name="road_edges",
            first_batch=first_batch,
        )

    print("\nConversion complete.")
    print(
        f"Directed edges written: "
        f"{edge_count:,}"
    )
    print(
        f"Edges skipped due to missing nodes: "
        f"{missing_node_count:,}"
    )
    print(
        f"Output: {output_path}"
    )


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Convert Component 2 GraphML road network "
            "to a projected GeoPackage road-edge layer."
        )
    )

    parser.add_argument(
        "--input",
        required=True,
        help="Path to Component 2 GraphML file.",
    )

    parser.add_argument(
        "--output",
        default=(
            "data/component2/"
            "c2_road_edges_utm.gpkg"
        ),
        help="Output GeoPackage path.",
    )

    parser.add_argument(
        "--batch-size",
        type=int,
        default=20000,
    )

    args = parser.parse_args()

    convert_graphml(
        graphml_path=Path(args.input),
        output_path=Path(args.output),
        batch_size=args.batch_size,
    )


if __name__ == "__main__":
    main()