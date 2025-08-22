import ast
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List

import sys

sys.path.append(str(Path(__file__).parent.parent))
from app.schemas.telemetry import OrderTelemetry

BASE_DIR = Path(__file__).parent.parent
RAW_DATA_DIR = BASE_DIR / "data"
VIDEO_DIR = BASE_DIR / "videos"
PROCESSED_DATA_DIR = BASE_DIR / "processed_data"
ORDERS_LOG_FILE = RAW_DATA_DIR / "orders_logs.txt"
TELEMETRY_LOG_FILE = RAW_DATA_DIR / "telemetry_logs.txt"


def find_order_ids() -> Dict[str, str]:
    order_ids = {}
    order_pattern = re.compile(
        r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*?\"uid\": (\d+\.\d+).*?\"action\": \"new_order\"")
    with open(ORDERS_LOG_FILE, "r") as f:
        for line in f:
            match = order_pattern.search(line)
            if match:
                uid = match.group(2)
                if uid not in order_ids:
                    order_ids[uid] = match.group(1)
    print(f"Found {len(order_ids)} unique orders.")
    return order_ids


def find_video_for_order(timestamp_str: str) -> str:
    dt_obj = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
    video_filename_stem = dt_obj.strftime("%Y-%m-%d_%H-%M-%S")
    for video_file in VIDEO_DIR.iterdir():
        if video_file.stem == video_filename_stem:
            return video_file.name
    return "video_not_found.mp4"


def parse_and_transform_telemetry(raw_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Correctly parses flat telemetry data into a nested structure based on node names.
    Example key: 'screen_weight_value' -> node='screen', metric='weight', type='value'
    """
    nodes_buffer = {}
    for key, data_list in raw_data.items():
        parts = key.split('_')
        if len(parts) < 3: continue
        node_name, metric_name, data_type = parts[0], parts[1], parts[2]
        if node_name not in nodes_buffer: nodes_buffer[node_name] = {}
        if metric_name not in nodes_buffer[node_name]: nodes_buffer[node_name][metric_name] = {}
        nodes_buffer[node_name][metric_name][data_type] = data_list

    # 2. Build the final 'motors' dictionary from the buffer
    motors_data = {}
    for node_name, metrics in nodes_buffer.items():
        # A valid motor must have these three essential metrics
        if all(k in metrics for k in ("velocity", "position", "state")):
            motors_data[node_name] = {
                "velocity": metrics.get("velocity"),
                "position": metrics.get("position"),
                "state": metrics.get("state"),
                # Add weight metric if it exists for this specific node
                "weight": metrics.get("weight"),
            }

    # 3. Assemble the final object for Pydantic validation
    return {
        "motors": motors_data,
        "start_time": raw_data.get("start_time"),
        "end_time": raw_data.get("end_time"),
    }


def main():
    print("Starting ETL process...")
    order_id_map = find_order_ids()

    all_telemetry_data = []
    try:
        with open(TELEMETRY_LOG_FILE, 'r') as f:
            for i, line in enumerate(f):
                if line.strip():
                    try:
                        all_telemetry_data.append(ast.literal_eval(line))
                    except (SyntaxError, ValueError) as e:
                        print(f"Warning: Could not parse line {i + 1} in telemetry_logs.txt. Skipping. Details: {e}")
        print(f"Successfully parsed telemetry log. Found {len(all_telemetry_data)} records.")
    except FileNotFoundError:
        print(f"Error: Could not find {TELEMETRY_LOG_FILE}.")
        return

    PROCESSED_DATA_DIR.mkdir(exist_ok=True)
    processed_count = 0
    skipped_count = 0
    telemetry_map = {int(record['start_time']): record for record in all_telemetry_data if 'start_time' in record}

    for order_id, timestamp_str in order_id_map.items():
        order_start_time_int = int(float(order_id))
        raw_telemetry_record = telemetry_map.get(order_start_time_int)

        if not raw_telemetry_record:
            print(f"[SKIP] Order UID: {order_id}. Reason: No telemetry record found.")
            skipped_count += 1
            continue

        video_filename = find_video_for_order(timestamp_str)
        if video_filename == "video_not_found.mp4":
            expected_video_name = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S").strftime("%Y-%m-%d_%H-%M-%S.mp4")
            print(f"[SKIP] Order UID: {order_id}. Reason: Video not found. Expected name: '{expected_video_name}'")
            skipped_count += 1
            continue

        transformed_data = parse_and_transform_telemetry(raw_telemetry_record)
        transformed_data['order_id'] = order_id
        transformed_data['video_filename'] = video_filename

        try:
            order_data = OrderTelemetry(**transformed_data)
            output_path = PROCESSED_DATA_DIR / f"{order_id}.json"
            with open(output_path, "w") as f:
                f.write(order_data.model_dump_json(indent=2))
            processed_count += 1
        except Exception as e:
            print(f"[FAIL] Order UID: {order_id}. Reason: Validation Error. Details: {e}")
            skipped_count += 1

    print("\n--- ETL process finished ---")
    print(f"Successfully processed and saved: {processed_count} orders.")
    print(f"Skipped or failed: {skipped_count} orders.")
    print(f"Processed data is located in: {PROCESSED_DATA_DIR}")


if __name__ == "__main__":
    main()