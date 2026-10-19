from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import rasterio


PROJECT_ROOT = Path(__file__).resolve().parents[2]

OUTPUTS_DIR = PROJECT_ROOT / "outputs"
PREVIEW_DIR = PROJECT_ROOT / "frontend" / "public" / "maps"

PREVIEW_DIR.mkdir(
    parents=True,
    exist_ok=True,
)


MAPS = [
    {
        "input": OUTPUTS_DIR / "flood_susceptibility_norm.tif",
        "output": PREVIEW_DIR / "flood_susceptibility.png",
        "title": "Flood Susceptibility",
    },
    {
        "input": OUTPUTS_DIR / "landslide_susceptibility_norm.tif",
        "output": PREVIEW_DIR / "landslide_susceptibility.png",
        "title": "Landslide Susceptibility",
    },
    {
        "input": OUTPUTS_DIR / "multi_hazard_max.tif",
        "output": PREVIEW_DIR / "multi_hazard_max.png",
        "title": "Multi-Hazard Susceptibility (MAX Fusion)",
    },
]


def create_preview(input_path, output_path, title):
    with rasterio.open(input_path) as src:
        data = src.read(1).astype(np.float32)

        nodata = src.nodata

        if nodata is not None:
            data[data == nodata] = np.nan

    fig, ax = plt.subplots(
        figsize=(7, 8)
    )

    image = ax.imshow(
        data,
        cmap="RdYlGn_r",
        vmin=0,
        vmax=1,
    )

    ax.set_title(
        title,
        fontsize=14,
        fontweight="bold",
        pad=12,
    )

    ax.axis("off")

    colorbar = fig.colorbar(
        image,
        ax=ax,
        fraction=0.04,
        pad=0.03,
    )

    colorbar.set_label(
        "Susceptibility Score (0–1)"
    )

    plt.tight_layout()

    fig.savefig(
        output_path,
        dpi=180,
        bbox_inches="tight",
        facecolor="white",
    )

    plt.close(fig)

    print(f"Saved: {output_path}")


for item in MAPS:
    create_preview(
        item["input"],
        item["output"],
        item["title"],
    )

print("\nAll map previews generated successfully.")