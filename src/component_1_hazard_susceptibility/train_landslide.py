
"""Train a Random Forest on synthetic landslide susceptibility labels."""

from sklearn.ensemble import RandomForestClassifier

from data_loader import load_synthetic_data
from data_validation import validate_dataset
from train_test_split import prepare_split

RANDOM_STATE = 42


def train_landslide_model(data):
    """Train a class-weighted synthetic landslide classifier."""

    X_train, X_test, y_train, y_test = prepare_split(
        data, "landslide_sim"
    )

    model = RandomForestClassifier(
        n_estimators=200,
        max_depth=10,
        min_samples_leaf=2,
        class_weight="balanced",
        random_state=RANDOM_STATE,
        n_jobs=-1,
    )

    print("\n=== LANDSLIDE RANDOM FOREST TRAINING ===")

    model.fit(X_train, y_train)

    print("Landslide model trained successfully!")
    print(f"Number of trees: {model.n_estimators}")
    print(f"Training samples: {len(X_train)}")
    print(f"Testing samples reserved: {len(X_test)}")
    print(f"Number of features: {model.n_features_in_}")
    print(f"Model classes: {model.classes_.tolist()}")

    return model, X_test, y_test


if __name__ == "__main__":
    dataset = load_synthetic_data()
    validate_dataset(dataset)
    train_landslide_model(dataset)
