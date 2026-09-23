# Implementation decisions

Updated: 2026-09-23

## D-001 — Preserve the current checkout and build on existing WIP

The repository is on `codex/local-product-readiness` at `ad504386ecfe54f113bd257229698fbe48e3314f`. Existing user edits are kept. Changes that share files with existing UI work remain uncommitted; no blanket staging or commit was performed.

## D-002 — Keep M0 dispatch authority inside the existing request path

The final provider dispatch decision rechecks captured run, scope, task lease, policy/contract revision, generation, and budget under the store authority. A queue admission alone is not permission to dispatch. Cancellation and dispatch are ordered at that final authority check. Provider calls that already crossed dispatch preserve actual or unknown usage.

## D-003 — Treat the historical cost failure as reservation contention

The baseline verifier that failed was rejected before provider dispatch after waiting at its request deadline while concurrent reservations occupied the remaining budget. Settled actual usage was below the arm limit. This evidence does not support describing the incident as actual token overrun. The verify path now serializes checks and distinguishes known-zero pre-dispatch outcomes from dispatched requests with unknown usage.

## D-004 — Freeze product API v1 beside the legacy laboratory contract

`s9/product/v1_contracts.py` and `docs/implementation/CONTRACTS.md` define the new product domain boundary. They do not reinterpret current `/api` or `/agent` payloads as v1 and do not add routes, migrations, or fake-success responses. Incident identity is long-lived; each InvestigationRun is a separate terminal attempt. Proposal content is hash-bound; execution and validation bind exact versions.

## D-005 — Do not promote the existing project scope to workspace authority

Existing ProductRegistry records use `project_id` and `environment_id`; those fields are useful for current product flows but are not equivalent to a Workspace security boundary. M1 storage/API work must add explicit workspace ownership and resolve it from authenticated server context before mapping or migrating records.

## D-006 — Preserve registry source semantics and unknown acceptance

The source JSON is copied byte-for-byte under `docs/parity/source/` and SHA-256 recorded. The YAML keeps every source field and source ID. Tracking overlays are additive and record partial implementation for six feature records and two connector records; none of those full capabilities is marked verified. Four supplemental integration tasks remain without generated IDs.

## D-007 — Keep external capability claims separated

Remote model inference, local GEP assets, native EvoMap task/session collaboration, and Hub search/publish/identity/receipts remain separate capabilities. A local asset, model call, or compatible API does not prove native swarm or Hub access. No external credentials, publishing, or paid calls were used.

## D-008 — Process control fails closed on uncertain identity

Stop logic requires a versioned process identity match, including process start identity and executable/arguments plus the recorded repository role/root/port. PID reuse, damaged records, or identity mismatch must not signal the process. Runtime timeout kill is preceded by identity recheck; the service scripts do not escalate to SIGKILL. This change was tested without stopping the active Section9 services.

## D-009 — Ship the first Workspace setup slice as a local operator surface

Workspace, Application, Connection, and ScopeBinding now have additive storage, idempotent setup routes, and an event log. The first user surface is limited to the local operator origin already enforced by the parent app. It is not a multi-user authorization system and must not be described or exposed as production SaaS identity. Existing project/environment and Pair identities are still not implicitly migrated.

## D-010 — Treat provider verification and provider ingestion as different capabilities

Langfuse's bounded one-row check and GitHub's metadata/default-branch/root listing check prove only the selected connection and scope to the extent stated in their response. Neither check means historical/current data was fully ingested. Separate, explicit bounded import actions may create neutral Signals with deduplication and coverage records; people triage them into Incidents. GitHub issue/PR paging is best-effort over mutable updated-time ordering and is not presented as a stable source snapshot.

## D-011 — Restrict automatic Signal association to repeated source objects

Operator attachment of new Signals still requires the current Incident revision, each Signal revision, and a required operator reason. For explicitly polled imports only, a new version of the exact same object in the exact same verified source binding may be appended automatically when a prior version was already Incident-linked, that previous observation is within 24 hours, and exactly one active Incident matches. The Signal, Incident membership, relationship, and audit events update in one transaction. Multiple candidates, stale versions, other source kinds, and older observations remain in the inbox. This narrow identity rule does not infer causality from shared services or nearby timestamps; broader grouping, suppression, and routing remain unimplemented.

## D-012 — Make scheduled source polling explicit and bounded

Monitors are off until an operator enables them for a verified Langfuse/GitHub binding. They reuse the manual import handlers and their idempotent checkpoints, read at most five pages per run, and retry temporary outages with backoff. Missing credentials, permissions, or scope confirmation pause a monitor. After an outage longer than 24 hours, the skipped interval is recorded and automatic polling resumes at the newest bounded window; older history requires a separately selected manual import. The local scheduler does not infer incidents or call a model.

## D-013 — Keep EvoMap protocol discovery separate from external enrollment

The official public protocol index was re-read on 2026-09-23 and records A2A sessions, direct messages, project task routes, and Hub experience APIs. The official GEP MCP server is a separate client surface from those A2A collaboration routes; Section9's local SDK bridge remains offline. Do not call `hello`, create a session, claim a task, publish, provision, or spend credits without explicit external-identity/test-scope authorization. The exact sources and acceptance evidence are recorded in [EVOMAP_VALIDATION.md](EVOMAP_VALIDATION.md); public documentation review does not pass E2/E3.
