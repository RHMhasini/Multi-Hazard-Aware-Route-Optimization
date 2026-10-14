"""Data exchange schemas and contracts for Component 3 Route Optimization.

Provides typed validation contracts for:
- Component 1: Hazard Susceptibility and Severity Inputs
- Component 2: Road Network Graph Representation
- Component 3: Route Optimization Requests, Routes, and Performance Metrics
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union
from pydantic import BaseModel, Field, field_validator


class RiskFormulaType(str, Enum):
    """Supported risk formulation strategies for edge impedance calculation."""
    LINEAR = "linear"
    EXPONENTIAL = "exponential"
    MAX_EDGE_HAZARD = "max_edge_hazard"
    THRESHOLD_PENALTY = "threshold_penalty"
    TIME_WEIGHTED = "time_weighted"


class C1HazardRasterContract(BaseModel):
    """Schema for raster hazard data produced by Component 1."""
    hazard_type: str = Field(..., description="Hazard type (e.g. 'flood', 'landslide', 'multi_hazard')")
    crs: str = Field(default="EPSG:4326", description="Coordinate Reference System")
    bounds: Tuple[float, float, float, float] = Field(
        ...,
        description="Bounding box coordinates in (min_lon, min_lat, max_lon, max_lat)"
    )
    resolution: Tuple[float, float] = Field(
        ...,
        description="(pixel_width_deg, pixel_height_deg) or metric pixel size"
    )
    dimensions: Tuple[int, int] = Field(
        ...,
        description="(height_rows, width_cols)"
    )
    value_range: Tuple[float, float] = Field(
        default=(0.0, 1.0),
        description="Min and max normalized hazard value [0.0, 1.0]"
    )
    nodata_value: Optional[float] = Field(default=None, description="NoData marker value")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Component 1 model provenance")


class C1HazardVectorContract(BaseModel):
    """Schema for polygon/vector hazard zones from Component 1."""
    hazard_type: str = Field(..., description="Hazard type (e.g. 'flood', 'landslide')")
    crs: str = Field(default="EPSG:4326", description="Coordinate Reference System")
    feature_count: int = Field(ge=0, description="Total number of hazard polygons")
    severity_range: Tuple[float, float] = Field(
        default=(0.0, 1.0),
        description="Min and max severity score"
    )
    metadata: Dict[str, Any] = Field(default_factory=dict)


class C2GraphContract(BaseModel):
    """Schema validating the Road Network Graph from Component 2."""
    format: str = Field(default="graphml", description="Graph storage format (graphml, json, etc.)")
    crs: str = Field(default="EPSG:4326", description="Coordinate reference system of node coordinates")
    node_count: int = Field(ge=1, description="Number of vertices in the network")
    edge_count: int = Field(ge=1, description="Number of directed edges in the network")
    is_directed: bool = Field(default=True, description="Whether the graph is directed")
    has_parallel_edges: bool = Field(default=False, description="Whether multi-edges are retained")
    required_node_attributes: List[str] = Field(
        default_factory=lambda: ["x", "y"]
    )
    required_edge_attributes: List[str] = Field(
        default_factory=lambda: ["length", "highway"]
    )
    metadata: Dict[str, Any] = Field(default_factory=dict)


class C3ODPairContract(BaseModel):
    """Origin-Destination query pair."""
    origin_id: Union[str, int] = Field(..., description="Source node ID in the graph")
    destination_id: Union[str, int] = Field(..., description="Target node ID in the graph")
    origin_coord: Optional[Tuple[float, float]] = Field(
        default=None,
        description="(longitude, latitude) of origin"
    )
    destination_coord: Optional[Tuple[float, float]] = Field(
        default=None,
        description="(longitude, latitude) of destination"
    )


class C3RouteRequestContract(BaseModel):
    """Request payload for route optimization."""
    od_pair: C3ODPairContract
    risk_formula: RiskFormulaType = Field(default=RiskFormulaType.LINEAR)
    hazard_threshold: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="Hazard score threshold above which edges are penalized or avoided"
    )
    tradeoff_alpha: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Weight factor: 1.0 = shortest time only, 0.0 = minimal risk only"
    )
    max_detour_ratio: Optional[float] = Field(
        default=1.5,
        ge=1.0,
        description="Maximum acceptable travel time multiplier relative to fastest route"
    )


class C3RouteSegmentContract(BaseModel):
    """Properties of a single directed road edge in a route."""
    u: Union[str, int] = Field(..., description="Tail node ID")
    v: Union[str, int] = Field(..., description="Head node ID")
    length_m: float = Field(ge=0.0, description="Segment length in meters")
    travel_time_sec: float = Field(ge=0.0, description="Estimated travel time in seconds")
    hazard_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Intersecting hazard index normalized to [0.0, 1.0]"
    )
    highway: Optional[str] = Field(default=None, description="Road functional classification")


class C3EvaluationMetricsContract(BaseModel):
    """Comprehensive performance evaluation metrics for a route."""
    total_travel_time_sec: float = Field(ge=0.0, description="Total route travel time in seconds")
    total_distance_m: float = Field(ge=0.0, description="Total route distance in meters")
    cumulative_hazard_exposure: float = Field(
        ge=0.0,
        description="Integrated hazard exposure (length * hazard or time * hazard)"
    )
    max_edge_hazard: float = Field(
        ge=0.0,
        le=1.0,
        description="Peak hazard intensity encountered along any single edge"
    )
    threshold_penalty: float = Field(
        ge=0.0,
        description="Penalty value accrued for traversing segments exceeding hazard threshold"
    )
    edge_count: int = Field(ge=0, description="Number of edges in path")
    is_pareto_optimal: Optional[bool] = Field(
        default=None,
        description="True if route is non-dominated in the Pareto evaluation set"
    )


class C3RouteResultContract(BaseModel):
    """Optimized route result returned by Component 3."""
    route_id: str = Field(..., description="Unique route identifier")
    algorithm: str = Field(
        ...,
        description="Algorithm used (e.g. 'shortest_time', 'risk_min', 'weighted_sum', 'pareto_knee')"
    )
    node_path: List[Union[str, int]] = Field(..., description="Ordered list of node IDs")
    metrics: C3EvaluationMetricsContract
    segments: Optional[List[C3RouteSegmentContract]] = Field(
        default=None,
        description="Detailed edge breakdown"
    )
    audit_notes: Optional[str] = Field(
        default=None,
        description="Human-readable explanation of recommendation decision and trade-offs"
    )
