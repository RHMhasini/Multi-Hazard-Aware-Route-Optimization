
"""Prepare stratified train/test splits for synthetic hazard models."""

from sklearn.model_selection import train_test_split

from data_loader import load_synthetic_data
from data_validation import validate_dataset

FACTOR_COLUMNS = [f"factor_{i}" for i in range(1, 9)]
TARGET_COLUMNS = ["flood_sim", "landslide_sim"]

RANDOM_STATE = 42
TEST_SIZE = 0.20


def prepare_split(data, target_column):
    """Create reproducible stratified train/test datasets."""

    if target_column not in TARGET_COLUMNS:
        raise ValueError(f"Unsupported target: {target_column}")

    X = data[FACTOR_COLUMNS]
    y = data[target_column].astype(int)

    X_train, X_test, y_train, y_test = train_test_split(
        X,
        y,
        test_size=TEST_SIZE,
        random_state=RANDOM_STATE,
        stratify=y,
    )

    print(f"\n=== {target_column.upper()} SPLIT ===")
    print(f"Training samples: {len(X_train)}")
    print(f"Testing samples: {len(X_test)}")

    print("\nTraining class distribution:")
    print(y_train.value_counts().sort_index().to_string())

    print("\nTesting class distribution:")
    print(y_test.value_counts().sort_index().to_string())

    return X_train, X_test, y_train, y_test


if __name__ == "__main__":
    dataset = load_synthetic_data()
    validate_dataset(dataset)

    for target in TARGET_COLUMNS:
        prepare_split(dataset, target)
