# PP1 Testing Evidence
## Component 1 – Multi-Hazard Susceptibility Modelling

**Project ID:** J26-DS-301  
**Overall Project:** Multi-Hazard-Aware Emergency Logistics Route Optimization for Sabaragamuwa Province  
**Component:** Flood and Landslide Susceptibility Modelling  

---

## 1. Testing Objective

The purpose of this testing stage was to verify that the prototype outputs are technically consistent before integration with the route-optimization component.

The tests cover:

- susceptibility raster alignment,
- normalized score ranges,
- multi-hazard MAX fusion,
- road-network integration,
- stable road-edge identifiers,
- NoData handling,
- road-level MAX aggregation, and
- Component 3 handoff consistency.

---

## 2. Automated Validation Summary

| Test | Result | Evidence |
|---|---|---|
| Raster alignment | PASS | EPSG:32644, raster size 2978 × 4311 |
| Flood susceptibility range | PASS | 0.000000 – 1.000000 |
| Landslide susceptibility range | PASS | 0.000000 – 1.000000 |
| Multi-hazard susceptibility range | PASS | 0.021352 – 1.000000 |
| Pixel-wise MAX fusion | PASS | 5,417,434 valid pixels verified |
| Unique road IDs | PASS | 599,077 rows, 599,077 unique IDs, 0 missing |
| Road susceptibility range | PASS | 595,039 valid scored roads |
| Road-level MAX rule | PASS | 595,039 road edges verified |
| NoData consistency | PASS | 4,038 NoData road edges |
| Road hazard coverage | PASS | 99.33% |
| Component 3 row count | PASS | 599,077 input and output road edges |
| Component 3 hazard-valid flag | PASS | 595,039 valid road edges correctly flagged |
| Component 3 road ID preservation | PASS | Road IDs preserved in original order |

**Overall Result: 13 / 13 tests passed.**

---

## 3. Raster Validation

Flood, landslide, and multi-hazard susceptibility rasters were checked for spatial alignment.

All three outputs use:

- CRS: EPSG:32644
- Width: 2,978 pixels
- Height: 4,311 pixels
- Common 30 m raster grid

Flood and landslide susceptibility outputs were confirmed to remain within the normalized 0–1 range.

The multi-hazard surface was independently checked against:

`Multi-Hazard Score = MAX(Flood Score, Landslide Score)`

The MAX relationship was successfully verified for **5,417,434 valid raster pixels**.

---

## 4. Component 2 Road-Network Integration

The road graph received from Component 2 contained:

- **599,077 directed road edges**
- **599,077 unique stable road IDs**
- **0 missing road IDs**

The Component 2 graph was converted into a projected road-edge layer using EPSG:32644 so that it could be spatially integrated with the susceptibility rasters.

---

## 5. Road-Level Hazard Scoring

Flood and landslide susceptibility values were aggregated to each road edge.

The following road-level relationship was verified:

`Final Road Score = MAX(Flood Road Score, Landslide Road Score)`

Results:

- Total directed road edges: **599,077**
- Valid scored road edges: **595,039**
- NoData road edges: **4,038**
- Coverage: **99.33%**

All valid road scores remained within the 0–1 range.

---

## 6. NoData Handling

A total of **4,038 road edges** received NoData values.

Further testing showed that the midpoint of all 4,038 roads falls on raster pixels with the NoData value `-9999`.

Therefore, these edges are retained as NoData instead of assigning a score of zero.

This avoids incorrectly interpreting unavailable susceptibility data as very low susceptibility.

---

## 7. Component 3 Integration Contract

The Component 1 handoff contains:

- `road_id`
- `u`
- `v`
- `key`
- `flood_road_score`
- `landslide_road_score`
- `final_road_score`
- `dominant_hazard`
- `hazard_valid`
- `critical_x`
- `critical_y`

The validation confirmed:

- 599,077 road edges are preserved in the handoff.
- Stable road IDs remain unchanged.
- 595,039 valid hazard scores are correctly flagged.
- 4,038 unavailable scores remain explicitly identified as NoData.

This output can be consumed by Component 3 for hazard-aware route optimization.

---

## 8. Prototype Limitation

The current PP1 hazard models use synthetic historical hazard samples and DEM-derived predictors.

Therefore, the current susceptibility values and model-performance metrics are used to demonstrate the implemented workflow and system integration, not as final scientific hazard estimates.

The same pipeline will be rerun using official historical hazard inventories and rainfall observations when the requested real datasets become available.

---

## 9. Reproducibility

Automated validation is implemented in:

`src/component_1_hazard_susceptibility/validate_integration_outputs.py`

Machine-readable validation results are stored in:

`outputs/validation_summary.csv`

The validation script completed successfully with:

**13 / 13 tests passed.**