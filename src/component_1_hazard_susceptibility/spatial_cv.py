import numpy as np
import pandas as pd

from pyproj import Transformer
from sklearn.cluster import KMeans
from sklearn.model_selection import StratifiedGroupKFold


def add_projected_coordinates(
    df: pd.DataFrame,
    longitude_col: str = "Longitude",
    latitude_col: str = "Latitude",
) -> pd.DataFrame:
    """
    Convert WGS84 longitude/latitude coordinates to
    EPSG:32644 (WGS 84 / UTM Zone 44N).

    Projected coordinates are used only for spatial
    cross-validation and are not model predictors.
    """

    df = df.copy()

    transformer = Transformer.from_crs(
        "EPSG:4326",
        "EPSG:32644",
        always_xy=True,
    )

    x_utm, y_utm = transformer.transform(
        df[longitude_col].to_numpy(),
        df[latitude_col].to_numpy(),
    )

    df["X_UTM"] = x_utm
    df["Y_UTM"] = y_utm

    return df


def create_spatial_folds(
    df: pd.DataFrame,
    target_col: str = "Label",
    n_blocks: int = 15,
    n_splits: int = 5,
    random_state: int = 42,
) -> pd.DataFrame:
    """
    Create spatially separated cross-validation folds.

    Workflow:
    1. Cluster projected coordinates into geographic blocks.
    2. Keep each spatial block together.
    3. Allocate complete blocks to stratified CV folds.
    """

    df = df.copy()

    required_columns = {
        "X_UTM",
        "Y_UTM",
        target_col,
    }

    missing = required_columns - set(df.columns)

    if missing:
        raise ValueError(
            f"Missing required columns: {sorted(missing)}"
        )

    # Create geographic blocks
    kmeans = KMeans(
        n_clusters=n_blocks,
        random_state=random_state,
        n_init=20,
    )

    df["Spatial_Block"] = kmeans.fit_predict(
        df[["X_UTM", "Y_UTM"]]
    )

    # Allocate whole blocks to spatial folds
    spatial_cv = StratifiedGroupKFold(
        n_splits=n_splits,
        shuffle=True,
        random_state=random_state,
    )

    df["Spatial_Fold"] = -1

    X_dummy = np.zeros((len(df), 1))
    y = df[target_col].to_numpy()
    groups = df["Spatial_Block"].to_numpy()

    for fold, (_, test_idx) in enumerate(
        spatial_cv.split(
            X_dummy,
            y,
            groups=groups,
        )
    ):
        df.iloc[
            test_idx,
            df.columns.get_loc("Spatial_Fold")
        ] = fold

    return df


def get_fold_distribution(
    df: pd.DataFrame,
    target_col: str = "Label",
) -> pd.DataFrame:
    """
    Return the class distribution for each spatial fold.
    """

    return pd.crosstab(
        df["Spatial_Fold"],
        df[target_col],
        margins=True,
    )