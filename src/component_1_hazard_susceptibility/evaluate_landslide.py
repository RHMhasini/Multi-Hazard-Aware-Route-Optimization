
"""Evaluate the synthetic landslide model and generate PP1 charts."""

from pathlib import Path

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
from sklearn.metrics import (
    ConfusionMatrixDisplay,
    balanced_accuracy_score,
    classification_report,
    confusion_matrix,
    precision_score,
    recall_score,
    f1_score,
)

from data_loader import load_synthetic_data
from data_validation import validate_dataset
from train_landslide import train_landslide_model

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = PROJECT_ROOT / "outputs" / "landslide_evaluation"

FACTOR_NAMES = {
    "factor_1": "TRI",
    "factor_2": "TWI",
    "factor_3": "Aspect",
    "factor_4": "Distance to Streams",
    "factor_5": "Drainage Density",
    "factor_6": "Profile Curvature",
    "factor_7": "Slope",
    "factor_8": "Tangential Curvature",
}


def evaluate_landslide_model():
    """Evaluate held-out predictions and save visual evidence."""

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    data = load_synthetic_data()
    validate_dataset(data)

    model, X_test, y_test = train_landslide_model(data)
    predictions = model.predict(X_test)

    print("\n=== LANDSLIDE MODEL EVALUATION ===")
    print(classification_report(
        y_test,
        predictions,
        labels=[0, 1],
        zero_division=0,
    ))

    print(
        "Balanced accuracy:",
        round(balanced_accuracy_score(y_test, predictions), 4),
    )
    print(
        "Precision:",
        round(precision_score(
            y_test, predictions, zero_division=0
        ), 4),
    )
    print(
        "Recall:",
        round(recall_score(
            y_test, predictions, zero_division=0
        ), 4),
    )
    print(
        "F1-score:",
        round(f1_score(
            y_test, predictions, zero_division=0
        ), 4),
    )

    # Confusion matrix
    cm = confusion_matrix(y_test, predictions, labels=[0, 1])

    fig, ax = plt.subplots(figsize=(6, 5))
    display = ConfusionMatrixDisplay(
        confusion_matrix=cm,
        display_labels=["Non-Landslide", "Landslide"],
    )
    display.plot(ax=ax, cmap="Blues", colorbar=False)
    ax.set_title("Synthetic Landslide Model - Confusion Matrix")
    fig.tight_layout()

    confusion_path = OUTPUT_DIR / "landslide_confusion_matrix.png"
    fig.savefig(confusion_path, dpi=200)
    plt.close(fig)

    # Feature importance
    importances = model.feature_importances_
    feature_names = [
        FACTOR_NAMES.get(name, name)
        for name in X_test.columns
    ]

    order = np.argsort(importances)

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.barh(
        np.array(feature_names)[order],
        importances[order],
    )
    ax.set_xlabel("Random Forest Feature Importance")
    ax.set_title("Synthetic Landslide Model - Feature Importance")
    fig.tight_layout()

    importance_path = OUTPUT_DIR / "landslide_feature_importance.png"
    fig.savefig(importance_path, dpi=200)
    plt.close(fig)

    print("\n=== VISUALIZATIONS SAVED ===")
    print(confusion_path)
    print(importance_path)


if __name__ == "__main__":
    evaluate_landslide_model()
