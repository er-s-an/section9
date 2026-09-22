# Section 9 evaluation CLI

`scripts/evaluate.py` collects real runs from the local API. Its FR7 default
evaluates the four enabled conditions (`single`, `muted`, `swarm`, `memory`)
across the four scenarios (`prompt`, `cost`, `loop`, `composite`), for 16
runs. The fifth FR7 row, `memory_jev`, remains in the summary as `待测` with
`disabled_reason: no_real_model_artifact_jev_p2_disabled`; it is never replaced
by another condition. `single_memory` is available only as an explicit
supplemental `--conditions single_memory` run and cannot replace an FR7 row.
Each matrix cell can be repeated with `--repeats N`; `--max-runs` defaults to
20 and bounds the total number of runs. Unvisited cells remain in
`summary.json` as `待测`.

Example:

```bash
python scripts/evaluate.py --repeats 2 --max-runs 20 \
  --base-url http://127.0.0.1:9019 --output artifacts/evaluation
```

Before each run the CLI calls `/api/reset`, sets autonomy to `L2`, and sends
`POST /api/evaluate` with the same `{scenario, condition, seed: 42}` body as
the injection API. It polls the returned run until `resolved`, `failed`, or
`reset`, with a 210-second bound. It performs one final reset even when a
request fails. Requests use `trust_env=False` so local traffic bypasses the
macOS proxy environment.

Every completed run is saved under `runs/<run_id>.json`, including the full
run detail, events, usage, and manifest. Request failures are also saved as
`runs/error-*.json`; they count as failures and are never retried or dropped.
The summary records success/failure, median elapsed time, known tokens,
unknown-usage count, run ids, and checks that each manifest declares
`environment: evaluation`. Manifest baselines are grouped by scenario and
compare model, token budget, concurrency, fixture version, and the recorded
memory snapshot while ignoring condition and scenario as identity fields.
Memory versus non-memory snapshot differences are the treatment under test and
are called out without being labelled unfair. Different conditions are also
deliberately marked incomparable; the report does not claim a condition is
better merely because its observed sample differs.

This task does not execute a batch by itself. Running the command is an
explicit operator action against a live model-backed server.
