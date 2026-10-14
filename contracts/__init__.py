"""Contracts package for Multi-Hazard-Aware Route Optimization.

Defines schemas and interfaces for data exchange between:
- Component 1: Multi-Hazard Susceptibility & Risk Models
- Component 2: Road Network Graph Construction
- Component 3: Multi-Hazard-Aware Route Optimization
"""

from contracts.schemas import (
    C1HazardRasterContract,
    C1HazardVectorContract,
    C2GraphContract,
    C3ODPairContract,
    C3RouteRequestContract,
    C3RouteSegmentContract,
    C3RouteResultContract,
    C3EvaluationMetricsContract,
    RiskFormulaType,
)

__all__ = [
    "C1HazardRasterContract",
    "C1HazardVectorContract",
    "C2GraphContract",
    "C3ODPairContract",
    "C3RouteRequestContract",
    "C3RouteSegmentContract",
    "C3RouteResultContract",
    "C3EvaluationMetricsContract",
    "RiskFormulaType",
]
