# Store adversarial review

`tests/test_store_adversarial.py` exercises authority boundaries directly with
`Store(tmp_path)` and real observation events. It covers autonomy policy,
capability and lease fencing, reset generation, plan and grant binding,
idempotency, independent verification, and muted message delivery.

The tests intentionally assert the audit trail for rejected write attempts.
If a rejection is not recorded as an `action.rejected` or `grant.rejected`
event, the test fails rather than weakening the assertion. Run the focused
review with:

```bash
uv run pytest -q tests/test_store_adversarial.py
```

The review is a component-level authority check. It does not claim HTTP,
FastAPI authentication middleware, model-provider behavior, or multi-process
timing has been verified.

Current focused run (with `PYTHONPATH=.` because this checkout is not installed
as a package) is **8 passed, 2 failed**. The failures are deliberate findings:

* `grant()` rejects L0 with `POLICY_DENIED` but does not append a
  `grant.rejected` (or equivalent) audit event.
* A plan's stored action list can be mutated after grant creation while its
  stored hash remains unchanged; `execute()` then applies the mutated action.
  The mutated-plan test fails because the expected `GRANT_MISMATCH` is absent.
* The idempotency replay and conflict paths pass: the same valid request
  replays once, while a different valid plan/grant using the same key is
  rejected without a second mutation.
