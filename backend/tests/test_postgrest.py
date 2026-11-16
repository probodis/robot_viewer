"""Machine-scoped PostgREST reads and sanitized dependency failures."""
import unittest

import httpx

from adapters.postgrest import ORDER_COLUMNS, PostgrestOrdersRepository
from app.services.errors import InvalidUpstreamResponse, UpstreamUnavailable
from fixtures import MACHINE_ID, ORDER_ID, TELEMETRY_KEY, VIDEO_KEY


class PostgrestRepositoryTests(unittest.TestCase):
    def make_repository(self, handler):
        client = httpx.Client(
            base_url="https://metadata.example.invalid/data-api/",
            headers={"X-API-Key": "test-secret-not-for-logs"},
            transport=httpx.MockTransport(handler),
        )
        self.addCleanup(client.close)
        return PostgrestOrdersRepository(client)

    def row(self, **overrides):
        data = dict(machine_id=MACHINE_ID, uid=ORDER_ID,
                    telemetry_storage_file=TELEMETRY_KEY, video_storage_file=VIDEO_KEY)
        data.update(overrides)
        return data

    def test_exact_machine_and_order_filters_request_only_required_columns(self):
        requests = []
        def handle(request):
            requests.append(request)
            return httpx.Response(200, json=[self.row()])
        record = self.make_repository(handle).get_order(MACHINE_ID, ORDER_ID)
        self.assertEqual(record.telemetry_storage_file, TELEMETRY_KEY)
        self.assertEqual(len(requests), 1)
        request = requests[0]
        self.assertEqual(request.url.path, "/data-api/orders")
        self.assertEqual(dict(request.url.params), {
            "machine_id": f"eq.{MACHINE_ID}", "uid": f"eq.{ORDER_ID}",
            "select": ORDER_COLUMNS, "limit": "1",
        })
        self.assertEqual(request.headers["x-api-key"], "test-secret-not-for-logs")
        self.assertNotIn("test-secret", str(request.url))

    def test_empty_result_is_the_only_normal_missing_order_case(self):
        repo = self.make_repository(lambda request: httpx.Response(200, json=[]))
        self.assertIsNone(repo.get_order(MACHINE_ID, ORDER_ID))

    def test_dependency_failures_are_not_missing_orders_or_retried(self):
        for status in [401, 403, 404, 408, 429, 500, 503]:
            with self.subTest(status=status):
                calls = []
                def handle(request):
                    calls.append(request)
                    return httpx.Response(status, text="secret response body")
                repo = self.make_repository(handle)
                expected = UpstreamUnavailable if status in (408, 429, 500, 503) else InvalidUpstreamResponse
                with self.assertRaises(expected) as caught:
                    repo.get_order(MACHINE_ID, ORDER_ID)
                self.assertNotIn("secret response", str(caught.exception))
                self.assertEqual(len(calls), 1)

    def test_timeout_is_sanitized_and_not_retried(self):
        calls = []
        def handle(request):
            calls.append(request)
            raise httpx.ReadTimeout("private upstream details", request=request)
        with self.assertLogs("robot_viewer", level="WARNING") as logs:
            with self.assertRaises(UpstreamUnavailable) as caught:
                self.make_repository(handle).get_order(MACHINE_ID, ORDER_ID)
        self.assertNotIn("private upstream", str(caught.exception))
        self.assertNotIn("test-secret-not-for-logs", " ".join(logs.output))
        self.assertEqual(len(calls), 1)

    def test_malformed_duplicate_and_wrong_order_responses_are_rejected(self):
        cases = [None, {}, [self.row(), self.row()], [self.row(uid=ORDER_ID + 1)],
                 [self.row(machine_id="cb-3-9999")], [{"machine_id": MACHINE_ID}],
                 [self.row(total_time=-1)]]
        for payload in cases:
            with self.subTest(payload=payload):
                repo = self.make_repository(lambda request: httpx.Response(200, json=payload))
                with self.assertRaises(InvalidUpstreamResponse):
                    repo.get_order(MACHINE_ID, ORDER_ID)
        repo = self.make_repository(lambda request: httpx.Response(200, text="not JSON"))
        with self.assertRaises(InvalidUpstreamResponse):
            repo.get_order(MACHINE_ID, ORDER_ID)


if __name__ == "__main__":
    unittest.main()
