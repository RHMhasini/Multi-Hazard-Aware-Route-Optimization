import argparse
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import rasterio
from rasterio.windows import Window


FEATURES = [
    "Elevation",
    "Slope",
    "Aspect",
    "Profile_Curvature",
    "Tangential_Curvature",
    "TRI",
    "TWI",
    "Flow_Accumulation",
    "Distance_to_Stream",
    "Drainage_Density",
]


def generate_susceptibility(
    predictor_stack: Path,
    model_path: Path,
    output_path: Path,
    block_height: int = 256,
):
    """
    Generate a single-band susceptibility raster from a
    10-band predictor stack using a trained classifier.

    Output values are class-1 model scores in the range 0-1.
    """

    model = joblib.load(model_path)

    with rasterio.open(predictor_stack) as src:

        if src.count != len(FEATURES):
            raise ValueError(
                f"Expected {len(FEATURES)} raster bands, "
                f"but found {src.count}."
            )

        profile = src.profile.copy()

        # Remove inherited block settings that may cause warnings
        profile.pop("blockxsize", None)
        profile.pop("blockysize", None)
        profile.pop("tiled", None)

        profile.update(
            driver="GTiff",
            count=1,
            dtype="float32",
            nodata=-9999.0,
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
                src.height,
                block_height,
            ):
                current_height = min(
                    block_height,
                    src.height - row_start,
                )

                window = Window(
                    col_off=0,
                    row_off=row_start,
                    width=src.width,
                    height=current_height,
                )

                data = src.read(
                    window=window
                ).astype(np.float32)

                valid_mask = np.all(
                    np.isfinite(data)
                    & (data != -9999.0),
                    axis=0,
                )

                output_chunk = np.full(
                    (
                        current_height,
                        src.width,
                    ),
                    -9999.0,
                    dtype=np.float32,
                )

                if np.any(valid_mask):

                    X_valid = data[
                        :,
                        valid_mask,
                    ].T

                    X_valid = pd.DataFrame(
                        X_valid,
                        columns=FEATURES,
                    )

                    scores = model.predict_proba(
                        X_valid
                    )[:, 1]

                    output_chunk[
                        valid_mask
                    ] = scores.astype(
                        np.float32
                    )

                dst.write(
                    output_chunk,
                    1,
                    window=window,
                )

                print(
                    f"Processed rows "
                    f"{row_start} to "
                    f"{row_start + current_height - 1}"
                )

    print(
        f"\nSusceptibility raster saved to: "
        f"{output_path}"
    )


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Generate flood or landslide susceptibility "
            "raster from a trained model."
        )
    )

    parser.add_argument(
        "--stack",
        required=True,
        help="Path to the 10-band predictor GeoTIFF.",
    )

    parser.add_argument(
        "--model",
        required=True,
        help="Path to the trained .pkl model.",
    )

    parser.add_argument(
        "--output",
        required=True,
        help="Output susceptibility GeoTIFF.",
    )

    args = parser.parse_args()

    generate_susceptibility(
        predictor_stack=Path(args.stack),
        model_path=Path(args.model),
        output_path=Path(args.output),
    )


if __name__ == "__main__":
    main()