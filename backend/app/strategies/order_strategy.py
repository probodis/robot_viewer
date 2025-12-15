import logging
import json
from datetime import datetime, timezone
from pathlib import Path

from app.schemas.order import Order
from app.utils import open_text

logger = logging.getLogger("robot_viewer")


class OrdersStrategy:

    TS_FORMAT = "%Y-%m-%d %H:%M:%S.%f"
        
    def fetch_order(self, order_id: float, path: Path) -> Order | None:
        order_data = dict()

        with open_text(path) as f:
            for line in f:
                if str(order_id) not in line:
                    continue
                if '"action": "new_order"' not in line and 'end_screen_weight' not in line:
                    continue

                ts_str, payload = line.split(None, 1)
                ts = datetime.strptime(ts_str, "%Y-%m-%d") \
                    .replace(
                        hour=int(payload[0:2]),
                        minute=int(payload[3:5]),
                        second=int(payload[6:8]),
                        tzinfo=timezone.utc
                    )
                try:
                    data = json.loads(payload[15:].strip())
                except json.JSONDecodeError:
                    logger.warning(f"JSON decode error for line: {line.strip()}")
                    continue

                if data.get("action") == "new_order":
                    order_data["uid"] = data["uid"]
                    order_data["start_time"] = ts

                if "end_screen_weight" in data:
                    order_data["end_time"] = ts
                    
                    return Order(**order_data) if order_data else None
