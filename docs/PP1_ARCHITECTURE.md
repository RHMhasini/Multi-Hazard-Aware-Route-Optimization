# PP1 Architecture and Integration

## Project
**J26-DS-301 – Multi-Hazard-Aware Emergency Logistics Route Optimization for Sabaragamuwa Province**

## Component 1
**Flood and Landslide Susceptibility Modelling**

```mermaid
flowchart LR

    A[Historical Flood Events] --> F1
    B[Historical Landslide Events] --> L1
    C[DEM-derived Predictors] --> F1
    C --> L1
    R[Rainfall Data<br/>Final Stage] --> F1
    R --> L1

    F1[Flood ML Pipeline<br/>RF / XGBoost / LightGBM]
    L1[Landslide ML Pipeline<br/>RF / XGBoost / CatBoost]

    F1 --> F2[Flood Susceptibility<br/>0–1]
    L1 --> L2[Landslide Susceptibility<br/>0–1]

    F2 --> M[Pixel-wise MAX Fusion]
    L2 --> M

    M --> MH[Multi-Hazard<br/>Susceptibility Surface]

    C2[Component 2<br/>Directed Road Graph<br/>599,077 Edges]
    C2 --> RS

    F2 --> RS
    L2 --> RS

    RS[Road-Level Hazard Scoring<br/>MAX along Road Edge]

    RS --> O[Component 1 Road Output<br/>595,039 Valid Scores<br/>99.33% Coverage]

    O --> C3[Component 3<br/>Hazard-Aware Route Optimization]

    MH --> D[Prototype Dashboard]
    O --> D