# Section9 paired Showcase contract

The original `/` is the operator console. `/showcase/swarm` and `/showcase/baseline` are read-only projections. A fixed link uses `?pair_id=...`; visiting or refreshing it never creates an experiment or calls a model. The active Pair pointer means the latest selected experiment, including its completed result; it does not mean there is currently executing work. A missing active Pair is an empty state. Unknown IDs return a JSON API error and a visible page error; there is no global SPA fallback.

## Authority and lifecycle

`PairCoordinator` runs inside the existing backend. Every arm has an explicit `RuntimeContext`, Store SQLite database, configuration, VictimApp, worker processes, tasks, leases, plans, grants, usage ledger and memory output directory. Paths derive only from catalog-generated IDs. The legacy console remains its own runtime. `RUN_ACTIVE` still applies within each Store.

`POST /api/pairs` accepts only scenario, seed and optional previous_pair_id. It resolves the configured model and policies on the server and freezes the full spec, fixture text snapshot, source identity and content hash. It assigns two permanent run IDs and prepares healthy configurations without provider calls. Per-arm model, budget and fault overrides are rejected. Idempotency-Key reuse with different input returns 409. Internal catalog updates cannot replace the spec or either run ID.

`POST /api/pairs/{id}/start` requires expected_spec_hash. Both worker sets prepare behind `execution_enabled=false`; each Store commits its fault; hashes are compared; only then is the barrier released. These are separate SQLite transactions, not a distributed atomic commit. Failure leaves setup_failed evidence and stops both sides. Actual fault times and release skew are retained. Only one Pair with unfinished arms may run at once; creating an unstarted Pair is harmless.

An arm can succeed or fail independently. completed means both arms ended, not that both passed. `/reset` for one arm invalidates that arm first, cancels only its local model tasks and restores the Pair's frozen healthy snapshot; the other arm continues. The Pair loses comparison eligibility. Full reruns create a new Pair and two new run IDs. Restart recovery interrupts preparing/ready/running work and unfinished arms in an already interrupted Pair; no old worker lease resumes. Reserved usage from a crashed process is preserved as unknown.

## Shared model connection and security

`ProviderGateway` shares the HTTP client and bounded admission capacity. Waiting scopes rotate; unused slots are borrowable. ModelClient instances retain separate `(pair_id,run_id,arm,generation)` cancellation maps and separate Store budget accounts. queued, admitted, provider_started and completed/failed/cancelled events record queue/provider/total durations. Before-provider cancellation releases its reservation with a known zero provider cost; cancellation after sending conservatively retains unknown usage. Local HTTP cancellation cannot prove supplier-side computation or billing stopped.

Workers retain macOS Seatbelt restrictions: only worker code/runtime and the restricted agent ingress are available, not `.env`, databases, logs, memory, the console, or Showcase. The server resolves a bearer token to its runtime before routing any agent method. Task, actor, run, plan and grant bind pair/run/arm/spec as well as the existing instance, generation, epoch, fence and transport fields. Configuration revision and content hash bind execution and independent verification. No public API offers raw Store writes.

The baseline has one reasoning worker (`single`). `sentry` and independent verification are deterministic infrastructure. Baseline receives the union of raw business/configuration facts available to the specialists, but cannot read their conclusions. Both arms use the same configured model, fault, fixture, acceptance contract and total budget. Memory retrieval is off on both sides; successful output is arm-local. Seed is recorded for fixture selection; this implementation has one fixture variant and does not claim provider determinism. Supplier cache is uncontrolled. Shared Mac and service processes are a common failure domain.

## Journal and read model

Arm events remain in their authoritative transaction. PairJournal imports committed events idempotently using `(run_id,run_sequence,event_id)` and durable per-run cursors. Its own sequence orders journal commits, not causal order across arms. The journal can be rebuilt from source Store events. System/null-run events do not become actor activity.

Arm snapshots and Logbook use the same imported journal and publish as_of_sequence. Reads run synchronously on the authority event loop. SSE accepts arm, after and Last-Event-ID and filters on the server. Frontend snapshots validate pair/run/arm, cancel old-scope reads, reconcile after reconnect, and deduplicate by event ID. Pausing log scrolling does not pause fetching, workers or the experiment.

Seven stages carry actual source event IDs and times. Baseline diagnosis and planning share a real single decision/plan event and have no invented separate duration. Failed verification never becomes a green completed stage. Actor status and location are separate activity projections with source_event_id and position_event_id. The UI labels movement as a mapping of activity, not physical telemetry. Raw evidence includes failures, cancellations and unknown costs.

Telemetry exports pair/run/arm metadata. Collector ingress uses the catalog mapping to find the arm Store, not a caller-provided filesystem path. Telemetry remains evidence, never execution authority.

## API

- `GET/POST /api/pairs`; `GET /api/pairs/active`; `GET /api/pairs/{pair_id}`
- `POST /api/pairs/{pair_id}/start` with `{ "expected_spec_hash": "..." }`
- `GET /api/pairs/{pair_id}/arms/{swarm|baseline}`
- `GET /api/pairs/{pair_id}/events?arm=swarm&after=0` (SSE)
- `GET /api/pairs/{pair_id}/logbook?arm=swarm&after=0`
- `POST /api/pairs/{pair_id}/reset` with `{}`
- `POST /api/pairs/{pair_id}/arms/{arm}/reset` with `{}`

No paired record is created by combining historical independent experiments. Old scoreboard records remain legacy experiments. Paired comparison eligibility and business success are separate fields; a real failed arm remains a valid observed outcome unless the experiment was also interrupted or inconsistent.
