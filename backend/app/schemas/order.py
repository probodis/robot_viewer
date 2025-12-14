from datetime import datetime
from pydantic import BaseModel


class Order(BaseModel):
    """Order schema for representing order data.

    Attributes:
        uid (float): Unique identifier for the order.
        start_time (datetime): Start time of the order.
        end_time (datetime): End time of the order.
    """
    uid: float
    start_time: datetime
    end_time: datetime

    class Config:
        frozen = True  # Make the model immutable to prevent accidental changes
