from dataclasses import dataclass
from pathlib import Path

from s9 import config
from s9.core import Core
from s9.model import ModelClient
from s9.pairs.contracts import WORKERS
from s9.store import Store
from s9.victim import VictimApp


@dataclass(frozen=True)
class RuntimeContext:
    pair_id: str
    run_id: str
    arm: str
    spec_hash: str
    data_dir: Path

    @property
    def scope(self):
        return {k: getattr(self, k) for k in ('pair_id', 'run_id', 'arm', 'spec_hash')}


class ArmRuntime:
    def __init__(self, context, baseline, *, gateway, telemetry, identity, spec=None):
        self.context = context
        self.spec = spec or {}
        self.store = Store(context.data_dir / 'runtime.sqlite', scope=context.scope, baseline_config=baseline)
        self.gateway, self.telemetry, self.identity = gateway, telemetry, identity
        self.core = None

    def prepare_core(self):
        if self.core is None:
            model = ModelClient(self.store, self.telemetry, gateway=self.gateway, scope=self.context.scope)
            self.core = Core(data_dir=self.context.data_dir, store=self.store, model=model, scope=self.context.scope,
                             worker_ids=WORKERS[self.context.arm], agent_port=config.PORT + 2,
                             telemetry=self.telemetry, identity=self.identity)
            self.core.victim = VictimApp(model, self.store.current_config, self.core.emit,
                                         asset_snapshot=self.spec.get('fixture_snapshot'))
            self.core.execution_enabled = False
        return self.core

    async def stop(self):
        if self.core:
            core, self.core = self.core, None
            await core.stop()
