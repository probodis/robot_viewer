"""User-selected log windows use content timestamps and hard read budgets."""
import base64
import gzip
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from app.services.order_logs import DiagnosticWindowUnavailable, OrderLogWindowService
from app.services.errors import UpstreamUnavailable
from fixtures import MACHINE_ID, ORDER_ID, order_record


class OrderLogWindowTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.repository = Mock(spec=["get_order"])
        self.repository.get_order.return_value = order_record()
        self.service = OrderLogWindowService(self.repository, self.root)

    def write_file(self, log_key, content, compressed=False):
        path = self.root / MACHINE_ID / "logs" / log_key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(gzip.compress(content.encode()) if compressed else content.encode())
        return path

    def test_previous_date_filename_uses_actual_utc_content_with_no_directory_scan(self):
        key = "machine/2026-11-06_machine.txt.gz"
        self.write_file(key, "2026-11-07 00:24:10 old\n2026-11-07 00:24:38 order-start\ncontinued detail\n2026-11-07 00:25:10 later\n", compressed=True)
        with patch.object(Path, "iterdir", side_effect=AssertionError("No directory discovery")):
            result = self.service.get_window(MACHINE_ID, ORDER_ID, key)
        text = base64.b64decode(result["text"]).decode()
        self.assertEqual(result["status"], "available")
        self.assertIn("order-start", text)
        self.assertIn("continued detail", text)
        self.assertNotIn("old", text)
        self.assertNotIn("later", text)
        self.assertFalse(result["truncated"])

    def test_oversize_single_line_stops_at_read_budget(self):
        key = "machine/2026-11-06_machine.txt"
        self.write_file(key, "x" * 10000)
        with patch("app.services.order_logs.MAX_INPUT_BYTES", 128):
            result = self.service.get_window(MACHINE_ID, ORDER_ID, key)
        self.assertTrue(result["truncated"])
        self.assertEqual(base64.b64decode(result["text"]), b"")

    def test_output_budget_is_reported_instead_of_truncating_silently(self):
        key = "console/2026-11-06_console.txt"
        self.write_file(key, "2026-11-07 00:24:38 " + "x" * 100)
        with patch("app.services.order_logs.MAX_OUTPUT_BYTES", 32):
            result = self.service.get_window(MACHINE_ID, ORDER_ID, key)
        self.assertTrue(result["truncated"])
        self.assertEqual(base64.b64decode(result["text"]), b"")

    def test_time_only_sauce_record_is_not_assigned_a_guessed_date(self):
        key = "sauce_weight/2026-11-06_sauce_weight.txt"
        self.write_file(key, "{'time':'00:24:38','weight_point':[1,2]}\n")
        result = self.service.get_window(MACHINE_ID, ORDER_ID, key)
        self.assertEqual(result["status"], "no_timestamp")
        self.assertEqual(result["extra_weight_points_status"], "not_available")

    def test_unbounded_order_duration_is_rejected(self):
        for duration in [None, 3601]:
            with self.subTest(duration=duration):
                self.repository.get_order.return_value = order_record(total_time=duration)
                with self.assertRaises(DiagnosticWindowUnavailable):
                    self.service.get_window(MACHINE_ID, ORDER_ID, "machine/2026-11-06_machine.txt")

    def test_paths_outside_selected_machine_are_rejected(self):
        for key in ["../../outside.txt", "/outside.txt", "machine/%2e%2e/outside.txt", "videos/a.mp4"]:
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    self.service.get_window(MACHINE_ID, ORDER_ID, key)
        self.repository.get_order.assert_not_called()

    def test_truncated_gzip_is_a_dependency_error(self):
        key = "machine/2026-11-06_machine.txt.gz"
        path = self.write_file(key, "2026-11-07 00:24:38 order-start\n", compressed=True)
        path.write_bytes(path.read_bytes()[:-5])
        with self.assertRaises(UpstreamUnavailable):
            self.service.get_window(MACHINE_ID, ORDER_ID, key)


if __name__ == "__main__":
    unittest.main()
