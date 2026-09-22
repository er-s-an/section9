from typing import Literal

from s9 import config
from s9.contracts import StrictModel
from s9.store import DEFAULT_CONFIG, digest

Arm = Literal['swarm', 'baseline']
ARMS = ('swarm', 'baseline')
WORKERS = {'swarm': ['sentry', 'diagnoser', 'fixer-a', 'fixer-b', 'verifier'], 'baseline': ['sentry', 'single']}
FAULTS = {
    'prompt': {'prompt_version': 'degraded'},
    'cost': {'context_multiplier': 8, 'max_output_tokens': 4096},
    'loop': {'retry_limit': 6, 'retry_on_terminal': True},
    'composite': {'prompt_version': 'degraded', 'retry_limit': 6, 'retry_on_terminal': True},
}


class CreatePair(StrictModel):
    scenario: Literal['prompt', 'cost', 'loop', 'composite']
    seed: int = 42
    previous_pair_id: str | None = None


class StartPair(StrictModel):
    expected_spec_hash: str


class EmptyRequest(StrictModel):
    pass


def content_config(value):
    return {k: value[k] for k in DEFAULT_CONFIG}


def freeze_spec(request: CreatePair, identity: dict) -> dict:
    # Values are resolved on the server once. The create API does not offer
    # per-arm overrides, mutable references or arbitrary filesystem paths.
    fixture = {str(p.relative_to(config.ROOT / 'assets/xiaozhi')): p.read_text()
               for p in sorted((config.ROOT / 'assets/xiaozhi').rglob('*')) if p.is_file()}
    return {
        'schema_version': '1', 'scenario': request.scenario, 'seed': request.seed,
        'seed_scope': 'reserved_for_fixture_selection; current fixture has one deterministic variant',
        'provider_seed_supported': False, 'model': config.MODEL,
        'baseline_config_snapshot': dict(DEFAULT_CONFIG), 'baseline_config_hash': digest(DEFAULT_CONFIG),
        'fault_spec': dict(FAULTS[request.scenario]), 'fault_hash': digest(FAULTS[request.scenario]),
        'fixture_version': 'xiaozhi-s9-v1', 'fixture_hash': identity['fixture_hash'], 'fixture_snapshot': fixture,
        'acceptance_contract': {'version': '2', 'suite': 'xiaozhi-heldout-v2',
                                'required_checks': ['heldout_semantic_policy', 'heldout_outside_return_window',
                                                    'unaffected_product_fact', 'terminal_tool_stops', 'cost_budget',
                                                    'no_stalled_requests', 'revision_unchanged_at_close', 'whole_run_budget']},
        'acceptance_contract_hash': identity['acceptance_contract_hash'],
        'budget_policy': {'total_tokens_per_arm': config.RUN_TOKEN_BUDGET, 'max_concurrency_per_arm': config.MODEL_CONCURRENCY,
                          'provider_timeout_ms': config.MODEL_TIMEOUT * 1000, 'run_timeout_ms': config.RUN_TIMEOUT * 1000,
                          'unknown_usage_policy': 'retain_reservation', 'reservation': 'max(100,characters//2)+max_output'},
        'model_parameters': {'temperature': 0, 'thinking': {'type': 'disabled'}, 'stream': False, 'worker_max_tokens': 1600,
                             'schema_attempt_limit': 2, 'provider_retries': 0},
        'memory_policy': {'mode': 'off', 'input_snapshot_hash': None, 'output': 'separate_arm_local_GEP_journal'},
        'algorithms': {'swarm': WORKERS['swarm'], 'baseline': WORKERS['baseline'],
                       'baseline_reasoning_agents': 1, 'sentry_and_verification': 'deterministic_infrastructure'},
        'data_access': 'baseline receives union of raw role facts; no cross-arm conclusions or operator reads',
        'tools': ['set_prompt_revision', 'apply_context_budget', 'apply_retry_policy', 'apply_config_bundle', 'rollback_action'],
        'scheduler': {'policy': 'round_robin_among_queued_scopes', 'shared_capacity': config.MODEL_CONCURRENCY,
                      'idle_slots': 'borrowable', 'timing': 'wall time includes queue; provider duration separately recorded'},
        'autonomy': 'L2', 'intervention_policy': 'reset interrupts comparison; no in-place rerun or spec changes',
        'source_identity': identity, 'hardware_isolation': False, 'provider_cache': 'not_controlled',
    }
