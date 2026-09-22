from s9.api import _collaboration_event_history, _product_collaboration


def test_product_collaboration_uses_durable_event_history_after_live_window_rolls():
    state = {
        "agents": [{"id": "cost-agent", "role": "cost", "status": "idle", "capabilities": []}],
        "runs": [{"id": "memory-run", "reuseapproved": True, "memory_used_id": "seed_quality_prompt"}],
        "events": [{"sequence": "7000", "event_type": "reset.completed", "payload": {}}],
    }
    history = [
        {"run_id": "old-run", "event_type": "grant.rejected", "payload": {"code": "FENCE_STALE"}},
        {"run_id": "old-run", "event_type": "agent.resumed", "payload": {}},
        {"run_id": "old-run", "event_type": "dialog.dropped", "payload": {"reason": "MUTED"}},
        {"run_id": "old-run", "event_type": "dialog.received",
         "payload": {"generation": "1", "task_epoch": "2", "transport_epoch": "3"}},
    ]

    result = _product_collaboration(state, history)
    local = result["local"]
    assert local["expert_join"]["status"] == "ready"
    assert local["handoff"]["status"] == "ready"
    assert local["communication_isolation"]["status"] == "ready"
    assert local["structured_messages"]["status"] == "ready"
    assert local["experience_reuse"]["status"] == "ready"


def test_collaboration_history_window_is_bounded_and_starts_after_latest_sequence():
    class Store:
        def __init__(self):
            self.query = None

        def events(self, *, after, limit):
            self.query = (after, limit)
            return []

    class CoreStub:
        store = Store()

    core = CoreStub()
    assert _collaboration_event_history(core, {"events": [{"sequence": "7200"}]}) == []
    assert core.store.query == (2200, 5000)
