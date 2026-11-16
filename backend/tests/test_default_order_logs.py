"""Legacy automatic tabs are selected and read under one request budget."""

import base64
import gzip
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from app.services.default_order_logs import (
    AutomaticOrderLogsService,
    DefaultLogLimits,
    SelectionBudget,
    select_default_logs,
)
from app.services.order_logs import DiagnosticWindowUnavailable
from fixtures import MACHINE_ID, ORDER_ID, order_record


class AutomaticOrderLogsTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.repository = Mock(spec=["get_order"])
        self.repository.get_order.return_value = order_record()

    def write(self, key, content="2026-11-07 00:24:38 selected\n"):
        path = self.root / MACHINE_ID / "logs" / key
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = content.encode()
        path.write_bytes(gzip.compress(raw) if path.suffix == ".gz" else raw)
        return path

    def select(self, limits=None):
        limits = limits or DefaultLogLimits()
        return select_default_logs(
            self.root,
            MACHINE_ID,
            ORDER_ID,
            SelectionBudget(limits, time.perf_counter() + 20),
        )

    def test_legacy_selection_prefers_latest_date_and_gzip_without_binary_folder_walks(
        self,
    ):
        for key in [
            "orders/2026-11-06_orders.txt.gz",
            "orders/2026-11-07_orders.txt",
            "orders/2026-11-07_orders.txt.gz",
            "orders/2026-11-08_orders.txt.gz",
            "orders/2026-11-07_orders_261107123456.txt.gz",
            "orders/2026-99-07_orders.txt.gz",
            "subapps/2026-11-06_recorder.txt.gz",
            "videos/2026-11-07_video.txt",
            "scanner/2026-11-07_scanner.txt",
            "start_order_converted/2026-11-07_start_order.txt",
        ]:
            self.write(key)
        import os

        original = os.scandir
        scanned = []

        def scan(path):
            scanned.append(Path(path).name)
            return original(path)

        with patch("app.services.default_order_logs.os.scandir", side_effect=scan):
            selected = self.select()
        self.assertEqual(
            selected.keys,
            ("orders/2026-11-07_orders.txt.gz", "subapps/2026-11-06_recorder.txt.gz"),
        )
        self.assertFalse(selected.truncated)
        self.assertTrue(
            {"videos", "scanner", "start_order_converted"}.isdisjoint(scanned)
        )

    def test_auto_response_restores_tabs_and_isolates_corrupt_files(self):
        self.write(
            "orders/2026-11-07_orders.txt.gz",
            "2026-11-07 00:24:38 expected order record\n",
        )
        corrupt = self.write("console/2026-11-07_console.txt.gz")
        corrupt.write_bytes(b"not a gzip stream")
        result = AutomaticOrderLogsService(self.repository, self.root).get_logs(
            MACHINE_ID, ORDER_ID
        )
        self.assertEqual(result["status"], "partial")
        self.assertFalse(
            result["truncated"],
            "A corrupt source is partial, not a read-limit truncation",
        )
        self.assertEqual(
            result["logs"]["console/2026-11-07_console.txt.gz"]["status"], "error"
        )
        entry = result["logs"]["orders/2026-11-07_orders.txt.gz"]
        self.assertEqual(entry["path"], "orders/2026-11-07_orders.txt.gz")
        self.assertIn("expected order record", base64.b64decode(entry["text"]).decode())
        self.assertNotIn(str(self.root), str(result))
        self.repository.get_order.assert_called_once_with(MACHINE_ID, ORDER_ID)

    def test_total_read_budget_leaves_explicit_unloaded_tabs(self):
        self.write("a/2026-11-07_a.txt", "x" * 1000)
        self.write("b/2026-11-07_b.txt")
        limits = DefaultLogLimits(max_input_bytes=64)
        result = AutomaticOrderLogsService(self.repository, self.root, limits).get_logs(
            MACHINE_ID, ORDER_ID
        )
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["truncated"])
        self.assertTrue(result["logs"]["a/2026-11-07_a.txt"]["truncated"])
        self.assertEqual(result["logs"]["b/2026-11-07_b.txt"]["status"], "not_loaded")
        self.assertEqual(result["loaded_files"], 1)

    def test_selection_entry_file_and_deadline_budgets_are_explicit(self):
        for index in range(4):
            self.write(f"orders/2026-11-07_source{index}.txt")
        capped = self.select(DefaultLogLimits(max_files=2))
        self.assertEqual(len(capped.keys), 2)
        self.assertTrue(capped.truncated)
        capped = self.select(DefaultLogLimits(max_entries=1))
        self.assertTrue(capped.truncated)
        self.assertEqual(capped.keys, ())
        limits = DefaultLogLimits()
        expired = select_default_logs(
            self.root,
            MACHINE_ID,
            ORDER_ID,
            SelectionBudget(limits, time.perf_counter() - 1),
        )
        self.assertTrue(expired.truncated)
        self.assertEqual(expired.keys, ())

    def test_time_only_logs_get_placeholders_instead_of_guessed_dates(self):
        self.write(
            "sauce_weight/2026-11-07_sauce_weight.txt",
            "{'time':'00:24:38','weight_point':[1,2]}\n",
        )
        result = AutomaticOrderLogsService(self.repository, self.root).get_logs(
            MACHINE_ID, ORDER_ID
        )
        entry = result["logs"]["sauce_weight/2026-11-07_sauce_weight.txt"]
        self.assertEqual(entry["status"], "no_timestamp")
        self.assertIn("Double-click", base64.b64decode(entry["text"]).decode())

    def test_automatic_windows_stop_at_first_later_timestamp_under_legacy_behavior(
        self,
    ):
        key = "machine/2026-11-07_machine.txt"
        self.write(
            key,
            "2026-11-07 00:24:38 selected-first\n"
            "2026-11-07 00:25:10 past-window\n"
            "2026-11-07 00:24:40 clock-restart-matching\n"
            "2026-11-07 00:26:10 " + "x" * 1000 + "\n",
        )
        limits = DefaultLogLimits(max_input_bytes=128)
        result = AutomaticOrderLogsService(self.repository, self.root, limits).get_logs(
            MACHINE_ID, ORDER_ID
        )
        text = base64.b64decode(result["logs"][key]["text"]).decode()
        self.assertIn("selected-first", text)
        self.assertNotIn("clock-restart-matching", text)
        self.assertFalse(
            result["truncated"],
            "The reader should stop before the irrelevant tail consumes its budget",
        )
        self.assertEqual(result["loaded_files"], 1)

    def test_missing_order_or_duration_never_discovers_files(self):
        service = AutomaticOrderLogsService(self.repository, self.root)
        with patch(
            "app.services.default_order_logs.select_default_logs",
            side_effect=AssertionError("Unexpected file discovery"),
        ):
            self.repository.get_order.return_value = None
            self.assertIsNone(service.get_logs(MACHINE_ID, ORDER_ID))
            self.repository.get_order.return_value = order_record(total_time=None)
            with self.assertRaises(DiagnosticWindowUnavailable):
                service.get_logs(MACHINE_ID, ORDER_ID)

    def test_invalid_identity_is_rejected_before_metadata_or_files(self):
        service = AutomaticOrderLogsService(self.repository, self.root)
        with self.assertRaises(ValueError):
            service.get_logs("../outside", ORDER_ID)
        self.repository.get_order.assert_not_called()


if __name__ == "__main__":
    unittest.main()
