
"""Load the synthetic flood and landslide ML dataset."""

from pathlib import Path

import geopandas as gpd


DATA_PATH = Path(
    r"C:\GIS_Work\Synthetic_ML_Prototype_Final.gpkg"
)


def load_synthetic_data(path=DATA_PATH):
    """Read the synthetic sampling dataset from a GeoPackage."""

    path = Path(path)

    if not path.is_file():
        raise FileNotFoundError(
            f"GeoPackage file not found: {path}"
        )

    data = gpd.read_file(path)

    print("Synthetic dataset loaded successfully!")
    print(f"Total sampling points: {len(data)}")
    print(f"Coordinate system: {data.crs}")
    print(f"Columns: {list(data.columns)}")

    return data


if __name__ == "__main__":
    dataset = load_synthetic_data()
