import runpy
from pathlib import Path


probe_usage_tokens = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts" / "product-closure.py"))[
    "probe_usage_tokens"
]


def test_reported_zero_usage_is_unknown():
    step = {"body": {"evidence": {"value": {"response": {"usage": {"total_tokens": 0}}}}}}
    assert probe_usage_tokens(step) is None


def test_positive_usage_is_counted():
    step = {"body": {"evidence": {"value": {"response": {"usage": {"total_tokens": 602}}}}}}
    assert probe_usage_tokens(step) == 602


def test_observe_usage_is_read_from_wrapped_probe():
    step = {"body": {"probe": {"evidence": {"value": {"response": {"usage": {"total_tokens": 781}}}}}}}
    assert probe_usage_tokens(step) == 781
