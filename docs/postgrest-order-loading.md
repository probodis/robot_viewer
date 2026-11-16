# PostgREST order loading

## Why this reader exists

Incident investigated on 2026-11-13: opening machine `cb-3-0007`, UID
`1794011078.328`, returned 404 even though its video existed. The order began at
2026-11-07 00:24:38 UTC, while its telemetry appeared on line 90 of
`2026-11-06_start_order.txt.gz`, with `start_time=1794011078.318`.
The machine logger kept a filename chosen before midnight. The old Viewer
selected only the latest file whose filename date was at or before the UTC order
date, and incorrectly selected the November 7 archive. It returned before video
lookup.

The order row already contained:

```text
telemetry_storage_file = start_order_converted/1794011078.328.json
video_storage_file     = videos/2026-11-07_00-24-38.mp4
```

The referenced JSON was 90,311 bytes and contained full motor/sensor telemetry.
The referenced video was 4,461,686 bytes and its signed range GET returned 206.
No filename-date search is necessary for this order.

## Ownership and load boundaries

- `xrobotics-api` receives machine uploads and preserves machine-scoped artifact references.
- PostgREST exposes the existing read-only `orders` view; Viewer sends exact filters and selects only its required columns.
- `PostgrestOrdersRepository` owns metadata lookup and sanitized upstream errors.
- S3 access reads one bounded JSON and signs one exact video key.
- The order service maps validated telemetry into the existing viewer response.
- The HTTP layer owns client lifecycle and translates service errors into HTTP responses.
- Frontend order loading cancels old requests when the route changes or the user retries.
- Log listing and scanner searching remain explicit user actions, never automatic fallbacks.

The reader does not repair raw archives or missing database references during an
HTTP request. It does not introduce a new database connection pool in Viewer or
a parallel order metadata store. Historical backfill and a time-range log
catalog are separate future work.

## Operational checks

Keep credentials out of output while performing checks. From the Viewer backend
container, exercise the HTTPS Data API with its dedicated key and exact
`machine_id`, `uid`, `select` and `limit=1` parameters. Confirm a single matching
row and the two expected artifact references. Test the new Viewer order endpoint
and inspect only status/summary fields; avoid copying signed URLs into logs.

For the incident order verify:

1. HTTP 200, `telemetry_status=available`, `video_status=referenced`.
2. Non-empty motor series and millisecond telemetry timestamps.
3. A video link whose range request returns 206.
4. No daily-file selector, directory walk or S3 listing during order loading.
5. No automatic scanner or machine-log request from the browser on page opening.
6. Scanner search and manual selected-file log windows still work when explicitly requested.

Also verify a nonexistent order (404), metadata-service failure (502/503), missing
telemetry reference/object, invalid JSON and missing video reference. Telemetry
failure must preserve a valid video link. Navigating between orders while a
request is pending must not display a previous order's response or diagnostics.

## Logs and troubleshooting

Application log events include:

- `event=order_metadata_loaded`: machine, UID, row presence and metadata duration.
- `event=order_metadata_failed`: machine, UID and sanitized failure reason/status.
- `event=order_view_loaded`: machine, UID, telemetry/video statuses and total duration.
- `event=order_log_window_loaded`: explicitly selected log, bytes scanned, truncation and status.

Use machine and UID to correlate an incident. Never log `POSTGREST_API_KEY`,
`X-API-Key`, AWS credentials, complete telemetry payloads or video presigned URLs.
The frontend exposes friendly error states rather than raw upstream responses.

| Observation                | Investigation                                                                           |
| -------------------------- | --------------------------------------------------------------------------------------- |
| Order 404                  | Verify exact machine/UID row in PostgREST; do not assume its video is missing           |
| Order 502                  | Check key authorization, required order columns and metadata contract                   |
| Order 503                  | Check backend-to-nginx connectivity, Data API health and shared pool availability       |
| Telemetry `not_recorded`   | No stored reference; assess historical backfill separately                              |
| Telemetry `not_uploaded`   | Reference exists but S3 object is absent; check upload processing                       |
| Telemetry `invalid`        | Check source JSON size, syntax, order timestamp and series validation                   |
| Telemetry `unavailable`    | Check S3 reachability, credentials and read timeout                                     |
| Video playback failure     | Refresh link, then verify exact object, permissions, region and browser codec support   |
| Log window 409             | No usable recorded duration; inspect the explicitly selected full file                  |
| Empty/truncated log window | Check actual record timestamps and reading limits; filename dates are not authoritative |

A separate Viewer Data API key can be revoked without changing ERP or other
consumers' keys. Rotation updates the nginx key map and the dedicated Viewer
environment, validates/reloads nginx, and recreates the backend so it reloads its
environment. Keep any temporary overlap between old and new keys brief and
verify the new key before removing the old entry.

## Verified production deployment — 2026-11-16

Robot Viewer 0.8.0 was deployed and verified through the original public order
URL. The production frontend serves `index-EtoTFBQT.js`. The dedicated PostgREST
key is stored in the separate root-owned environment file with mode `0600`;
credential values are not included in this record.

| Check | Observed result |
| --- | --- |
| Incident order `cb-3-0007` / `1794011078.328` | HTTP 200; telemetry `available`; start `1794011078.318`, end `1794011095.167`; 9 motors; 199 truck velocity points |
| Referenced video | Range GET 206; browser `readyState=4`; duration 16.92 seconds; 820 × 616; no playback error |
| Control order `1794267966.115` | HTTP 200 |
| Nonexistent order `1794011078.329` | HTTP 404 |
| Explicit log window `orders/2026-11-06_orders.txt.gz` | HTTP 200; 17 lines; not truncated |
| Automated checks | 33 tests passed on Windows / Python 3.12 and Linux container / Python 3.11 |

A rollback snapshot is retained on the server at
`/home/ubuntu/robot_viewer_releases/postgrest-20261116/`:

- `previous-source.tar.gz`: source checkout before deployment.
- `previous-frontend.tar.gz`: frontend assets before deployment.
- `rollback.json`: recorded rollback metadata.
- Docker image tag `robot-viewer-postgrest:rollback-20261116`: previous backend image.

Keep these artifacts until the deployment has completed its observation period.
Rollback must restore the matching source, backend image and frontend assets;
do not mix reader contracts from different releases. Treat key revocation as a
separate operational step so other Data API consumers remain unaffected.
