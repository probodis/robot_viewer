"""HTTP contract and worker resource ownership for the no-discovery order path."""
import asyncio
import importlib
import io
import gzip
import base64
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import httpx
from fastapi.testclient import TestClient
from pydantic import SecretStr

from adapters.order_assets import S3OrderAssets
from adapters.postgrest import PostgrestOrdersRepository
from app.services.errors import InvalidUpstreamResponse, UpstreamUnavailable
from app.services.order_viewer import OrderViewerService
from fixtures import MACHINE_ID, ORDER_ID, SIGNED_VIDEO_URL, TELEMETRY_KEY, VIDEO_KEY, telemetry_data


class OrderHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.log_dir = TemporaryDirectory()
        import app.constants
        cls.constants_patch = patch.object(app.constants, "LOGS_DIR", Path(cls.log_dir.name))
        cls.constants_patch.start()
        cls.main = importlib.import_module("app.main")

    @classmethod
    def tearDownClass(cls):
        cls.main.handler.close()
        cls.main.logger.removeHandler(cls.main.handler)
        cls.constants_patch.stop()
        cls.log_dir.cleanup()

    def tearDown(self):
        self.main.app.dependency_overrides.clear()

    def test_real_order_pipeline_does_not_list_files_or_probe_s3(self):
        requests = []
        def handle(request):
            requests.append(request)
            return httpx.Response(200, json=[dict(
                machine_id=MACHINE_ID, uid=ORDER_ID, telemetry_storage_file=TELEMETRY_KEY,
                video_storage_file=VIDEO_KEY, total_time=16.82596731185913)])
        with httpx.Client(base_url="https://example.invalid/data-api/", transport=httpx.MockTransport(handle)) as metadata:
            raw_s3 = Mock(spec=["get_object", "head_object", "list_objects_v2"])
            raw_s3.get_object.return_value = {
                "Body": io.BytesIO(json.dumps(telemetry_data()).encode()), "ContentLength": 500}
            storage = SimpleNamespace(client=raw_s3, bucket_name="bucket", get_presigned_url=Mock(return_value=SIGNED_VIDEO_URL))
            service = OrderViewerService(PostgrestOrdersRepository(metadata), S3OrderAssets(storage))
            self.main.app.dependency_overrides[self.main.get_order_service] = lambda: service
            client = TestClient(self.main.app)
            self.addCleanup(client.close)
            with patch.object(Path, "iterdir", side_effect=AssertionError("No filesystem discovery is allowed")):
                response = client.get("/api/v1/orders/", params={"machine_id": MACHINE_ID, "order_id": ORDER_ID})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["telemetry_status"], "available")
            self.assertEqual(response.json()["video_path"], SIGNED_VIDEO_URL)
            self.assertEqual(len(requests), 1)
            raw_s3.get_object.assert_called_once()
            raw_s3.head_object.assert_not_called()
            raw_s3.list_objects_v2.assert_not_called()

    def test_http_statuses_distinguish_invalid_input_missing_order_and_dependencies(self):
        service = Mock(spec=["get_order"])
        self.main.app.dependency_overrides[self.main.get_order_service] = lambda: service
        client = TestClient(self.main.app)
        self.addCleanup(client.close)
        for failure, status in [(None, 404), (ValueError("secret detail"), 422),
                                (UpstreamUnavailable("secret detail"), 503),
                                (InvalidUpstreamResponse("secret detail"), 502)]:
            with self.subTest(status=status):
                service.get_order.side_effect = failure
                service.get_order.return_value = None
                response = client.get("/api/v1/orders/", params={"machine_id": MACHINE_ID, "order_id": ORDER_ID})
                self.assertEqual(response.status_code, status)
                self.assertNotIn("secret detail", response.text)

    def test_full_log_rejects_traversal_and_binary_paths(self):
        client = TestClient(self.main.app)
        self.addCleanup(client.close)
        for machine, key in [("../outside", "machine/a.txt"), (MACHINE_ID, "../secret.txt"),
                             (MACHINE_ID, "machine/%2e%2e.txt"), (MACHINE_ID, "videos/a.mp4")]:
            with self.subTest(machine=machine, key=key):
                response = client.get("/api/v1/log", params={"machine_id": machine, "log_key": key})
                self.assertEqual(response.status_code, 422)

    def test_full_log_reads_one_file_and_returns_no_absolute_server_path(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            key = "machine/2026-11-06_machine.txt.gz"
            path = root / MACHINE_ID / "logs" / key
            path.parent.mkdir(parents=True)
            path.write_bytes(gzip.compress(b"example log\n"))
            client = TestClient(self.main.app)
            self.addCleanup(client.close)
            with patch.object(self.main, "DATA_DIR", root):
                response = client.get("/api/v1/log", params={"machine_id": MACHINE_ID, "log_key": key})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["path"], key)
            self.assertEqual(base64.b64decode(response.json()["text"]), b"example log\n")
            self.assertNotIn(directory, response.text)

    def test_full_log_size_and_corrupt_archive_errors_are_explicit(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            key = "machine/2026-11-06_machine.txt.gz"
            path = root / MACHINE_ID / "logs" / key
            path.parent.mkdir(parents=True)
            client = TestClient(self.main.app)
            self.addCleanup(client.close)
            with patch.object(self.main, "DATA_DIR", root), patch.object(self.main, "MAX_INPUT_BYTES", 32):
                path.write_bytes(gzip.compress(b"x" * 1000))
                response = client.get("/api/v1/log", params={"machine_id": MACHINE_ID, "log_key": key})
                self.assertEqual(response.status_code, 413)
                path.write_bytes(gzip.compress(b"small")[:-5])
                response = client.get("/api/v1/log", params={"machine_id": MACHINE_ID, "log_key": key})
                self.assertEqual(response.status_code, 503)
                path.unlink()
                response = client.get("/api/v1/log", params={"machine_id": MACHINE_ID, "log_key": key})
                self.assertEqual(response.status_code, 404)

    def test_worker_lifespan_closes_both_connection_pools(self):
        config = SimpleNamespace(
            POSTGREST_BASE_URL="https://example.invalid/data-api/", POSTGREST_API_KEY=SecretStr("test-key"),
            POSTGREST_TIMEOUT_SECONDS=5.0, POSTGREST_CONNECT_TIMEOUT_SECONDS=2.0,
            TELEMETRY_MAX_BYTES=1024 * 1024)
        metadata = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[])))
        storage = SimpleNamespace(client=Mock(spec=["close"]))
        async def enter_worker():
            with patch.object(self.main, "get_config", return_value=config), \
                 patch.object(self.main.httpx, "Client", return_value=metadata), \
                 patch.object(self.main, "get_s3_client", return_value=storage):
                async with self.main.lifespan(self.main.app):
                    self.assertFalse(metadata.is_closed)
                    self.assertIsInstance(self.main.app.state.order_service, OrderViewerService)
        asyncio.run(enter_worker())
        self.assertTrue(metadata.is_closed)
        storage.client.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
