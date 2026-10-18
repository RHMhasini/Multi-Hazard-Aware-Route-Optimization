import argparse
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)

from xgboost import XGBClassifier
from lightgbm import LGBMClassifier
from catboost import CatBoostClassifier

from spatial_cv import (
    add_projected_coordinates,
    create_spatial_folds,
)


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


def get_models(hazard: str):
    """
    Return the baseline candidate models for each hazard.
    """

    common_rf = RandomForestClassifier(
        n_estimators=300,
        random_state=42,
        n_jobs=-1,
    )

    common_xgb = XGBClassifier(
        n_estimators=300,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        random_state=42,
        eval_metric="logloss",
        n_jobs=-1,
    )

    if hazard == "flood":
        return {
            "Random Forest": common_rf,
            "XGBoost": common_xgb,
            "LightGBM": LGBMClassifier(
                n_estimators=300,
                learning_rate=0.05,
                random_state=42,
                verbosity=-1,
            ),
        }

    if hazard == "landslide":
        return {
            "Random Forest": common_rf,
            "XGBoost": common_xgb,
            "CatBoost": CatBoostClassifier(
                iterations=300,
                depth=6,
                learning_rate=0.05,
                random_seed=42,
                verbose=0,
            ),
        }

    raise ValueError(
        "Hazard must be either 'flood' or 'landslide'."
    )


def evaluate_models(df: pd.DataFrame, hazard: str):
    """
    Evaluate all candidate models using the predefined
    5-fold spatial cross-validation scheme.
    """

    X = df[FEATURES]
    y = df["Label"]
    folds = df["Spatial_Fold"]

    models = get_models(hazard)

    results = []

    for model_name, model in models.items():

        print(f"\nTraining: {model_name}")

        for fold in sorted(folds.unique()):

            train_mask = folds != fold
            test_mask = folds == fold

            X_train = X.loc[train_mask]
            y_train = y.loc[train_mask]

            X_test = X.loc[test_mask]
            y_test = y.loc[test_mask]

            model.fit(X_train, y_train)

            y_prob = model.predict_proba(X_test)[:, 1]
            y_pred = model.predict(X_test)

            y_pred = np.asarray(
                y_pred
            ).astype(int).ravel()

            metrics = {
                "Model": model_name,
                "Fold": int(fold),
                "ROC_AUC": roc_auc_score(
                    y_test,
                    y_prob,
                ),
                "F1": f1_score(
                    y_test,
                    y_pred,
                ),
                "Precision": precision_score(
                    y_test,
                    y_pred,
                    zero_division=0,
                ),
                "Recall": recall_score(
                    y_test,
                    y_pred,
                    zero_division=0,
                ),
            }

            results.append(metrics)

            print(
                f"Fold {fold}: "
                f"AUC={metrics['ROC_AUC']:.3f}, "
                f"F1={metrics['F1']:.3f}, "
                f"Precision={metrics['Precision']:.3f}, "
                f"Recall={metrics['Recall']:.3f}"
            )

    return pd.DataFrame(results)


def create_summary(results_df: pd.DataFrame):
    """
    Create mean ± standard deviation summary table.
    """

    return (
        results_df
        .groupby("Model")
        .agg(
            ROC_AUC_Mean=("ROC_AUC", "mean"),
            ROC_AUC_SD=("ROC_AUC", "std"),
            F1_Mean=("F1", "mean"),
            F1_SD=("F1", "std"),
            Precision_Mean=("Precision", "mean"),
            Precision_SD=("Precision", "std"),
            Recall_Mean=("Recall", "mean"),
            Recall_SD=("Recall", "std"),
        )
        .reset_index()
        .round(3)
    )


def select_best_model(summary_df: pd.DataFrame):
    """
    Select the preliminary best model using mean ROC-AUC
    as the primary metric.
    """

    best_row = summary_df.loc[
        summary_df["ROC_AUC_Mean"].idxmax()
    ]

    return best_row["Model"]


def train_final_model(
    df: pd.DataFrame,
    hazard: str,
    model_name: str,
):
    """
    Retrain the selected prototype model on the full dataset.
    """

    models = get_models(hazard)

    if model_name not in models:
        raise ValueError(
            f"Unknown model '{model_name}' for hazard '{hazard}'."
        )

    model = models[model_name]

    X = df[FEATURES]
    y = df["Label"]

    model.fit(X, y)

    return model


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Train and compare baseline flood or landslide "
            "susceptibility models using spatial CV."
        )
    )

    parser.add_argument(
        "--hazard",
        required=True,
        choices=["flood", "landslide"],
    )

    parser.add_argument(
        "--input",
        required=True,
        help="Path to the cleaned training CSV.",
    )

    parser.add_argument(
        "--output-dir",
        default="outputs",
        help="Directory for evaluation results.",
    )

    parser.add_argument(
        "--model-dir",
        default="models",
        help="Directory for saved model files.",
    )

    args = parser.parse_args()

    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    model_dir = Path(args.model_dir)

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    model_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    df = pd.read_csv(input_path)

    # Build spatial validation fields
    df = add_projected_coordinates(df)
    df = create_spatial_folds(df)

    # Baseline comparison
    results_df = evaluate_models(
        df,
        args.hazard,
    )

    summary_df = create_summary(results_df)

    best_model_name = select_best_model(
        summary_df
    )

    print("\nModel summary:")
    print(summary_df.to_string(index=False))

    print(
        f"\nPreliminary best model: "
        f"{best_model_name}"
    )

    # Retrain best model using full prototype dataset
    final_model = train_final_model(
        df,
        args.hazard,
        best_model_name,
    )

    # Save results
    results_path = output_dir / (
        f"{args.hazard}_cv_results.csv"
    )

    summary_path = output_dir / (
        f"{args.hazard}_model_summary.csv"
    )

    model_filename = (
        f"{args.hazard}_best_model.pkl"
    )

    model_path = model_dir / model_filename

    results_df.to_csv(
        results_path,
        index=False,
    )

    summary_df.to_csv(
        summary_path,
        index=False,
    )

    joblib.dump(
        final_model,
        model_path,
    )

    print(f"\nSaved CV results: {results_path}")
    print(f"Saved model summary: {summary_path}")
    print(f"Saved model: {model_path}")


if __name__ == "__main__":
    main()