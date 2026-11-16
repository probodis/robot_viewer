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
from concurrent.futures import ThreadPoolExecutor
from threading import BoundedSemaphore, Event
import unittest
from unittest.mock import Mock, patch

import httpx
from fastapi.testclient import TestClient
from pydantic import SecretStr

from adapters.order_assets import S3OrderAssets
from adapters.postgrest import PostgrestOrdersRepository
from app.services.errors import InvalidUpstreamResponse, UpstreamUnavailable
from app.services.order_viewer import OrderViewerService
from fixtures import (
    MACHINE_ID,
    ORDER_ID,
    SIGNED_VIDEO_URL,
    TELEMETRY_KEY,
    VIDEO_KEY,
    telemetry_data,
    service_dependencies,
)


class OrderHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.log_dir = TemporaryDirectory()
        import app.constants

        cls.constants_patch = patch.object(
            app.constants, "LOGS_DIR", Path(cls.log_dir.name)
        )
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
            return httpx.Response(
                200,
                json=[
                    dict(
                        machine_id=MACHINE_ID,
                        uid=ORDER_ID,
                        telemetry_storage_file=TELEMETRY_KEY,
                        video_storage_file=VIDEO_KEY,
                        total_time=16.82596731185913,
                    )
                ],
            )

        with httpx.Client(
            base_url="https://example.invalid/data-api/",
            transport=httpx.MockTransport(handle),
        ) as metadata:
            raw_s3 = Mock(spec=["get_object", "head_object", "list_objects_v2"])
            raw_s3.get_object.return_value = {
                "Body": io.BytesIO(json.dumps(telemetry_data()).encode()),
                "ContentLength": 500,
            }
            storage = SimpleNamespace(
                client=raw_s3,
                bucket_name="bucket",
                get_presigned_url=Mock(return_value=SIGNED_VIDEO_URL),
            )
            service = OrderViewerService(
                PostgrestOrdersRepository(metadata), S3OrderAssets(storage)
            )
            self.main.app.dependency_overrides[self.main.get_order_service] = (
                lambda: service
            )
            client = TestClient(self.main.app)
            self.addCleanup(client.close)
            with patch.object(
                Path,
                "iterdir",
                side_effect=AssertionError("No filesystem discovery is allowed"),
            ):
                response = client.get(
                    "/api/v1/orders/",
                    params={"machine_id": MACHINE_ID, "order_id": ORDER_ID},
                )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["telemetry_status"], "available")
            self.assertEqual(response.json()["video_path"], SIGNED_VIDEO_URL)
            self.assertEqual(len(requests), 1)
            raw_s3.get_object.assert_called_once()
            raw_s3.head_object.assert_not_called()
            raw_s3.list_objects_v2.assert_not_called()

    def test_http_statuses_distinguish_invalid_input_missing_order_and_dependencies(
        self,
    ):
        service = Mock(spec=["get_order"])
        self.main.app.dependency_overrides[self.main.get_order_service] = (
            lambda: service
        )
        client = TestClient(self.main.app)
        self.addCleanup(client.close)
        for failure, status in [
            (None, 404),
            (ValueError("secret detail"), 422),
            (UpstreamUnavailable("secret detail"), 503),
            (InvalidUpstreamResponse("secret detail"), 502),
        ]:
            with self.subTest(status=status):
                service.get_order.side_effect = failure
                service.get_order.return_value = None
                response = client.get(
                    "/api/v1/orders/",
                    params={"machine_id": MACHINE_ID, "order_id": ORDER_ID},
                )
                self.assertEqual(response.status_code, status)
                self.assertNotIn("secret detail", response.text)

    def test_full_log_rejects_traversal_and_binary_paths(self):
        client = TestClient(self.main.app)
        self.addCleanup(client.close)
        for machine, key in [
            ("../outside", "machine/a.txt"),
            (MACHINE_ID, "../secret.txt"),
            (MACHINE_ID, "machine/%2e%2e.txt"),
            (MACHINE_ID, "videos/a.mp4"),
        ]:
            with self.subTest(machine=machine, key=key):
                response = client.get(
                    "/api/v1/log", params={"machine_id": machine, "log_key": key}
                )
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
                response = client.get(
                    "/api/v1/log", params={"machine_id": MACHINE_ID, "log_key": key}
                )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["path"], key)
            self.assertEqual(
                base64.b64decode(response.json()["text"]), b"example log\n"
            )
            self.assertNotIn(directory, response.text)

    def test_full_log_size_and_corrupt_archive_errors_are_explicit(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            key = "machine/2026-11-06_machine.txt.gz"
            path = root / MACHINE_ID / "logs" / key
            path.parent.mkdir(parents=True)
            client = TestClient(self.main.app)
            self.addCleanup(client.close)
            with patch.object(self.main, "DATA_DIR", root), patch.object(
                self.main, "MAX_INPUT_BYTES", 32
            ):
                path.write_bytes(gzip.compress(b"x" * 1000))
                response = client.get(
                    "/api/v1/log", params={"machine_id": MACHINE_ID, "log_key": key}
                )
                self.assertEqual(response.status_code, 413)
                path.write_bytes(gzip.compress(b"small")[:-5])
                response = client.get(
                    "/api/v1/log", params={"machine_id": MACHINE_ID, "log_key": key}
                )
                self.assertEqual(response.status_code, 503)
                path.unlink()
                response = client.get(
                    "/api/v1/log", params={"machine_id": MACHINE_ID, "log_key": key}
                )
                self.assertEqual(response.status_code, 404)

    def test_slow_automatic_logs_do_not_block_media_and_excess_logs_are_rejected(self):
        entered = Event()
        release = Event()
        diagnostics = Mock(spec=["get_logs"])

        def slow_logs(*_args):
            entered.set()
            if not release.wait(timeout=5):
                raise AssertionError("Test did not release diagnostics")
            return {
                "logs": {},
                "status": "empty",
                "truncated": False,
                "selected_files": 0,
                "loaded_files": 0,
            }

        diagnostics.get_logs.side_effect = slow_logs
        repository, assets = service_dependencies()
        order_service = OrderViewerService(repository, assets)
        self.main.app.dependency_overrides[self.main.get_order_service] = (
            lambda: order_service
        )
        self.main.app.dependency_overrides[self.main.get_default_order_log_service] = (
            lambda: diagnostics
        )
        self.main.app.state.diagnostics_gate = BoundedSemaphore(1)
        client = TestClient(self.main.app)
        self.addCleanup(client.close)
        params = {"machine_id": MACHINE_ID, "order_id": ORDER_ID}
        with ThreadPoolExecutor(max_workers=3) as workers:
            pending = workers.submit(client.get, "/api/v1/orders/logs", params=params)
            try:
                self.assertTrue(
                    entered.wait(timeout=2), "Diagnostics request did not start"
                )
                media = workers.submit(
                    client.get, "/api/v1/orders/", params=params
                ).result(timeout=1)
                self.assertEqual(media.status_code, 200)
                self.assertEqual(media.json()["video_path"], SIGNED_VIDEO_URL)
                self.assertEqual(media.json()["telemetry_status"], "available")
                self.assertFalse(
                    pending.done(), "Logs must still be pending when media completes"
                )
                rejected = workers.submit(
                    client.get, "/api/v1/orders/logs", params=params
                ).result(timeout=1)
                self.assertEqual(rejected.status_code, 503)
                self.assertEqual(rejected.headers["Retry-After"], "2")
                self.assertEqual(diagnostics.get_logs.call_count, 1)
            finally:
                release.set()
            self.assertEqual(pending.result(timeout=2).status_code, 200)
        # Reservation is released once the earlier request finishes.
        self.assertEqual(
            client.get("/api/v1/orders/logs", params=params).status_code, 200
        )

    def test_automatic_log_errors_are_isolated_from_order_and_media_response(self):
        diagnostics = Mock(spec=["get_logs"])
        repository, assets = service_dependencies()
        self.main.app.dependency_overrides[self.main.get_order_service] = (
            lambda: OrderViewerService(repository, assets)
        )
        self.main.app.dependency_overrides[self.main.get_default_order_log_service] = (
            lambda: diagnostics
        )
        self.main.app.state.diagnostics_gate = BoundedSemaphore(1)
        client = TestClient(self.main.app)
        self.addCleanup(client.close)
        params = {"machine_id": MACHINE_ID, "order_id": ORDER_ID}
        from app.services.order_logs import DiagnosticWindowUnavailable

        for failure, status in [
            (OSError("private path"), 503),
            (UpstreamUnavailable("private detail"), 503),
            (InvalidUpstreamResponse("private credentials"), 502),
            (DiagnosticWindowUnavailable("private payload"), 409),
            (ValueError("private identity"), 422),
            (None, 404),
        ]:
            with self.subTest(status=status):
                diagnostics.get_logs.side_effect = failure
                diagnostics.get_logs.return_value = None
                response = client.get("/api/v1/orders/logs", params=params)
                self.assertEqual(response.status_code, status)
                self.assertNotIn("private", response.text)
                media = client.get("/api/v1/orders/", params=params)
                self.assertEqual(media.status_code, 200)
                self.assertEqual(media.json()["video_path"], SIGNED_VIDEO_URL)

    def test_worker_lifespan_owns_separate_metadata_pools_and_closes_all_resources(
        self,
    ):
        config = SimpleNamespace(
            POSTGREST_BASE_URL="https://example.invalid/data-api/",
            POSTGREST_API_KEY=SecretStr("test-key"),
            POSTGREST_TIMEOUT_SECONDS=5.0,
            POSTGREST_CONNECT_TIMEOUT_SECONDS=2.0,
            TELEMETRY_MAX_BYTES=1024 * 1024,
        )
        metadata = httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[]))
        )
        diagnostics = httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[]))
        )
        storage = SimpleNamespace(client=Mock(spec=["close"]))

        async def enter_worker():
            with patch.object(
                self.main, "get_config", return_value=config
            ), patch.object(
                self.main.httpx, "Client", side_effect=[metadata, diagnostics]
            ), patch.object(
                self.main, "get_s3_client", return_value=storage
            ):
                async with self.main.lifespan(self.main.app):
                    self.assertFalse(metadata.is_closed)
                    self.assertFalse(diagnostics.is_closed)
                    state = self.main.app.state
                    self.assertIs(state.order_service._repository._client, metadata)
                    self.assertIs(
                        state.order_log_service._repository._client, diagnostics
                    )
                    self.assertIs(
                        state.default_order_log_service._repository._client, diagnostics
                    )
                    self.assertTrue(state.diagnostics_gate.acquire(blocking=False))
                    self.assertFalse(state.diagnostics_gate.acquire(blocking=False))
                    state.diagnostics_gate.release()

        asyncio.run(enter_worker())
        self.assertTrue(metadata.is_closed)
        self.assertTrue(diagnostics.is_closed)
        storage.client.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
