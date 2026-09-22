# Section9 observability

This stack is local-only and uses Compose project `section9-observe`. Langfuse v4 is served at `http://127.0.0.1:9030`; MinIO is at `http://127.0.0.1:9090` and its console at `http://127.0.0.1:9091`. The OTLP/HTTP Collector ingress is `http://127.0.0.1:9431/v1/traces` (the requested label `94318` cannot be a host TCP port because valid ports end at 65535; container port remains the official `4318`).

The Python `Telemetry` producer sends OTLP/HTTP to the Collector. The Collector has two independent queued pipelines: one to the root backend at `/api/telemetry/v1/traces`, and one to local Langfuse v4 at `/api/public/otel/v1/traces`. The Langfuse path carries `x-langfuse-ingestion-version: 4` and Basic auth from the locally generated key. No Cloud endpoint is configured. Backend control events remain independent of span completion.

## Local configuration

Secrets and headless-init credentials are in `infra/.env` (mode 0600; ignored by the repository root `.gitignore`). Do not copy values into chat or logs. To load the same configuration for the root process, use the env names from that file and source it in the root process launcher, for example `set -a; . section9/infra/.env; set +a`; the authoritative file path is `section9/infra/.env`.

The Langfuse v4 direct OTLP path is verified by the Collector config and the `LANGFUSE_INIT_*` values attempt to create a local org, project, user, and project key on first web startup. The generated key is only used in the Collector's local environment.

## Evidence commands

Run from this directory:

```sh
docker compose --project-name section9-observe --env-file infra/.env -f infra/docker-compose.yml ps
curl -fsS http://127.0.0.1:9030/api/public/health
curl -fsS http://127.0.0.1:9133/
docker compose --project-name section9-observe --env-file infra/.env -f infra/docker-compose.yml images
```

The root backend endpoint must be live at `127.0.0.1:9019` before a fan-out delivery probe can be marked successful. A collector health response alone proves ingress readiness, not backend persistence.

Lifecycle helpers are `scripts/infra-start.sh`, `scripts/infra-stop.sh`, and `scripts/infra-status.sh`; each pins the Compose project name and `.env` path. Stop intentionally leaves all named volumes intact. Run `scripts/check-telemetry.py` after the root app has emitted a real span. It reads the local Langfuse v4 Observations API using the local project key with `fields=core,basic,metadata,time` (metadata is omitted by the v2 default response), prints only redacted trace IDs and safe metadata, and stores a JSON record in `artifacts/telemetry/`. An `empty` result means no matching real Section9 span was found in the last 24 hours; it is not a successful synthetic probe.

## Final verification and limitations

The initial run proved Langfuse ingestion only. A final independent check found the backend fan-out rejected the Collector's default gzip body with HTTP 400. The receiver now performs size-limited gzip decoding; malformed bodies create an explicit rejection event. `check-telemetry.py` requires matching trace IDs in both the backend event store and Langfuse before reporting dual-path success. Earlier Langfuse-only reports are retained and do not prove backend fan-out.

Log in to the local dashboard using the local initialization fields `LANGFUSE_INIT_USER_EMAIL` and `LANGFUSE_INIT_USER_PASSWORD` in the protected `infra/.env`. The application loads this configuration automatically. The project is Section9 Observability. Browser evidence is in `artifacts/observability-browser/`; it contains no credentials.

Langfuse's unconfigured monetary-cost column may display $0.00. No EvoMap billing-price table has been configured: that display is not a claim of zero spending. Section9 uses provider-reported token counts; missing usage remains unknown, and missing USD cost is not estimated.

Registry images and the Collector base are pinned to the verified local digests in `infra/images.lock.json`. Only this project's services and volumes are managed.
