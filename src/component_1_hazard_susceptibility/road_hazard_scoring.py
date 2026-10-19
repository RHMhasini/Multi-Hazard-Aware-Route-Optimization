import argparse
import math
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio
import rasterio
from rasterio.transform import rowcol


NODATA_SCORE = np.nan


def validate_rasters(flood_src, landslide_src):
    if flood_src.crs != landslide_src.crs:
        raise ValueError("Flood and landslide CRS values do not match.")

    if flood_src.transform != landslide_src.transform:
        raise ValueError("Flood and landslide raster grids are not aligned.")

    if flood_src.width != landslide_src.width:
        raise ValueError("Raster widths do not match.")

    if flood_src.height != landslide_src.height:
        raise ValueError("Raster heights do not match.")


def get_sample_cells(
    geometry,
    transform,
    width,
    height,
    spacing,
):
    """
    Sample raster cells along a road geometry using regular
    spacing smaller than the raster pixel size.
    """

    if geometry is None or geometry.is_empty:
        return np.array([], dtype=int), np.array([], dtype=int)

    coords = np.asarray(geometry.coords)

    rows_all = []
    cols_all = []

    for i in range(len(coords) - 1):
        x1, y1 = coords[i]
        x2, y2 = coords[i + 1]

        distance = math.hypot(
            x2 - x1,
            y2 - y1,
        )

        steps = max(
            2,
            int(math.ceil(distance / spacing)) + 1,
        )

        xs = np.linspace(
            x1,
            x2,
            steps,
        )

        ys = np.linspace(
            y1,
            y2,
            steps,
        )

        rows, cols = rowcol(
            transform,
            xs,
            ys,
        )

        rows_all.extend(rows)
        cols_all.extend(cols)

    if not rows_all:
        return np.array([], dtype=int), np.array([], dtype=int)

    cells = np.column_stack(
        (
            np.asarray(rows_all, dtype=int),
            np.asarray(cols_all, dtype=int),
        )
    )

    valid_bounds = (
        (cells[:, 0] >= 0)
        & (cells[:, 0] < height)
        & (cells[:, 1] >= 0)
        & (cells[:, 1] < width)
    )

    cells = cells[valid_bounds]

    if len(cells) == 0:
        return np.array([], dtype=int), np.array([], dtype=int)

    cells = np.unique(
        cells,
        axis=0,
    )

    return cells[:, 0], cells[:, 1]


def score_geometry(
    geometry,
    flood_array,
    landslide_array,
    transform,
    flood_nodata,
    landslide_nodata,
    width,
    height,
    spacing,
):
    rows, cols = get_sample_cells(
        geometry=geometry,
        transform=transform,
        width=width,
        height=height,
        spacing=spacing,
    )

    if len(rows) == 0:
        return {
            "flood_road_score": NODATA_SCORE,
            "landslide_road_score": NODATA_SCORE,
            "final_road_score": NODATA_SCORE,
            "dominant_hazard": "NoData",
            "critical_x": NODATA_SCORE,
            "critical_y": NODATA_SCORE,
        }

    flood_values = flood_array[
        rows,
        cols,
    ]

    landslide_values = landslide_array[
        rows,
        cols,
    ]

    valid = (
        np.isfinite(flood_values)
        & np.isfinite(landslide_values)
    )

    if flood_nodata is not None:
        valid &= flood_values != flood_nodata

    if landslide_nodata is not None:
        valid &= landslide_values != landslide_nodata

    if not np.any(valid):
        return {
            "flood_road_score": NODATA_SCORE,
            "landslide_road_score": NODATA_SCORE,
            "final_road_score": NODATA_SCORE,
            "dominant_hazard": "NoData",
            "critical_x": NODATA_SCORE,
            "critical_y": NODATA_SCORE,
        }

    valid_rows = rows[valid]
    valid_cols = cols[valid]

    flood_values = flood_values[valid]
    landslide_values = landslide_values[valid]

    flood_score = float(
        np.max(flood_values)
    )

    landslide_score = float(
        np.max(landslide_values)
    )

    final_score = max(
        flood_score,
        landslide_score,
    )

    if flood_score > landslide_score:
        dominant_hazard = "Flood"
    elif landslide_score > flood_score:
        dominant_hazard = "Landslide"
    else:
        dominant_hazard = "Equal"

    combined = np.maximum(
        flood_values,
        landslide_values,
    )

    critical_index = int(
        np.argmax(combined)
    )

    critical_row = valid_rows[
        critical_index
    ]

    critical_col = valid_cols[
        critical_index
    ]

    critical_x, critical_y = rasterio.transform.xy(
        transform,
        critical_row,
        critical_col,
        offset="center",
    )

    return {
        "flood_road_score": flood_score,
        "landslide_road_score": landslide_score,
        "final_road_score": final_score,
        "dominant_hazard": dominant_hazard,
        "critical_x": float(critical_x),
        "critical_y": float(critical_y),
    }


def score_roads(
    roads_path,
    flood_raster,
    landslide_raster,
    output_vector,
    output_csv,
    layer_name="road_edges",
    batch_size=20000,
):
    roads_path = Path(roads_path)
    output_vector = Path(output_vector)
    output_csv = Path(output_csv)

    info = pyogrio.read_info(
        roads_path,
        layer=layer_name,
    )

    total_roads = info["features"]

    print(
        f"Road segments found: "
        f"{total_roads:,}"
    )

    with rasterio.open(flood_raster) as flood_src, \
         rasterio.open(landslide_raster) as landslide_src:

        validate_rasters(
            flood_src,
            landslide_src,
        )

        flood_array = flood_src.read(
            1
        ).astype(np.float32)

        landslide_array = landslide_src.read(
            1
        ).astype(np.float32)

        transform = flood_src.transform
        width = flood_src.width
        height = flood_src.height

        pixel_size = abs(
            flood_src.transform.a
        )

        # 30 m raster -> approximately 10 m sampling
        spacing = pixel_size / 3.0

        print(
            f"Raster sampling spacing: "
            f"{spacing:.2f} m"
        )

        output_vector.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        output_csv.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        if output_vector.exists():
            output_vector.unlink()

        if output_csv.exists():
            output_csv.unlink()

        first_batch = True

        processed = 0

        for start in range(
            0,
            total_roads,
            batch_size,
        ):
            current_size = min(
                batch_size,
                total_roads - start,
            )

            roads = pyogrio.read_dataframe(
                roads_path,
                layer=layer_name,
                skip_features=start,
                max_features=current_size,
            )

            if roads.crs != flood_src.crs:
                roads = roads.to_crs(
                    flood_src.crs
                )

            results = []

            for geometry in roads.geometry:
                result = score_geometry(
                    geometry=geometry,
                    flood_array=flood_array,
                    landslide_array=landslide_array,
                    transform=transform,
                    flood_nodata=flood_src.nodata,
                    landslide_nodata=landslide_src.nodata,
                    width=width,
                    height=height,
                    spacing=spacing,
                )

                results.append(result)

            result_df = pd.DataFrame(
                results,
                index=roads.index,
            )

            for column in result_df.columns:
                roads[column] = result_df[
                    column
                ].values

            roads["explanation"] = np.where(
                np.isfinite(
                    roads["final_road_score"]
                ),
                (
                    "Highest road susceptibility = "
                    + roads[
                        "final_road_score"
                    ].round(3).astype(str)
                    + "; dominant hazard = "
                    + roads[
                        "dominant_hazard"
                    ].astype(str)
                ),
                "No valid hazard raster data for this road segment",
            )

            pyogrio.write_dataframe(
                roads,
                output_vector,
                layer="road_hazard_scores",
                driver="GPKG",
                append=not first_batch,
            )

            csv_columns = [
                "road_id",
                "u",
                "v",
                "key",
                "flood_road_score",
                "landslide_road_score",
                "final_road_score",
                "dominant_hazard",
                "critical_x",
                "critical_y",
                "explanation",
            ]

            roads[
                csv_columns
            ].to_csv(
                output_csv,
                mode="w" if first_batch else "a",
                header=first_batch,
                index=False,
            )

            first_batch = False

            processed += len(roads)

            print(
                f"Processed "
                f"{processed:,}/{total_roads:,} "
                f"road segments"
            )

    print("\nRoad hazard scoring complete.")

    print(
        f"GeoPackage: {output_vector}"
    )

    print(
        f"CSV: {output_csv}"
    )


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Aggregate flood and landslide susceptibility "
            "scores to Component 2 road segments."
        )
    )

    parser.add_argument(
        "--roads",
        required=True,
    )

    parser.add_argument(
        "--flood",
        required=True,
    )

    parser.add_argument(
        "--landslide",
        required=True,
    )

    parser.add_argument(
        "--output-vector",
        default="outputs/road_hazard_scores.gpkg",
    )

    parser.add_argument(
        "--output-csv",
        default="outputs/road_hazard_scores.csv",
    )

    parser.add_argument(
        "--batch-size",
        type=int,
        default=20000,
    )

    args = parser.parse_args()

    score_roads(
        roads_path=args.roads,
        flood_raster=args.flood,
        landslide_raster=args.landslide,
        output_vector=args.output_vector,
        output_csv=args.output_csv,
        batch_size=args.batch_size,
    )


if __name__ == "__main__":
    main()