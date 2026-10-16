
"""Train and save a Random Forest on synthetic flood susceptibility labels."""

from pathlib import Path

import joblib
from sklearn.ensemble import RandomForestClassifier

from data_loader import load_synthetic_data
from data_validation import validate_dataset
from train_test_split import prepare_split

RANDOM_STATE = 42
MODEL_DIR = Path(__file__).resolve().parents[2] / "outputs" / "models"


def train_flood_model(data):
    """Train and save a class-weighted synthetic flood classifier."""

    X_train, X_test, y_train, y_test = prepare_split(
        data, "flood_sim"
    )

    model = RandomForestClassifier(
        n_estimators=200,
        max_depth=10,
        min_samples_leaf=2,
        class_weight="balanced",
        random_state=RANDOM_STATE,
        n_jobs=-1,
    )

    print("\n=== FLOOD RANDOM FOREST TRAINING ===")

    model.fit(X_train, y_train)

    print("Flood model trained successfully!")
    print(f"Number of trees: {model.n_estimators}")
    print(f"Training samples: {len(X_train)}")
    print(f"Testing samples reserved: {len(X_test)}")
    print(f"Number of features: {model.n_features_in_}")
    print(f"Model classes: {model.classes_.tolist()}")

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    model_path = MODEL_DIR / "flood_rf_synthetic.joblib"

    joblib.dump(model, model_path)
    print(f"Model saved to: {model_path}")

    return model, X_test, y_test


if __name__ == "__main__":
    dataset = load_synthetic_data()
    validate_dataset(dataset)
    train_flood_model(dataset)
