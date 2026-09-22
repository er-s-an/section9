# Local setup

This repository keeps runtime credentials out of Git. A fresh checkout can
create the ignored files required by the local Section9 service and the
Langfuse Compose project:

```sh
python3 scripts/init-local-config.py --root .
python3 scripts/init-local-config.py --root . --check
```

The first command creates `.env` for the application and `infra/.env` for the
local observability Compose project. It uses fresh random values for the
database, Langfuse, ClickHouse, Redis, MinIO, and application secrets. It
never overwrites an existing file, copies or prints a secret, and does not
start Docker, recreate a database, or contact a provider. The second command
checks only variable names and file mode; it does not report dotenv values.

The generated application file has safe local defaults for port `9019`, data
directory `data`, and the remote EvoMap inference endpoint. The
`EVOMAP_MODEL_API_KEY` entry is left empty by design. If the selected model
needs a key, edit the ignored root `.env` on the local machine. The helper
does not read claims or copy configuration from elsewhere.

`infra/.env.example` documents the names consumed by the checked-in
`infra/docker-compose.yml`; it contains no credentials. The generated
`infra/.env` also supplies the local Langfuse organization, project, and
operator bootstrap values. The operator can then start the existing stack with
the repository command:

```sh
./scripts/start.sh
```

The observability UI is bound to the local machine by the existing Compose
configuration. Keep `infra/.env` and the root `.env` private; both are ignored
by Git. Keep the configuration with its existing database volumes; generating
new credentials for existing volumes would make their credentials inconsistent.
This helper does not perform credential rotation. No headless initialization is performed by the configuration helper;
the Compose environment carries the local bootstrap values for the normal
stack startup.

For a non-destructive validation against another checkout or fixture, point
the helper at that root:

```sh
python3 scripts/init-local-config.py --root /path/to/checkout --check
```

The generator derives the observability variable list from the current Compose
file, so a new placeholder causes validation to fail until the local generator
and example are updated together.

Validation: temporary-file generation, 0600 modes, preservation of existing files,
minimal model configuration and `docker compose config --quiet` passed. See
`artifacts/local-setup/report.json`. A fresh empty-volume Docker startup was not
performed in this publication check; the existing local stack remains healthy.
