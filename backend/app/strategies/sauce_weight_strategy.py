import logging
import json
from pathlib import Path
from datetime import datetime, timezone
from typing import List

from app.schemas.telemetry import ExtraWeightPoint
from app.schemas.order import Order
from app.utils import open_text

logger = logging.getLogger("robot_viewer")


class SauceWeightStrategy:

    def fetch_points(
        self,
        order: Order,
        path: Path,
        name_prefix: str = "Sauce",
    ) -> List[ExtraWeightPoint]:

        points: List[ExtraWeightPoint] = list()

        date = path.name.split("_")[0]  # 2025-11-25

        with open_text(path) as f:
            for line in f:
                # single quotes → valid JSON
                try:
                    record = json.loads(line.replace("'", '"'))
                except json.JSONDecodeError:
                    logger.warning(f"JSON decode error for line: {line.strip()}")
                    continue

                record_time = datetime.strptime(
                    f"{date} {record['time']}",
                    "%Y-%m-%d %H:%M:%S"
                ).replace(tzinfo=timezone.utc)

                # ATTENTION! record_time is ts of last point
                if not (order.start_time <= record_time <= order.end_time):
                    continue

                max_dt = max(record["weight_point_time"])
                process_start_ts = record_time.timestamp() - max_dt

                for idx, (dt, value) in enumerate(
                    zip(record["weight_point_time"], record["weight_point"]),
                    start=1,
                ):
                    point_time = process_start_ts + dt
                    t = point_time - order.start_time.timestamp()

                    points.append(
                        ExtraWeightPoint(
                            name=f"{name_prefix} {idx}",
                            time=round(t, 3),
                            value=value,
                        )
                    )

                return points

        return points
