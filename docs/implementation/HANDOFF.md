# Handoff

Updated: 2026-09-24

## Checkout and working-tree state

- Repository: `/Users/xiejiachen/Documents/ChatGPT/rebuild/section9`
- Branch/HEAD: `codex/local-product-readiness` / `ad504386ecfe54f113bd257229698fbe48e3314f`
- The checkout was already dirty. Existing user edits and new uncommitted work coexist. No reset, clean, blanket stage, commit, push, PR, or deployment was done.
- Two pre-existing local services were left running. The new migration has only been applied to test databases; it has not been run against their live databases.

## Current implementation boundary

- M0 R1–R5 fixes and tests are local only; see [STATUS.md](STATUS.md).
- Workspace v1 has a strict schema, additive SQLite setup migration, Workspace/Application/Connection/ScopeBinding storage and APIs, idempotency receipts, an event log, and an operator UI.
- Langfuse bounded observation checks and GitHub repository checks are implemented locally. API/E2E checks use mocks; there was no real external-account request.
- Signal/Incident have bounded Langfuse observation and GitHub issue/PR imports with persisted window checkpoints, coverage, source-version dedupe, neutral inbox rows, revision-checked Incident actions, and operator-confirmed Signal attachment. Attachment updates Incident revision, Signal state, relationship table, and workspace events in one SQLite transaction. A narrow poll rule appends an exact external object's new version to its prior Incident only if that prior version was linked within 24 hours and there is exactly one matching active Incident in the same source binding. Ambiguous or older repeats stay in the inbox; no causal claim is made. Revision-checked merge/split move Incident signal links atomically, retain a merged source record, require reasons, and reject stale revisions, terminal records, or active investigations. A local single-operator principal can claim/release an active Incident and adjust its severity with reason, revision, idempotency, and audit; this is not team RBAC. A durable opt-in monitor polls only verified Langfuse/GitHub bindings, uses the same import/idempotency path, caps each attempt at five 100-row pages, resumes cursors after restart, backs off transient errors, and pauses on credential/permission/scope failures. A downtime gap over 24 hours is recorded instead of replaying unbounded history. The local scheduler and API have mock-provider tests; real provider accounts remain unverified. GitHub pagination remains best effort over mutable updated-time order. Broader automatic grouping, suppression, and routing are not implemented.
- A manual InvestigationRun still freezes Incident/Signal input, creates scoped Evidence, records operator Hypotheses and decisions, and can end inconclusive or with an operator-identified root cause. `single` runs create one investigator task with all frozen Evidence. Section9-owned `swarm` runs fan investigators out by source binding, create a blind challenge-seed task over frozen evidence, and require a separate review task to wait for both seed and investigation outputs. Tasks persist in additive schema v6, with scoped contexts, structured evidence-linked outputs, capability labels, bounded leases, and epoch fencing. Migration v7 adds budget reservations/provider usage; v8 adds one-time hashed environment-scoped Worker bearer credentials. Worker claim/context/renew/finish APIs authenticate the registered identity and server-held capabilities; the local operator can list, revoke, and rotate credentials. Expired task recovery reopens attempts that never reached provider dispatch; dispatch-committed attempts become outcome-unknown with the full reservation retained; known settled usage remains attached to a failed task whose result was not persisted. Execution never automatically resends an uncertain provider call; retry remains explicit. Local state-transition tests and mock transport cover these paths, but no real process was killed during an actual provider request. The UI configures a token limit and exposes a deliberate execute action. A completed task graph leaves its run active for operator judgment; it never asserts confirmed RCA or recovery. This is not parallel or independent multi-agent execution; it has no tool loop or dynamic planner, and the deterministic task planner is not an EvoMap service. Proposal approval, repair, and independent recovery validation are still absent.
- The local `/api/v1` surface is for a single-machine operator. It has no authenticated multi-user identity or production role model.
- Current official EvoMap public protocol discovery was reviewed on 2026-09-23 and is captured in [EVOMAP_VALIDATION.md](EVOMAP_VALIDATION.md). Native E2/E3 remains blocked: no node/session/task was created and no Hub call was made because an authorized test node and peer are not configured.
- Broader Signal grouping/suppression/routing, source-health alerting, authenticated team/worker identity and RBAC, model-backed task execution, dynamic task planning, independent verification, proposal approval, controlled execution, recovery validation, EvoMap native collaboration, and full parity/release acceptance remain open.

## Current verification run

The full backend run `.venv/bin/python -m pytest -vv` passed **330 tests in 270.09 s** with two dependency deprecation warnings. This includes Worker bearer identity/capability enforcement, token rotation/revocation, lease recovery before and after provider dispatch, and honest known/unknown usage retention. The focused regression set passed **49 tests in 13.91 s**. `cd frontend && npm run build` passed. `npx playwright test -c playwright.workspace.config.ts` passed **2 tests in 31.4 s**, including the mock-backed model investigation UI; `npm run test:e2e:m0` passed **3 tests in 19.3 s** with a production build. The workspace browser suite was adjusted to disambiguate an existing repeated GitHub issue label; no provider request was made. Prior local Uvicorn/browser smoke and Incident lifecycle smoke are still as recorded in [STATUS.md](STATUS.md). No current live database was migrated, no external provider/account or model request was used. The dedicated >10,000-record fixed-watermark case had passed earlier in **116.93 s**.

Frontend production build passed:

```text
cd frontend && npm run build
```

## Next product work

1. Replace the deterministic initial task plan with model-proposed DAGs that receive server-side dependency, capability, scope, and budget validation; add a bounded read-tool loop and compare single investigator and swarm paths under equal evidence, tools, budgets, and time.
2. Add immutable ProposalVersion and revision/hash-bound human approval, then fenced execution, reconciliation of unknown outcomes, and independent recovery validation.
3. Continue M1 source-health/grouping work and keep real Langfuse/GitHub proof plus EvoMap E2/E3 as external gates. EvoMap E2 needs an authorized test node and independently authenticated peer; E3 needs approved bounded Hub read/write/revoke scope.

## External proof prerequisites

For a real, bounded read check, the user must configure the customer Langfuse keys/project ID or a least-privilege GitHub token with repository Contents read and Issues read permissions in the ignored local `.env`, and authorize the outbound read. Do not send secret values in chat. Native EvoMap collaboration requires its current authorized account/session. No paid model run should occur without a stated cumulative spend ceiling.
