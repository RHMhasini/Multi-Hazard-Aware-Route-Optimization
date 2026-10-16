
"""Validate synthetic flood and landslide training data."""

from data_loader import load_synthetic_data

FACTOR_COLUMNS = [f"factor_{i}" for i in range(1, 9)]
LABEL_COLUMNS = ["flood_sim", "landslide_sim"]
REQUIRED_COLUMNS = FACTOR_COLUMNS + LABEL_COLUMNS


def validate_dataset(data):
    """Check required fields, missing values and label distributions."""

    print("\n=== SYNTHETIC DATASET VALIDATION ===")

    # Check required columns
    missing_columns = [
        col for col in REQUIRED_COLUMNS
        if col not in data.columns
    ]

    if missing_columns:
        raise ValueError(
            f"Missing required columns: {missing_columns}"
        )

    print("All 8 environmental factors and 2 labels are present.")

    # Check missing values
    missing_values = data[REQUIRED_COLUMNS].isna().sum()

    print("\nMissing values by column:")
    print(missing_values.to_string())

    if missing_values.sum() > 0:
        raise ValueError("Dataset contains missing feature or label values.")

    # Check hazard labels
    for label in LABEL_COLUMNS:
        if not data[label].isin([0, 1]).all():
            raise ValueError(f"{label} contains non-binary values.")

        print(f"\n{label} class distribution:")
        print(data[label].value_counts().sort_index().to_string())

    print("\nDataset validation completed successfully!")


if __name__ == "__main__":
    dataset = load_synthetic_data()
    validate_dataset(dataset)
