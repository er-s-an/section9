# Local GEP memory boundary

Section 9 keeps a local, append-only memory of repairs. Four seeded playbooks
are candidates only: `quality_mismatch`, `cost_high`, `tool_stalled`, and a
`quality_mismatch` + `tool_stalled` composite. Matching uses the observed signal codes and prefers a
candidate whose complete signal set is largest; it never reads a scenario or a
fixture to infer a match.

`MemoryStore.record_success` is an evidence gate. The caller must provide a
run with an explicit passed verification result, a run id, symptoms, and the
model used for that run. Failed, unverified, or model-less records are
rejected. A learned playbook is keyed by its symptoms and Action values. Every
accepted verification increments `success_count`; `reuse_count` increments
only when the root supplies the selected candidate's `memory_used_id`, sets
`reuseapproved`, and the executed Actions exactly match that candidate.

Each accepted result creates a protocol `Gene`, `Capsule`, and
`EvolutionEvent`. `integrations/gep/asset.mjs` calls the official
`@evomap/gep-sdk@1.14.0` `computeAssetId` and `verifyAssetId` helpers. Python
then validates each object against the JSON Schema shipped by that same SDK.
The three immutable assets are appended to `events.jsonl`; playbook indexes
are updated with an atomic replace and fsync. A protocol or schema failure
prevents the write and is surfaced in `status().validator_error`.

This implementation is local-only. It does not register with, publish to, or
claim a receipt from EvoMap Hub. Every playbook and result reports
`publish_state: local_only`; status also exposes `pending_auth: true` after a
local event exists. No credential files are read.
