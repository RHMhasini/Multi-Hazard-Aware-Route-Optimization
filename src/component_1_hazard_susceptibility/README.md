
# Component 1: Multi-Hazard Susceptibility Modelling

## Project
Multi-Hazard-Aware Emergency Logistics Route Optimization for Sabaragamuwa Province

## Study Area
Ratnapura and Kegalle Districts, Sri Lanka.

## Objectives
- Develop a flood susceptibility model.
- Develop a landslide susceptibility model.
- Generate separate susceptibility maps.
- Combine outputs into a multi-hazard susceptibility surface.
- Provide hazard scores for road-network route optimization.

## Tools
- QGIS 3.44
- Python
- Scikit-learn
- GeoPandas
- Rasterio

## Current Progress
- Study area boundary prepared.
- DEM preprocessing completed.
- Eight terrain-related predictor layers prepared.
- Synthetic sampling dataset created with 491 valid points.
- Synthetic flood and landslide labels generated.
- GitHub feature branch created.
- Python implementation setup initiated.

## Data Status
Historical flood and landslide inventories and rainfall observations are pending.

Synthetic data will be used only to demonstrate the prototype ML workflow. Synthetic model evaluation will not be presented as real-world hazard prediction accuracy.

## Next Steps
1. Load and validate the synthetic dataset.
2. Train separate flood and landslide Random Forest models.
3. Evaluate prototype models.
4. Generate susceptibility maps.
5. Integrate multi-hazard outputs.
6. Replace synthetic labels with real observations and retrain.
