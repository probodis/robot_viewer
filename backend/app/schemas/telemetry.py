# backend/app/schemas/telemetry.py (НОВАЯ, ПРАВИЛЬНАЯ ВЕРСИЯ)

"""
Pydantic models for telemetry data.
"""
from typing import List, Dict, Generic, TypeVar, Optional
from pydantic import BaseModel, Field

T = TypeVar('T')

class TimeSeriesData(BaseModel, Generic[T]):
    """Represents a time series for a single metric."""
    time: List[float] = Field(..., description="List of timestamps as floats (offsets in seconds).")
    value: List[T] = Field(..., description="List of metric values.")

class MotorNodeData(BaseModel):
    """
    Represents telemetry data for a single node (e.g., 'truck', 'screen').
    A node is defined by having velocity, position, and state.
    It can optionally have other sensors, like a weight sensor.
    """
    velocity: TimeSeriesData[float]
    position: TimeSeriesData[float]
    state: TimeSeriesData[str]
    # The weight sensor is attached to a motor (e.g., 'screen') and is optional.
    weight: Optional[TimeSeriesData[float]] = None

class OrderTelemetry(BaseModel):
    """The main model that aggregates all telemetry data for a single order."""
    order_id: str = Field(..., description="The unique UTC marker for the order.")
    start_time: float = Field(..., description="Absolute UTC start time of the order.")
    end_time: float = Field(..., description="Absolute UTC end time of the order.")
    video_filename: str = Field(..., description="The filename of the corresponding video.")
    # All robot components are now stored in a single dictionary.
    motors: Dict[str, MotorNodeData]