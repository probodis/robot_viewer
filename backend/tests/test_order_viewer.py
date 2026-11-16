"""Cross-midnight orders use exact assets, independent of calendar filenames."""
import unittest
import json

from app.services.errors import InvalidStorageReference, InvalidTelemetry, UpstreamUnavailable
from app.services.order_viewer import OrderViewerService
from fixtures import MACHINE_ID, ORDER_ID, SIGNED_VIDEO_URL, TELEMETRY_KEY, VIDEO_KEY, order_record, service_dependencies, telemetry_data


class OrderViewerServiceTests(unittest.TestCase):
    def make_service(self, record=None, telemetry=None):
        repository, assets = service_dependencies(record, telemetry)
        return OrderViewerService(repository, assets), repository, assets

    def test_cross_midnight_preserves_milliseconds_and_exact_asset_references(self):
        service, repository, assets = self.make_service()
        result = service.get_order(MACHINE_ID, ORDER_ID)
        self.assertEqual(result.order_id, str(ORDER_ID))
        self.assertEqual(result.start_time, 1794011078.318)
        self.assertEqual(result.end_time, 1794011095.167)
        self.assertEqual(result.telemetry_status, "available")
        self.assertEqual(result.video_status, "referenced")
        self.assertEqual(result.video_path, SIGNED_VIDEO_URL)
        self.assertEqual(result.motors["truck"].velocity.value, [0.0, 11.616])
        self.assertEqual(result.logs, {})
        repository.get_order.assert_called_once_with(MACHINE_ID, ORDER_ID)
        assets.read_telemetry.assert_called_once_with(MACHINE_ID, TELEMETRY_KEY)
        assets.create_video_url.assert_called_once_with(MACHINE_ID, VIDEO_KEY)

    def test_missing_order_never_attempts_asset_reads(self):
        service, repository, assets = self.make_service()
        repository.get_order.return_value = None
        self.assertIsNone(service.get_order(MACHINE_ID, ORDER_ID))
        assets.read_telemetry.assert_not_called()
        assets.create_video_url.assert_not_called()

    def test_metadata_failure_does_not_fall_back_to_assets_or_files(self):
        service, repository, assets = self.make_service()
        repository.get_order.side_effect = UpstreamUnavailable("Metadata unavailable")
        with self.assertRaises(UpstreamUnavailable):
            service.get_order(MACHINE_ID, ORDER_ID)
        assets.read_telemetry.assert_not_called()
        assets.create_video_url.assert_not_called()

    def test_missing_telemetry_reference_preserves_available_video(self):
        service, repository, assets = self.make_service(order_record(telemetry_storage_file=None))
        result = service.get_order(MACHINE_ID, ORDER_ID)
        self.assertEqual(result.telemetry_status, "not_recorded")
        self.assertEqual(result.video_path, SIGNED_VIDEO_URL)
        self.assertEqual(result.motors, {})
        assets.read_telemetry.assert_not_called()

    def test_asset_failures_are_distinct_without_hiding_video(self):
        cases = [(None, "not_uploaded"), (UpstreamUnavailable("storage"), "unavailable"),
                 (InvalidTelemetry("bad JSON"), "invalid"), (InvalidStorageReference("bad ref"), "invalid")]
        for failure, expected in cases:
            with self.subTest(expected=expected):
                service, repository, assets = self.make_service()
                if failure:
                    assets.read_telemetry.side_effect = failure
                else:
                    assets.read_telemetry.return_value = None
                result = service.get_order(MACHINE_ID, ORDER_ID)
                self.assertEqual(result.telemetry_status, expected)
                self.assertEqual(result.video_path, SIGNED_VIDEO_URL)
                self.assertEqual(result.motors, {})

    def test_invalid_times_or_series_cannot_be_displayed_as_valid_charts(self):
        cases = [telemetry_data(start_time=ORDER_ID + 20, end_time=ORDER_ID + 30),
                 telemetry_data(end_time=ORDER_ID - 5), telemetry_data(truck_velocity_time=[0.0]),
                 telemetry_data(start_time=float("inf")),
                 telemetry_data(truck_velocity_value=[0.0, float("inf")]),
                 telemetry_data(truck_velocity_time=[0.0, float("nan")]),
                 json.loads(json.dumps(telemetry_data()).replace('11.616', '1e999'))]
        for data in cases:
            with self.subTest(data=data):
                service, repository, assets = self.make_service(telemetry=data)
                result = service.get_order(MACHINE_ID, ORDER_ID)
                self.assertEqual(result.telemetry_status, "invalid")
                self.assertEqual(result.motors, {})
                self.assertEqual(result.video_path, SIGNED_VIDEO_URL)

    def test_missing_video_does_not_hide_charts(self):
        service, repository, assets = self.make_service(order_record(video_storage_file=None))
        result = service.get_order(MACHINE_ID, ORDER_ID)
        self.assertEqual(result.telemetry_status, "available")
        self.assertEqual(result.video_status, "not_recorded")
        self.assertEqual(result.video_path, "")
        assets.create_video_url.assert_not_called()

    def test_invalid_identity_rejected_before_metadata_lookup(self):
        for machine, uid in [("../outside", ORDER_ID), (MACHINE_ID, float("nan")), (MACHINE_ID, 0)]:
            with self.subTest(machine=machine, uid=uid):
                service, repository, assets = self.make_service()
                with self.assertRaises(ValueError):
                    service.get_order(machine, uid)
                repository.get_order.assert_not_called()


if __name__ == "__main__":
    unittest.main()
