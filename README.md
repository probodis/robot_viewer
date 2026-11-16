# Robot Telemetry Viewer

Robot Telemetry Viewer displays machine-scoped orders, synchronized video and
telemetry charts. Its FastAPI backend reads order metadata through the existing
read-only PostgREST Data API and reads the referenced telemetry JSON from S3.
The Vue frontend never receives the Data API key or AWS credentials.

## Data flow

```text
Browser -> Viewer backend -> PostgREST orders (machine_id + uid)
                          -> S3: one referenced telemetry JSON
Browser -> S3: video through a short-lived presigned URL
```

The machine uploads a per-order JSON and video, then sends their relative
`telemetry_storage_ref.log_key` and `video_storage_ref.log_key` with the order.
`xrobotics-api` stores these as `telemetry_storage_file` and
`video_storage_file`. Viewer resolves them under
`xcubes/{machine_id}/logs/` in `DATA_BUCKET_NAME`.

An order request uses exact machine and UID filters with a one-row limit.
It does not list local directories, scan daily telemetry archives, list S3 keys,
or guess video filenames. Opening a page also does not search scanner archives
or list additional logs; those operations require an explicit user action.

## Project structure

- `backend/adapters/`: PostgREST and S3 access.
- `backend/app/services/`: order assembly and bounded log-window extraction.
- `backend/app/schemas/`: validated metadata and viewer response contracts.
- `backend/app/main.py`: HTTP routes and dependency lifecycle.
- `frontend/src/composables/useOrderData.js`: cancelable order loading and explicit scanner search.
- `frontend/src/components/OrderDiagnostics.vue`: manual log selection and window loading.
- `docs/`: architecture, operation and incident notes.

## Backend configuration

Docker and Docker Compose are required for deployment. Backend settings are
read from process environment variables. Production Compose loads the shared
AWS configuration from `/etc/xrobotics/secrets/.env` and the dedicated Viewer
Data API configuration from `/etc/xrobotics/secrets/robot-viewer-postgrest.env`.
Keep both files outside Git and restrict them to the deployment administrator.

The dedicated file must contain:

```dotenv
POSTGREST_BASE_URL=https://xapi.xrobotics.io/data-api/
POSTGREST_API_KEY=<dedicated robot-viewer key>
```

| Setting                             | Default     | Purpose                                            |
| ----------------------------------- | ----------- | -------------------------------------------------- |
| `AWS_ACCESS_KEY_ID`                 | required    | Backend S3 access                                  |
| `AWS_SECRET_ACCESS_KEY`             | required    | Backend S3 access                                  |
| `DATA_BUCKET_NAME`                  | required    | Machine artifact bucket                            |
| `AWS_S3_REGION`                     | `us-west-2` | Region used for S3 and signed video links          |
| `POSTGREST_BASE_URL`                | required    | Existing Data API base URL, ending in `/data-api/` |
| `POSTGREST_API_KEY`                 | required    | Dedicated backend-only `X-API-Key`                 |
| `POSTGREST_TIMEOUT_SECONDS`         | `5`         | HTTP operation timeout                             |
| `POSTGREST_CONNECT_TIMEOUT_SECONDS` | `3`         | HTTP connection timeout                            |
| `S3_READ_TIMEOUT_SECONDS`           | `10`        | S3 operation read timeout                          |
| `TELEMETRY_MAX_BYTES`               | `2097152`   | Maximum accepted telemetry JSON bytes              |

Use the HTTPS nginx endpoint from the backend container. `localhost` inside the
container is not the host. Do not bypass nginx by connecting Viewer to the
unauthenticated PostgREST container port. The existing Data API has a small
shared connection pool; requests are point lookups with no automatic retry loop.

### Dedicated Data API key

The API project already manages nginx authentication using
`/etc/xrobotics/secrets/data-api-keys.map`. Provision a unique random key for
Viewer in that map and store the same value in the dedicated Viewer environment
file. Preserve existing consumers' keys. Validate nginx configuration before
reloading. Do not put a real key into source files, command-line arguments,
frontend `VITE_*` settings, logs or a browser request.

Each current map entry grants the same read-only Data API role. A separate key
allows independent rotation/revocation; it does not restrict Viewer to fewer
tables. Only the Viewer backend uses this key and selects the required order
columns. No new endpoint in `xrobotics-api` or database password for Viewer is
required.

## Running and deploying

For local development, put backend settings in an ignored `.env.local` file.
Review the machine-data and log-directory bind mounts in
`docker-compose.dev.yml` for your workstation, then run:

```sh
docker compose -f docker-compose.dev.yml up --build
```

- Frontend: `http://localhost:5173/robot_viewer/`
- Backend API documentation: `http://localhost:8000/docs`

For a reviewed production checkout with secrets already provisioned:

```sh
make deploy-backend
make deploy-frontend
```

`make deploy` also fetches and hard-resets to `origin/main`; use it only when
that is the intended release. Do not use it to test an uncommitted change.
The backend uses `/home/ubuntu/xcubes/` read-only for explicitly requested
machine logs, and writes application logs to `/home/ubuntu/logs/robot_viewer/`.

Existing nginx must route `/robot_viewer/api/` to the backend and serve the
frontend assets under `/robot_viewer/`. The static location is:

```nginx
location = /robot_viewer {
    return 301 /robot_viewer/;
}
location /robot_viewer/ {
    alias /var/www/robot_viewer/;
    try_files $uri $uri/ /robot_viewer/index.html;
    index index.html;
}
```

Build the frontend independently with:

```sh
cd frontend
npm ci
npm run build
```

## Order API and availability

`GET /api/v1/orders/?machine_id=cb-3-0007&order_id=1794011078.328`
retains the existing chart/video response fields and adds explicit statuses:

| Field                | Values                                                                |
| -------------------- | --------------------------------------------------------------------- |
| `telemetry_status`   | `available`, `not_recorded`, `not_uploaded`, `invalid`, `unavailable` |
| `video_status`       | `referenced`, `not_recorded`, `unavailable`                           |
| `diagnostics_status` | `on_demand`                                                           |

`not_recorded` means the database has no reference, not that a raw historical
archive has been exhaustively searched. `not_uploaded` means the referenced
telemetry object was not found in S3. `invalid` covers malformed or mismatched
telemetry; `unavailable` covers an upstream failure. `referenced` means a video
link was generated from the stored reference; it is not an assertion that the
browser has downloaded or decoded the object. The player reports playback
failures separately and can refresh an expired link by reloading the order.

Missing/invalid telemetry leaves `motors` empty and preserves an available video
link. A missing order row returns 404. PostgREST transport/temporary failures
return 503; rejected or malformed metadata responses return 502. They are not
reported as a nonexistent order. Old orders without stored artifact references
do not trigger archive scans or automatic historical reconstruction.

## Diagnostics on demand

- **Find scanner archive** invokes the existing scanner endpoint only when clicked.
- **Browse machine logs** explicitly lists files via `GET /api/v1/machine/logs`.
- Select one text file and click **Load order window** to call
  `GET /api/v1/orders/log-window?machine_id=...&order_id=...&log_key=...`.
- **Open full file**, or double-click a loaded log tab, opens the existing full-file view.

A window request reads only the named file, with limits on decompressed input,
line count and output. It reports truncation, no supported timestamps, or an
empty time window explicitly. It uses the database order time and duration;
missing duration returns 409 rather than guessing. Full-file views are separate
explicit reads capped at 10 MiB decompressed. Larger files return 413 and the
interface offers a download of the original file instead.

The main order response has `logs={}` and `extra_weight_points=[]`.
Additional sauce-weight points are not reconstructed from a daily filename:
that date can differ from its records' actual day. The manual log window reports
`extra_weight_points_status=not_available`. Weight series present in the
per-order telemetry JSON remain available in the charts. A future historical
index or conversion job can restore missing references and supplementary points
without adding an archive search to page loading.

The machine video list remains available at `/robot_viewer/machine/<machine_id>`
and `GET /api/v1/machine/videos?machine_id=<machine_id>`. It lists recent videos
only when that separate page is opened.

## Incident investigation

See [PostgREST order loading and verification](docs/postgrest-order-loading.md)
for the midnight-file incident, expected log events and acceptance checks.
