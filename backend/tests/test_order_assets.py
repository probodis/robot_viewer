"""Asset reads are exact, bounded and contained in one machine namespace."""
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from botocore.exceptions import ClientError

from adapters.order_assets import S3OrderAssets, asset_key, validate_order_identity
from app.services.errors import InvalidStorageReference, InvalidTelemetry, UpstreamUnavailable
from fixtures import MACHINE_ID, ORDER_ID, TELEMETRY_KEY, VIDEO_KEY, telemetry_data


class S3OrderAssetsTests(unittest.TestCase):
    def make_assets(self, content=b"{}", content_length=None, max_bytes=1024 * 1024):
        body = Mock(wraps=io.BytesIO(content))
        client = Mock(spec=["get_object"])
        client.get_object.return_value = {
            "Body": body,
            "ContentLength": len(content) if content_length is None else content_length,
        }
        s3 = SimpleNamespace(client=client, bucket_name="test-bucket", get_presigned_url=Mock(return_value="signed-url"))
        return S3OrderAssets(s3, max_bytes=max_bytes), s3, body

    def test_reads_one_exact_json_object_and_closes_stream(self):
        data = telemetry_data()
        assets, s3, body = self.make_assets(json.dumps(data).encode())
        self.assertEqual(assets.read_telemetry(MACHINE_ID, TELEMETRY_KEY), data)
        s3.client.get_object.assert_called_once_with(
            Bucket="test-bucket", Key=f"xcubes/{MACHINE_ID}/logs/{TELEMETRY_KEY}")
        body.close.assert_called_once()
        self.assertEqual(body.read.call_count, 1)

    def test_declared_and_actual_oversize_close_stream_without_unbounded_read(self):
        for declared_size in [1000, 0]:
            with self.subTest(declared_size=declared_size):
                assets, s3, body = self.make_assets(b"x" * 1000, content_length=declared_size, max_bytes=64)
                with self.assertRaises(InvalidTelemetry):
                    assets.read_telemetry(MACHINE_ID, TELEMETRY_KEY)
                body.close.assert_called_once()
                if declared_size:
                    body.read.assert_not_called()
                else:
                    body.read.assert_called_once_with(65)

    def test_invalid_json_is_not_returned_as_usable_telemetry(self):
        for content in [b"", b"<xml>error</xml>", b"[]", b"null", b'{"x": NaN}', b'{"x": Infinity}', b"\xff"]:
            with self.subTest(content=content):
                assets, s3, body = self.make_assets(content)
                with self.assertRaises(InvalidTelemetry):
                    assets.read_telemetry(MACHINE_ID, TELEMETRY_KEY)
                body.close.assert_called_once()

    def test_only_missing_s3_objects_become_not_uploaded(self):
        for code in ["NoSuchKey", "404", "NotFound", "AccessDenied", "SlowDown"]:
            with self.subTest(code=code):
                assets, s3, _ = self.make_assets()
                s3.client.get_object.side_effect = ClientError(
                    {"Error": {"Code": code, "Message": "private detail"}}, "GetObject")
                if code in ["NoSuchKey", "404", "NotFound"]:
                    self.assertIsNone(assets.read_telemetry(MACHINE_ID, TELEMETRY_KEY))
                else:
                    with self.assertRaises(UpstreamUnavailable) as caught:
                        assets.read_telemetry(MACHINE_ID, TELEMETRY_KEY)
                    self.assertNotIn("private detail", str(caught.exception))

    def test_video_signing_does_not_list_or_head_objects(self):
        assets, s3, _ = self.make_assets()
        self.assertEqual(assets.create_video_url(MACHINE_ID, VIDEO_KEY), "signed-url")
        s3.get_presigned_url.assert_called_once()
        self.assertEqual(str(s3.get_presigned_url.call_args.args[0]), f"xcubes/{MACHINE_ID}/logs/{VIDEO_KEY}")
        s3.client.get_object.assert_not_called()

    def test_cross_namespace_and_encoded_paths_are_rejected_before_s3(self):
        for key in ["../other.json", "/start_order_converted/a.json", "start_order_converted/../a.json",
                    "start_order_converted\\a.json", "start_order_converted/%2e%2e.json",
                    "start_order_converted/a.json\n", "videos/a.json", "start_order_converted/a.mp4",
                    "xcubes/other/logs/start_order_converted/a.json"]:
            with self.subTest(key=key):
                assets, s3, _ = self.make_assets()
                with self.assertRaises(InvalidStorageReference):
                    assets.read_telemetry(MACHINE_ID, key)
                s3.client.get_object.assert_not_called()

    def test_invalid_machine_or_timestamp_is_rejected(self):
        for machine, uid in [("../other", ORDER_ID), ("machine/name", ORDER_ID), ("", ORDER_ID),
                             (MACHINE_ID, float("nan")), (MACHINE_ID, float("inf")),
                             (MACHINE_ID, -1), (MACHINE_ID, 0)]:
            with self.subTest(machine=machine, uid=uid):
                with self.assertRaises(ValueError):
                    validate_order_identity(machine, uid)


if __name__ == "__main__":
    unittest.main()
