from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException


app = FastAPI(
    title="Multi-Hazard Susceptibility API",
    version="0.1.0",
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUTS_DIR = PROJECT_ROOT / "outputs"


@app.get("/")
def root():
    return {
        "message": "Multi-Hazard Susceptibility API is running"
    }


@app.get("/health")
def health():
    return {
        "status": "ok"
    }


def load_csv(filename: str):
    file_path = OUTPUTS_DIR / filename

    if not file_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"{filename} not found"
        )

    df = pd.read_csv(file_path)

    return df.to_dict(
        orient="records"
    )


@app.get("/models/flood")
def flood_model_summary():
    return {
        "hazard": "Flood",
        "selected_model": "XGBoost",
        "results": load_csv(
            "flood_model_summary.csv"
        ),
    }


@app.get("/models/landslide")
def landslide_model_summary():
    return {
        "hazard": "Landslide",
        "selected_model": "Random Forest",
        "results": load_csv(
            "landslide_model_summary.csv"
        ),
    }


@app.get("/features/flood")
def flood_feature_importance():
    return load_csv(
        "flood_feature_importance.csv"
    )


@app.get("/features/landslide")
def landslide_feature_importance():
    return load_csv(
        "landslide_feature_importance.csv"
    )