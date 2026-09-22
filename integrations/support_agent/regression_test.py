"""Protected acceptance test for upstream support-agent regression 708fd1d.

The runner executes this file against two immutable upstream worktrees.  It is
kept outside the candidate checkout so the repair worker cannot relax it.
"""

from __future__ import annotations

from src.agents import base


class _FakeMessage:
    def __init__(self, content: str = "", tool_calls: list[dict] | None = None):
        self.content = content
        self.tool_calls = tool_calls or []


class _FakeTool:
    name = "echo_tool"

    def invoke(self, args):
        return f"echoed:{args['value']}"


class _FakeModel:
    def __init__(self):
        self.calls = 0

    def bind_tools(self, _tools):
        return self

    def invoke(self, _messages):
        self.calls += 1
        return _FakeMessage(content="", tool_calls=[{"name": "echo_tool", "args": {"value": "x"}, "id": str(self.calls)}])


def test_upstream_repeated_tool_call_has_non_empty_fallback(monkeypatch):
    model = _FakeModel()
    monkeypatch.setattr(base, "get_llm", lambda: model)
    result = base.run_tool_agent("echo", "Use the tool", [_FakeTool()], "hello")
    assert result["reply"] == "echoed:x"
    assert model.calls <= 3
