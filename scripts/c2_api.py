"""Run the read-only Component 2 dashboard API."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.road_network.c2_api import main


if __name__ == "__main__":
    main()
