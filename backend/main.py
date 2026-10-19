import csv
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware


app = FastAPI(
    title="Multi-Hazard Susceptibility API",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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


@app.get("/roads/summary")
def get_road_integration_summary():
    summary_path = Path(
        "outputs/road_integration_summary.csv"
    )

    if not summary_path.exists():
        return {
            "error": "Road integration summary not found."
        }

    with summary_path.open(
        "r",
        encoding="utf-8",
        newline="",
    ) as file:
        reader = csv.DictReader(file)
        row = next(reader)

    return {
        "total_edges": int(row["total_edges"]),
        "valid_edges": int(row["valid_edges"]),
        "nodata_edges": int(row["nodata_edges"]),
        "coverage_percent": float(
            row["coverage_percent"]
        ),
        "flood_dominant": int(
            row["flood_dominant"]
        ),
        "landslide_dominant": int(
            row["landslide_dominant"]
        ),
        "mean_final_score": float(
            row["mean_final_score"]
        ),
        "median_final_score": float(
            row["median_final_score"]
        ),
        "max_final_score": float(
            row["max_final_score"]
        ),
    }