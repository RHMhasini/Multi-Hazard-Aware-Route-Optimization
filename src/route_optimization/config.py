"""Configuration settings, thresholds, CRS definitions, and risk formula options for route optimization.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional


class RiskFormulaChoice(str, Enum):
    """Supported risk formulation options for converting hazard into edge impedance."""
    LINEAR = "linear"                      # impedance = (1 - alpha)*time + alpha*exposure
    EXPONENTIAL = "exponential"            # impedance = time * exp(beta * hazard)
    MAX_EDGE_HAZARD = "max_edge_hazard"    # minimize max hazard along path, secondary time
    THRESHOLD_PENALTY = "threshold_penalty"# severe penalty applied when hazard >= threshold
    TIME_WEIGHTED = "time_weighted"        # hazard * travel_time integration


@dataclass
class RouteOptimizationConfig:
    """Master configuration for Component 3 Route Optimization."""

    # Risk formulation
    risk_formula: RiskFormulaChoice = RiskFormulaChoice.LINEAR
    alpha_tradeoff: float = 0.5            # 1.0 = time only, 0.0 = risk only
    exponential_beta: float = 3.0          # Scaling factor for exponential risk formula

    # Hazard & safety thresholds
    hazard_warning_threshold: float = 0.5  # Moderate risk threshold
    hazard_critical_threshold: float = 0.7 # High risk threshold (triggers penalty / detour)
    hazard_impassable_threshold: float = 0.95 # Impassable hazard level (edge effectively closed)
    threshold_penalty_multiplier: float = 10.0 # Penalty factor applied to edges >= critical threshold

    # Coordinate Reference Systems
    crs_source: str = "EPSG:4326"          # Default geographic CRS (WGS84 lat/lon)
    crs_projected: str = "EPSG:5235"       # Sri Lanka Grid (SLD99 / metric) for accurate planar distances

    # Reproducibility
    random_seed: int = 42

    # Speed limits by highway class in km/h for travel time calculation
    highway_speeds_kph: Dict[str, float] = field(
        default_factory=lambda: {
            "motorway": 100.0,
            "trunk": 80.0,
            "primary": 60.0,
            "secondary": 50.0,
            "tertiary": 40.0,
            "residential": 30.0,
            "unclassified": 30.0,
            "service": 20.0,
            "track": 15.0,
            "default": 35.0,
        }
    )

    # Detour & Pareto constraints
    max_detour_ratio: float = 1.5          # Maximum acceptable travel time relative to quickest route
    pareto_sweep_steps: int = 21           # Number of alpha values for weighted-sum sweep [0.0, 0.05, ..., 1.0]

    def get_speed_for_highway(self, highway: Optional[str]) -> float:
        """Return travel speed in km/h for a given highway classification."""
        if not highway:
            return self.highway_speeds_kph["default"]
        # Handle list-valued highway tags or compound strings
        if isinstance(highway, list):
            highway = highway[0]
        cleaned_highway = str(highway).lower().strip()
        return self.highway_speeds_kph.get(cleaned_highway, self.highway_speeds_kph["default"])


# Default global instance
DEFAULT_CONFIG = RouteOptimizationConfig()
