# Section9 workers

Each worker is an independent process and communicates only with the root
service's `/agent` API. Start one with:

```bash
S9_AGENT_TOKEN='issued-by-root' python -m s9.workers --id diagnoser --base-url http://127.0.0.1:9019
```

Valid ids are `sentry`, `diagnoser`, `fixer-a`, `fixer-b`, `verifier`, `cost`,
and `single`. The process reads the token only from `S9_AGENT_TOKEN`; it does
not load dotenv files, operator claims, databases, SSE streams, or raw logs.

The sentry submits a de-duplicated set of observed facts to `/agent/incidents`.
Other roles independently poll their capability task, claim it with its epoch,
and complete it through the collaboration API. A background heartbeat renews a
live lease. Pause changes the heartbeat status and stops renewal while retaining
any grant. If a fenced action is held during a pause, the worker waits and
sends the original grant after resume, allowing the server to reject a stale
grant; it does not obtain a replacement grant.

Workers check readiness before claiming: diagnosis, repair, and single wait for
`detection_pending` to clear; repair also waits for a delivered peer conclusion;
the verifier waits for `last_action`. Therefore a blocked worker does not claim
and complete a task merely to report missing evidence. Fixer B adds a short
standby poll delay so fixer A gets a natural lead while both remain autonomous.

Diagnoser evidence is exchanged through real challenge and synthesize messages.
Fixers need a diagnoser conclusion unless they are the `single` union role,
and their actions come from a model response validated by `/agent/plan` rather
than a scenario mapping. The model receives the role-projected config,
known-good policy, memory candidate, observations, peer messages, and the
allowed action schema. L0/L1 policy responses retain the generated plan and
lease while waiting, without another model call. The verifier calls
`/agent/verify` only when a `last_action` exists and reports a failed
verification as failed. Completion is best effort after reset or resolution;
rejected completion cannot terminate the worker process.

Component tests use `httpx.MockTransport` and run with `uv run pytest`.
Root service integration, real model responses, lease timing, and end-to-end
fencing remain to be exercised against the running Section9 server.

Communication ablation retains the repair capability. Muted workers may reason from their own projected observations; they cannot read peer messages, task results, operator APIs, local audit files or memory. Current live samples do not show an advantage for communication. MacOS Seatbelt and dedicated 9021 ingress enforce this boundary.
