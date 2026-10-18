import argparse
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio

from rasterio.features import geometry_mask, geometry_window
from rasterio.transform import xy
from rasterio.errors import WindowError
from shapely.geometry import mapping


NODATA_SCORE = np.nan


def validate_rasters(flood_src, landslide_src):
    """Ensure flood and landslide rasters use the same grid."""

    if flood_src.crs != landslide_src.crs:
        raise ValueError("Flood and landslide CRS values do not match.")

    if flood_src.transform != landslide_src.transform:
        raise ValueError("Flood and landslide raster grids are not aligned.")

    if flood_src.width != landslide_src.width:
        raise ValueError("Flood and landslide raster widths do not match.")

    if flood_src.height != landslide_src.height:
        raise ValueError("Flood and landslide raster heights do not match.")


def valid_values(array, nodata):
    """Return mask for finite, non-NoData raster values."""

    mask = np.isfinite(array)

    if nodata is not None:
        mask &= array != nodata

    return mask


def score_road_geometry(
    geometry,
    flood_src,
    landslide_src,
):
    """
    Extract the maximum flood and landslide susceptibility
    intersected by one road geometry.
    """

    if geometry is None or geometry.is_empty:
        return {
            "flood_road_score": NODATA_SCORE,
            "landslide_road_score": NODATA_SCORE,
            "final_road_score": NODATA_SCORE,
            "dominant_hazard": "NoData",
            "critical_x": NODATA_SCORE,
            "critical_y": NODATA_SCORE,
        }

    geom = mapping(geometry)

    try:
        window = geometry_window(
            flood_src,
            [geom],
        )
    except WindowError:
        return {
            "flood_road_score": NODATA_SCORE,
            "landslide_road_score": NODATA_SCORE,
            "final_road_score": NODATA_SCORE,
            "dominant_hazard": "NoData",
            "critical_x": NODATA_SCORE,
            "critical_y": NODATA_SCORE,
        }

    flood = flood_src.read(
        1,
        window=window,
    ).astype(np.float32)

    landslide = landslide_src.read(
        1,
        window=window,
    ).astype(np.float32)

    window_transform = flood_src.window_transform(window)

    road_mask = geometry_mask(
        [geom],
        out_shape=flood.shape,
        transform=window_transform,
        invert=True,
        all_touched=True,
    )

    valid = (
        road_mask
        & valid_values(flood, flood_src.nodata)
        & valid_values(landslide, landslide_src.nodata)
    )

    if not np.any(valid):
        return {
            "flood_road_score": NODATA_SCORE,
            "landslide_road_score": NODATA_SCORE,
            "final_road_score": NODATA_SCORE,
            "dominant_hazard": "NoData",
            "critical_x": NODATA_SCORE,
            "critical_y": NODATA_SCORE,
        }

    flood_score = float(
        np.max(flood[valid])
    )

    landslide_score = float(
        np.max(landslide[valid])
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

    # Pixel-wise MAX along the road
    combined = np.full(
        flood.shape,
        np.nan,
        dtype=np.float32,
    )

    combined[valid] = np.maximum(
        flood[valid],
        landslide[valid],
    )

    critical_flat_index = np.nanargmax(combined)
    critical_row, critical_col = np.unravel_index(
        critical_flat_index,
        combined.shape,
    )

    critical_x, critical_y = xy(
        window_transform,
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
    roads_path: Path,
    flood_raster: Path,
    landslide_raster: Path,
    output_vector: Path,
    output_csv: Path,
    road_id_field: str,
):
    """Calculate hazard scores for every road segment."""

    roads = gpd.read_file(roads_path)

    if road_id_field not in roads.columns:
        raise ValueError(
            f"Road ID field '{road_id_field}' was not found."
        )

    with rasterio.open(flood_raster) as flood_src, \
         rasterio.open(landslide_raster) as landslide_src:

        validate_rasters(
            flood_src,
            landslide_src,
        )

        if roads.crs is None:
            raise ValueError(
                "Road layer has no CRS."
            )

        if roads.crs != flood_src.crs:
            print(
                f"Reprojecting roads from "
                f"{roads.crs} to {flood_src.crs}"
            )

            roads = roads.to_crs(
                flood_src.crs
            )

        scores = []

        total = len(roads)

        for index, row in roads.iterrows():

            result = score_road_geometry(
                row.geometry,
                flood_src,
                landslide_src,
            )

            scores.append(result)

            if (index + 1) % 100 == 0 or index + 1 == total:
                print(
                    f"Processed {index + 1}/{total} road segments"
                )

    score_gdf = gpd.GeoDataFrame(
        scores,
        index=roads.index,
    )

    for column in score_gdf.columns:
        roads[column] = score_gdf[column]

    roads["explanation"] = roads.apply(
        lambda row: (
            f"Highest road susceptibility = "
            f"{row['final_road_score']:.3f}; "
            f"dominant hazard = {row['dominant_hazard']}"
        )
        if np.isfinite(row["final_road_score"])
        else "No valid hazard raster data for this road segment",
        axis=1,
    )

    output_vector.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_csv.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    roads.to_file(
        output_vector,
        driver="GPKG",
    )

    csv_columns = [
        road_id_field,
        "flood_road_score",
        "landslide_road_score",
        "final_road_score",
        "dominant_hazard",
        "critical_x",
        "critical_y",
        "explanation",
    ]

    roads[csv_columns].to_csv(
        output_csv,
        index=False,
    )

    print(
        f"\nRoad hazard vector saved to: {output_vector}"
    )

    print(
        f"Road hazard CSV saved to: {output_csv}"
    )


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Aggregate flood and landslide susceptibility "
            "scores to road segments using maximum raster values."
        )
    )

    parser.add_argument(
        "--roads",
        required=True,
        help="Input road network vector layer.",
    )

    parser.add_argument(
        "--road-id-field",
        required=True,
        help="Stable road segment ID field.",
    )

    parser.add_argument(
        "--flood",
        required=True,
        help="Normalized flood susceptibility raster.",
    )

    parser.add_argument(
        "--landslide",
        required=True,
        help="Normalized landslide susceptibility raster.",
    )

    parser.add_argument(
        "--output-vector",
        default="outputs/road_hazard_scores.gpkg",
    )

    parser.add_argument(
        "--output-csv",
        default="outputs/road_hazard_scores.csv",
    )

    args = parser.parse_args()

    score_roads(
        roads_path=Path(args.roads),
        flood_raster=Path(args.flood),
        landslide_raster=Path(args.landslide),
        output_vector=Path(args.output_vector),
        output_csv=Path(args.output_csv),
        road_id_field=args.road_id_field,
    )


if __name__ == "__main__":
    main()