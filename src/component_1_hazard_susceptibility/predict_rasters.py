
"""Generate 30 m synthetic-prototype flood and landslide probability rasters."""

from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import rasterio
from rasterio.windows import Window


# --------------------------------------------------
# 1. PATH CONFIGURATION
# --------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[2]

FACTOR_STACK = Path(
    r"C:\GIS_Work\04_Terrain_Factors\ML_factors_8band_stack.vrt"
)

VALID_MASK = Path(
    r"C:\GIS_Work\04_Terrain_Factors\common_valid_mask_30m.tif"
)

MODEL_DIR = PROJECT_ROOT / "outputs" / "models"
OUTPUT_DIR = PROJECT_ROOT / "outputs" / "susceptibility_maps"

FEATURE_NAMES = [f"factor_{i}" for i in range(1, 9)]

BLOCK_SIZE = 256
OUTPUT_NODATA = -9999.0

# Maximum acceptable transform difference: 1 mm
ALIGNMENT_TOLERANCE = 0.001


# --------------------------------------------------
# 2. LOAD TRAINED MODEL
# --------------------------------------------------

def load_model(filename):
    """Load and validate a synthetic-trained Random Forest model."""

    model_path = MODEL_DIR / filename

    if not model_path.exists():
        raise FileNotFoundError(
            f"Trained model not found: {model_path}"
        )

    model = joblib.load(model_path)

    if model.n_features_in_ != len(FEATURE_NAMES):
        raise ValueError(
            f"{filename}: Expected 8 model features."
        )

    saved_names = list(
        getattr(model, "feature_names_in_", [])
    )

    if saved_names != FEATURE_NAMES:
        raise ValueError(
            f"{filename}: Feature order mismatch."
        )

    if 1 not in model.classes_:
        raise ValueError(
            f"{filename}: Positive class 1 not found."
        )

    return model


# --------------------------------------------------
# 3. RASTER ALIGNMENT VALIDATION
# --------------------------------------------------

def validate_raster_alignment(factors, mask):
    """Check CRS, dimensions and pixel-grid alignment."""

    if factors.count != 8:
        raise ValueError(
            "Factor stack must contain exactly 8 bands."
        )

    if mask.count != 1:
        raise ValueError(
            "Valid mask must contain exactly 1 band."
        )

    if factors.crs != mask.crs:
        raise ValueError(
            "Factor stack and valid mask CRS do not match."
        )

    if (
        factors.width != mask.width
        or factors.height != mask.height
    ):
        raise ValueError(
            "Factor stack and valid mask dimensions do not match."
        )

    if not np.allclose(
        tuple(factors.transform),
        tuple(mask.transform),
        rtol=0,
        atol=ALIGNMENT_TOLERANCE,
    ):
        raise ValueError(
            "Factor stack and valid mask pixel grids are not aligned."
        )

    print("\nRaster alignment validation passed!")
    print(f"CRS: {factors.crs}")
    print(f"Raster size: {factors.width} x {factors.height}")
    print(f"Resolution: {factors.res}")


# --------------------------------------------------
# 4. GENERATE SUSCEPTIBILITY MAPS
# --------------------------------------------------

def predict_maps():
    """Predict flood and landslide probabilities using raster windows."""

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    print("\nLoading trained Random Forest models...")

    flood_model = load_model(
        "flood_rf_synthetic.joblib"
    )

    landslide_model = load_model(
        "landslide_rf_synthetic.joblib"
    )

    print("Both models loaded successfully!")

    flood_path = (
        OUTPUT_DIR / "flood_probability_synthetic.tif"
    )

    landslide_path = (
        OUTPUT_DIR / "landslide_probability_synthetic.tif"
    )

    with rasterio.open(FACTOR_STACK) as factors, \
         rasterio.open(VALID_MASK) as mask:

        validate_raster_alignment(
            factors,
            mask,
        )

        # GeoTIFF output configuration
        profile = factors.profile.copy()

        profile.update(
            driver="GTiff",
            dtype="float32",
            count=1,
            nodata=OUTPUT_NODATA,
            compress="deflate",
            tiled=True,
            blockxsize=256,
            blockysize=256,
            predictor=3,
        )

        flood_positive_index = list(
            flood_model.classes_
        ).index(1)

        landslide_positive_index = list(
            landslide_model.classes_
        ).index(1)

        total_valid = 0

        with rasterio.open(
            flood_path, "w", **profile
        ) as flood_dst, rasterio.open(
            landslide_path, "w", **profile
        ) as landslide_dst:

            flood_dst.set_band_description(
                1,
                "Synthetic-trained flood probability",
            )

            landslide_dst.set_band_description(
                1,
                "Synthetic-trained landslide probability",
            )

            flood_dst.update_tags(
                1,
                training_data="synthetic",
                validation_status="prototype_only",
            )

            landslide_dst.update_tags(
                1,
                training_data="synthetic",
                validation_status="prototype_only",
            )

            # Process the raster in manageable blocks
            for row in range(
                0,
                factors.height,
                BLOCK_SIZE,
            ):
                for col in range(
                    0,
                    factors.width,
                    BLOCK_SIZE,
                ):

                    height = min(
                        BLOCK_SIZE,
                        factors.height - row,
                    )

                    width = min(
                        BLOCK_SIZE,
                        factors.width - col,
                    )

                    window = Window(
                        col,
                        row,
                        width,
                        height,
                    )

                    # Read environmental factors
                    stack = factors.read(
                        window=window
                    )

                    # Read common valid-area mask
                    mask_data = mask.read(
                        1,
                        window=window,
                    )

                    valid_mask = mask_data == 1

                    # Exclude invalid and NoData factor values
                    factor_valid = np.all(
                        np.isfinite(stack),
                        axis=0,
                    )

                    for band_index in range(factors.count):
                        nodata_value = factors.nodatavals[
                            band_index
                        ]

                        if nodata_value is not None:
                            if np.isnan(nodata_value):
                                factor_valid &= ~np.isnan(
                                    stack[band_index]
                                )
                            else:
                                factor_valid &= (
                                    stack[band_index]
                                    != nodata_value
                                )

                    valid = valid_mask & factor_valid

                    # Initialize output blocks with NoData
                    flood_output = np.full(
                        (height, width),
                        OUTPUT_NODATA,
                        dtype=np.float32,
                    )

                    landslide_output = np.full(
                        (height, width),
                        OUTPUT_NODATA,
                        dtype=np.float32,
                    )

                    if np.any(valid):

                        # Shape: (valid_pixels, 8 factors)
                        X = stack[:, valid].T

                        X = pd.DataFrame(
                            X,
                            columns=FEATURE_NAMES,
                        )

                        # Flood probability for class 1
                        flood_probabilities = (
                            flood_model.predict_proba(X)[
                                :, flood_positive_index
                            ]
                        )

                        # Landslide probability for class 1
                        landslide_probabilities = (
                            landslide_model.predict_proba(X)[
                                :, landslide_positive_index
                            ]
                        )

                        flood_output[valid] = (
                            flood_probabilities.astype(
                                np.float32
                            )
                        )

                        landslide_output[valid] = (
                            landslide_probabilities.astype(
                                np.float32
                            )
                        )

                        total_valid += int(
                            np.count_nonzero(valid)
                        )

                    # Write each processed window
                    flood_dst.write(
                        flood_output,
                        1,
                        window=window,
                    )

                    landslide_dst.write(
                        landslide_output,
                        1,
                        window=window,
                    )

                print(
                    f"Processed rows up to "
                    f"{min(row + BLOCK_SIZE, factors.height)}"
                    f"/{factors.height}"
                )

    print("\n===================================")
    print("RASTER PREDICTION COMPLETED")
    print("===================================")

    print(
        f"Valid predicted pixels: {total_valid:,}"
    )

    print(f"\nFlood probability map:\n{flood_path}")

    print(
        f"\nLandslide probability map:\n{landslide_path}"
    )

    print(
        "\nIMPORTANT: These are synthetic-trained "
        "prototype susceptibility maps, not "
        "historically validated hazard maps."
    )


# --------------------------------------------------
# 5. MAIN EXECUTION
# --------------------------------------------------

if __name__ == "__main__":
    predict_maps()
