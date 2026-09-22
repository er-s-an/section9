"""Operator controls and persisted experiment conditions must agree."""
from types import SimpleNamespace

import pytest

from s9.api import inject
from s9.contracts import Injection
from s9.store import Rejected, Store


@pytest.mark.asyncio
async def test_memory_muted_rejection_precedes_any_incident_or_config_change(tmp_path):
    store = Store(tmp_path / 'conditions.sqlite')
    store.set_muted(True)
    with store.tx() as db:
        store.set_meta(db, 'memory_enabled', True)
    before = store.current_config()
    calls = []
    async def forbidden(*args):
        calls.append(args)
        raise AssertionError('must reject before Core.inject')
    with pytest.raises(Rejected) as error:
        await inject(Injection(scenario='composite', condition='memory'), SimpleNamespace(store=store, inject=forbidden))
    assert error.value.code == 'UNSUPPORTED_CONDITION'
    assert error.value.status == 422
    assert not calls and not store.runs() and store.current_config() == before


def test_muted_injection_preserves_transport_policy_and_manifest(tmp_path):
    store = Store(tmp_path / 'conditions.sqlite')
    store.set_muted(True)
    with store.tx() as db:
        epoch = store.meta(db, 'transport_epoch')
    run = store.inject('composite', 'muted', 'component-test', 42, 16000)
    with store.tx() as db:
        assert store.meta(db, 'muted') is True
        assert store.meta(db, 'transport_epoch') == epoch
    assert run['condition'] == run['manifest']['condition'] == 'muted'
