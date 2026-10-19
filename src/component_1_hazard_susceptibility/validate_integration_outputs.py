from pathlib import Path

import numpy as np
import pandas as pd
import rasterio


OUTPUTS = Path("outputs")

FLOOD_RASTER = OUTPUTS / "flood_susceptibility_norm.tif"
LANDSLIDE_RASTER = OUTPUTS / "landslide_susceptibility_norm.tif"
MULTI_HAZARD_RASTER = OUTPUTS / "multi_hazard_max.tif"

ROAD_SCORES = OUTPUTS / "road_hazard_scores.csv"
COMPONENT3_HANDOFF = OUTPUTS / "component3_road_hazard_handoff.csv"

VALIDATION_OUTPUT = OUTPUTS / "validation_summary.csv"


results = []


def add_result(test_name, passed, details):
    results.append(
        {
            "Test": test_name,
            "Status": "PASS" if passed else "FAIL",
            "Details": details,
        }
    )


def validate_rasters():
    print("\n=== Raster Validation ===")

    with rasterio.open(FLOOD_RASTER) as flood_src, \
         rasterio.open(LANDSLIDE_RASTER) as landslide_src, \
         rasterio.open(MULTI_HAZARD_RASTER) as multi_src:

        aligned = (
            flood_src.crs == landslide_src.crs == multi_src.crs
            and flood_src.transform
            == landslide_src.transform
            == multi_src.transform
            and flood_src.width
            == landslide_src.width
            == multi_src.width
            and flood_src.height
            == landslide_src.height
            == multi_src.height
        )

        add_result(
            "Raster alignment",
            aligned,
            (
                f"CRS={flood_src.crs}, "
                f"size={flood_src.width}x{flood_src.height}"
            ),
        )

        flood = flood_src.read(1).astype(np.float32)
        landslide = landslide_src.read(1).astype(np.float32)
        multi = multi_src.read(1).astype(np.float32)

        flood_valid = np.isfinite(flood)
        landslide_valid = np.isfinite(landslide)
        multi_valid = np.isfinite(multi)

        if flood_src.nodata is not None:
            flood_valid &= flood != flood_src.nodata

        if landslide_src.nodata is not None:
            landslide_valid &= landslide != landslide_src.nodata

        if multi_src.nodata is not None:
            multi_valid &= multi != multi_src.nodata

        flood_range_ok = np.all(
            (flood[flood_valid] >= 0)
            & (flood[flood_valid] <= 1)
        )

        landslide_range_ok = np.all(
            (landslide[landslide_valid] >= 0)
            & (landslide[landslide_valid] <= 1)
        )

        multi_range_ok = np.all(
            (multi[multi_valid] >= 0)
            & (multi[multi_valid] <= 1)
        )

        add_result(
            "Flood score range",
            flood_range_ok,
            (
                f"min={flood[flood_valid].min():.6f}, "
                f"max={flood[flood_valid].max():.6f}"
            ),
        )

        add_result(
            "Landslide score range",
            landslide_range_ok,
            (
                f"min={landslide[landslide_valid].min():.6f}, "
                f"max={landslide[landslide_valid].max():.6f}"
            ),
        )

        add_result(
            "Multi-hazard score range",
            multi_range_ok,
            (
                f"min={multi[multi_valid].min():.6f}, "
                f"max={multi[multi_valid].max():.6f}"
            ),
        )

        common_valid = (
            flood_valid
            & landslide_valid
            & multi_valid
        )

        expected_multi = np.maximum(
            flood[common_valid],
            landslide[common_valid],
        )

        max_rule_ok = np.allclose(
            multi[common_valid],
            expected_multi,
            atol=1e-6,
        )

        add_result(
            "Pixel-wise MAX fusion",
            max_rule_ok,
            (
                f"Validated pixels="
                f"{int(common_valid.sum()):,}"
            ),
        )


def validate_road_scores():
    print("\n=== Road Integration Validation ===")

    roads = pd.read_csv(ROAD_SCORES)

    total_rows = len(roads)
    unique_ids = roads["road_id"].nunique()
    missing_ids = roads["road_id"].isna().sum()

    id_ok = (
        total_rows == unique_ids
        and missing_ids == 0
    )

    add_result(
        "Unique road IDs",
        id_ok,
        (
            f"rows={total_rows:,}, "
            f"unique={unique_ids:,}, "
            f"missing={missing_ids:,}"
        ),
    )

    score_columns = [
        "flood_road_score",
        "landslide_road_score",
        "final_road_score",
    ]

    valid = roads.dropna(
        subset=score_columns
    ).copy()

    nodata_count = total_rows - len(valid)

    score_range_ok = (
        valid[score_columns]
        .ge(0)
        .all()
        .all()
        and valid[score_columns]
        .le(1)
        .all()
        .all()
    )

    add_result(
        "Road score range",
        score_range_ok,
        (
            f"valid={len(valid):,}, "
            f"NoData={nodata_count:,}"
        ),
    )

    road_max_ok = np.allclose(
        valid["final_road_score"],
        valid[
            [
                "flood_road_score",
                "landslide_road_score",
            ]
        ].max(axis=1),
        atol=1e-6,
    )

    add_result(
        "Road-level MAX rule",
        road_max_ok,
        f"Validated roads={len(valid):,}",
    )

    nodata_consistency = (
        roads["flood_road_score"].isna()
        == roads["landslide_road_score"].isna()
    ).all() and (
        roads["flood_road_score"].isna()
        == roads["final_road_score"].isna()
    ).all()

    add_result(
        "NoData consistency",
        nodata_consistency,
        f"NoData roads={nodata_count:,}",
    )

    coverage = (
        len(valid) / total_rows * 100
    )

    add_result(
        "Road hazard coverage",
        coverage >= 99,
        f"coverage={coverage:.2f}%",
    )


def validate_component3_handoff():
    print("\n=== Component 3 Handoff Validation ===")

    roads = pd.read_csv(ROAD_SCORES)
    handoff = pd.read_csv(
        COMPONENT3_HANDOFF
    )

    same_rows = len(roads) == len(handoff)

    add_result(
        "Component 3 row count",
        same_rows,
        (
            f"road_scores={len(roads):,}, "
            f"handoff={len(handoff):,}"
        ),
    )

    valid_expected = (
        roads["final_road_score"]
        .notna()
        .sum()
    )

    valid_handoff = (
        handoff["hazard_valid"]
        .astype(bool)
        .sum()
    )

    validity_ok = (
        valid_expected == valid_handoff
    )

    add_result(
        "Component 3 hazard_valid flag",
        validity_ok,
        (
            f"expected={valid_expected:,}, "
            f"handoff={valid_handoff:,}"
        ),
    )

    ids_match = (
        roads["road_id"].astype(str)
        .equals(
            handoff["road_id"].astype(str)
        )
    )

    add_result(
        "Component 3 road ID preservation",
        ids_match,
        "Road IDs preserved in original order.",
    )


def main():
    required_files = [
        FLOOD_RASTER,
        LANDSLIDE_RASTER,
        MULTI_HAZARD_RASTER,
        ROAD_SCORES,
        COMPONENT3_HANDOFF,
    ]

    missing = [
        str(path)
        for path in required_files
        if not path.exists()
    ]

    if missing:
        raise FileNotFoundError(
            "Missing required files:\n"
            + "\n".join(missing)
        )

    validate_rasters()
    validate_road_scores()
    validate_component3_handoff()

    summary = pd.DataFrame(results)

    summary.to_csv(
        VALIDATION_OUTPUT,
        index=False,
    )

    print("\n=== VALIDATION SUMMARY ===")
    print(summary.to_string(index=False))

    passed = (
        summary["Status"] == "PASS"
    ).sum()

    total = len(summary)

    print(
        f"\nPassed: {passed}/{total}"
    )

    print(
        f"Validation report: "
        f"{VALIDATION_OUTPUT}"
    )

    if passed != total:
        raise SystemExit(
            "One or more validation tests failed."
        )


if __name__ == "__main__":
    main()