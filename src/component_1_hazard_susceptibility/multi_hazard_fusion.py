import argparse
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import Window


NODATA = -9999.0


def create_multi_hazard_max(
    flood_path: Path,
    landslide_path: Path,
    output_path: Path,
    block_height: int = 256,
):
    """
    Combine normalized flood and landslide susceptibility
    rasters using pixel-wise MAX fusion.

    Multi_Hazard_Score = max(Flood_Score, Landslide_Score)
    """

    with rasterio.open(flood_path) as flood_src, \
         rasterio.open(landslide_path) as landslide_src:

        # Validate raster alignment
        if flood_src.width != landslide_src.width:
            raise ValueError("Raster widths do not match.")

        if flood_src.height != landslide_src.height:
            raise ValueError("Raster heights do not match.")

        if flood_src.crs != landslide_src.crs:
            raise ValueError("Raster CRS values do not match.")

        if flood_src.transform != landslide_src.transform:
            raise ValueError("Raster grids are not aligned.")

        profile = flood_src.profile.copy()

        # Remove inherited tiling/block settings
        profile.pop("blockxsize", None)
        profile.pop("blockysize", None)
        profile.pop("tiled", None)

        profile.update(
            driver="GTiff",
            count=1,
            dtype="float32",
            nodata=NODATA,
            compress="lzw",
        )

        output_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        with rasterio.open(
            output_path,
            "w",
            **profile,
        ) as dst:

            for row_start in range(
                0,
                flood_src.height,
                block_height,
            ):
                current_height = min(
                    block_height,
                    flood_src.height - row_start,
                )

                window = Window(
                    col_off=0,
                    row_off=row_start,
                    width=flood_src.width,
                    height=current_height,
                )

                flood = flood_src.read(
                    1,
                    window=window,
                ).astype(np.float32)

                landslide = landslide_src.read(
                    1,
                    window=window,
                ).astype(np.float32)

                flood_valid = (
                    np.isfinite(flood)
                    & (flood != flood_src.nodata)
                )

                landslide_valid = (
                    np.isfinite(landslide)
                    & (landslide != landslide_src.nodata)
                )

                # Both hazard scores must exist
                valid_mask = flood_valid & landslide_valid

                output = np.full(
                    flood.shape,
                    NODATA,
                    dtype=np.float32,
                )

                if np.any(valid_mask):
                    output[valid_mask] = np.maximum(
                        flood[valid_mask],
                        landslide[valid_mask],
                    )

                dst.write(
                    output,
                    1,
                    window=window,
                )

                print(
                    f"Processed rows "
                    f"{row_start} to "
                    f"{row_start + current_height - 1}"
                )

    print(
        f"\nMulti-hazard MAX raster saved to: "
        f"{output_path}"
    )


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Create a multi-hazard susceptibility raster "
            "using pixel-wise MAX fusion."
        )
    )

    parser.add_argument(
        "--flood",
        required=True,
        help="Normalized flood susceptibility GeoTIFF.",
    )

    parser.add_argument(
        "--landslide",
        required=True,
        help="Normalized landslide susceptibility GeoTIFF.",
    )

    parser.add_argument(
        "--output",
        required=True,
        help="Output multi-hazard GeoTIFF.",
    )

    args = parser.parse_args()

    create_multi_hazard_max(
        flood_path=Path(args.flood),
        landslide_path=Path(args.landslide),
        output_path=Path(args.output),
    )


if __name__ == "__main__":
    main()