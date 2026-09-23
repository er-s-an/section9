# Implementation status

Updated: 2026-09-24 (Asia/Shanghai)

## Checkout and safety boundary

- Repository: `/Users/xiejiachen/Documents/ChatGPT/rebuild/section9`
- Branch: `codex/local-product-readiness`
- HEAD: `ad504386ecfe54f113bd257229698fbe48e3314f` (unchanged during this work)
- The checkout was dirty before this work. Existing UI, docs, and product code changes remain in place; no reset, clean, blanket staging, or broad commit was done.
- No GitHub push, PR, deployment, external account access, credential use, external write, or paid model request was performed. New paid-provider requests: **0**.
- The two previously observed local services were not stopped or restarted. The new Workspace schema therefore has not been applied to their live databases; tests use isolated temporary databases.

## Milestone state

| Work | State | Evidence and limit |
| --- | --- | --- |
| M0 R1 request dispatch authority | `verified` (local regression scope) | Final authority is rechecked before provider dispatch; cancellation and spend reconciliation have deterministic regressions. Paid-provider in-flight cancellation was not tested. |
| M0 R2 stable Pair target | `verified` (local browser regression scope) | Selection is independent from the active poll target; captured selection/spec hash bind start/reset. Browser test uses API stubs. |
| M0 R3 request outcome truth | `verified` (local browser regression scope) | Success, error, cancellation, and unknown usage remain distinct in tested UI surfaces. No provider settlement was performed. |
| M0 R4 history and evidence paging | `verified` (local regression scope) | Fixed-watermark paging, exact event-ID lookup, incremental counts, evidence lookup, export path, browser paging, and >10,000-record backend history have local regression coverage. |
| M0 R5 process ownership | `verified` (local ownership-test scope) | Versioned identity records and strict PID/executable/role/root/port checks cover service scripts and external runtime. No live service was stopped. |
| M0 cost correction | `implemented`; live-provider recovery `not run` | Historical read-only evidence indicates pending-reservation contention and a verifier request rejected before provider dispatch. Verification requests are serialized and known-zero pre-dispatch failures are accounted separately. No paid recovery run was authorized. |
| M1 Workspace setup foundation | `partially implemented` | Strict v1 contracts; additive schema migration; workspace/application/connection/scope-binding storage; scoped list/get/create routes; idempotency receipts; event log; and a setup UI exist. Browser tests use API stubs. Storage/API tests use temporary databases. Routes rely on a local operator origin and have no authenticated multi-user principal or role model. |
| M1 Signal inbox and Incident intake | `local intake and opt-in sync implemented; milestone exit not met` | Langfuse observations and GitHub issues/PRs can be read in bounded windows from verified scopes. Source versions, neutral summaries, idempotency, per-window cursors, coverage, and audit events persist with imported Signals. Operators can start/append Incidents with revision checks and reasons. Explicitly enabled monitors run at 60s–24h intervals, at most five 100-row pages per run; cursors survive restarts, transient failures back off, authorization/scope failures pause, and outages over 24h produce a visible gap rather than an unbounded catch-up. Revision-checked Incident merge/split move links atomically and retain the merged source. Local single-operator claim/release and severity updates require reasons and revisions; team identity/RBAC is not implemented. A deterministic repeat-source rule may append an exact updated object to its unique active Incident within 24 hours; multiple candidates and older observations stay in triage. This does not establish causality. No real account was used. Broader grouping/suppression/routing, source-health alerting, and real provider proof remain open. |
| M1 connection check foundation | `implemented locally; real account verification not run` | Bounded read-only Langfuse sample/import and fixed-host GitHub repository metadata/default-branch/root listing are implemented with env-backed credentials, scope checks, and sanitized results. Unit/API tests use mock providers. No real credentials or provider calls were used. |
| M1 exit criteria | `not met` | GitHub repository issues/PRs enter through bounded manual reads or explicit opt-in polling, and local operators can append, claim, reprioritize, merge, and split Incidents. Exact repeat-source association has a local implementation and regression coverage; broader grouping/cooldown/routing, source-health alerting, real Langfuse/GitHub account proof, authenticated team identity, and EvoMap native E2/E3 task/session/Hub capability remain open. |
| EvoMap integration review | `public contract reviewed; E2/E3 blocked on authorization` | Current official public docs were checked on 2026-09-23. They list A2A sessions, direct messages, task/project routes, and Hub APIs; Section9's pinned local GEP SDK is not native collaboration. No node registration, session, task claim, remote read/write, publish, or credit operation was performed. See [EVOMAP_VALIDATION.md](EVOMAP_VALIDATION.md). |
| M2 investigation and remediation loop | `manual plus bounded model-backed task graph; milestone exit not met` | `single` and Section9-owned `swarm` graphs now execute serial role prompts through the configured chat-completions provider. Workspace token budgets and policy revisions are checked atomically before dispatch; reservations, known usage, and unknown outcomes persist in schema v7. Schema v8 adds one-time, hashed, environment-scoped Worker bearer credentials; claim/context/renew/finish resolve identity and capabilities server-side, with loopback-only local registration/revocation routes. On execution, expired leases with no dispatched request are reopened; post-dispatch interruption is stopped as failed with known or unknown usage preserved, never automatically resent. Tests exercise these states through temporary DB/API fixtures, not a real process kill or paid provider. The UI still requires a positive budget and explicit execute click. This is one configured model making separate requests, not independent/parallel reasoning, tool use, EvoMap-native swarm, or an evaluated RCA system. Dynamic DAGs, team RBAC, ProposalVersion/review/approval, execution, reconciliation, independent recovery validation, and M2 exit criteria remain open. |
| Feature and connector parity | `partially implemented` | The tracking overlay records partial implementations for 6 feature records and 2 connectors; full feature acceptance is not verified. All 36 source features, 83 connectors, and four supplemental tasks remain in scope. |
| M3–M5 | `not implemented to acceptance` | Full parity, operations, external collaboration, release/install, and production acceptance remain open. |

R1–R5 mean only that the listed local regression scope passed. They do not mean CloudThinker parity, production readiness, external-provider proof, or overall product completion.

## Verification record

Latest local validation after adding registered Worker authentication and interrupted-task recovery:

```text
.venv/bin/python -m pytest -vv
330 passed, 2 dependency deprecation warnings in 270.09 s

.venv/bin/python -m pytest -q tests/test_model_budget.py tests/test_model_cancellation.py tests/product/test_v1_task_graph_api.py tests/product/test_workspace_registry.py tests/product/test_signal_incident_registry.py
49 passed, 2 dependency deprecation warnings in 13.91 s

cd frontend && npm run build
passed; TypeScript and Vite production build transformed 4,592 modules.

cd frontend && npx playwright test -c playwright.workspace.config.ts
2 passed in 31.4 s. The browser flow covers workspace budget configuration and mock-backed model task execution; it does not contact a model provider.

cd frontend && npm run test:e2e:m0
3 passed; TypeScript and production Vite build passed.

Isolated local runtime/browser smoke (temporary SQLite directory, no model key or provider credentials):
workspace/application/connection/scope setup and monitor list loaded; an unverified scope could not enable polling; zero browser runtime errors. A separate temporary-process smoke created an Incident from synthetic manual Signals, attached a second Signal through the UI, and read back both clustered Signals, Incident revision 2, and the attachment audit event. No external provider or paid model call occurred.

Isolated main-ASGI HTTP smoke (temporary `S9_DATA_DIR`, free loopback port, blank model key): created four synthetic Signals and two Incidents, claimed one as `local-operator`, raised severity, split one Signal into a new Incident, merged it into the other Incident, and read back membership, merged target, revisions, and all six expected audit event types. The process was stopped after the smoke; no external provider or model call occurred.

The confirmed-scope repeat-source rule also passed registry and FastAPI HTTP-contract tests: same verified binding/object/type plus one active Incident and a new version within 24 hours is appended atomically; multiple candidates, unverified scope, and observations beyond 24 hours remain new Signals.

Task runtime coverage includes complete single/swarm graphs with provider mocks, structured role outputs, budget fail-closed behavior with zero provider requests, explicit model-use accounting, and unknown provider usage retaining the full reservation. Existing task-control tests cover evidence partition, challenger seed context, dependency gating, scoped outputs, retry, lease expiry/renewal, and epoch fencing. The API/browser verification uses mocks; no real provider request was made.

git diff --check
passed after the current implementation and documentation updates
```

The full pytest run included the registered Worker authentication, restart-recovery, ProductRegistry, and history regression suites. The dedicated >10,000-record case also passed earlier in this checkout:

```text
pytest -q tests/test_product_history.py::test_ingest_drains_fixed_high_water_and_pages_without_gaps
1 passed in 116.93 s
```

The broad backend/connector/security suite contains the new GitHub import, investigation, and durable monitor tests. The real Langfuse/GitHub account calls, current live database migration, and public deployment remain unverified. The two pre-existing local service databases were not migrated.

## Next concrete product work

1. Replace the deterministic initial task plan with model-proposed DAGs that receive server-side dependency, capability, scope, and budget validation; add a bounded read-tool loop and compare single investigator and swarm paths under equal evidence, tools, budgets, and time.
2. Add immutable ProposalVersion and revision/hash-bound human approval, then fenced execution, reconciliation of unknown outcomes, and independent recovery validation.
3. Continue M1 grouping/suppression/routing and source-health alerts; implement remaining catalog domains and connector acceptance, then complete installer, release, and deployment evidence.

Provider-backed validation requires the user to configure the scoped credentials in the local ignored `.env` and authorize read-only calls. It is not a reason to stop the independent local implementation work above.
