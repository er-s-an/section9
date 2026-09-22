# Offline capability proof

Run `python3 scripts/offline-check.py` on macOS. The script starts the real
`s9.api:app` in a temporary Seatbelt profile with `S9_PORT=9034` and the
agent ingress at `9036`, using `data/offline-proof` for its isolated database
and runtime files. It does not change system firewall settings or touch other
services.

The profile permits loopback service traffic only. The configured EvoMap model
URL remains unchanged, so the real `/api/chat` request attempts the configured
remote provider and is rejected by the operating-system sandbox. The script
then reads `/api/state` and `/api/playbooks`, records the degraded model state,
HTTP business error, and unknown usage count in
`artifacts/offline/report.json`, and terminates only the process group it
started.

The current macOS run completed the service startup and recorded the real
boundary in `artifacts/offline/report.json`: `/api/state` returned HTTP 200,
`/api/playbooks` remained readable, and `/api/chat` returned HTTP 200 with
business status `error` and `MODEL_ERROR`/`ConnectError`. The authoritative
state reported the model as `degraded`, `remote_inference: true`, and the
database retained `usage_unknown: 1`. The configured remote URL was attempted
by the real model client; Seatbelt blocked the non-loopback connection. The
main 9019/9024 services were left running and only the probe's 9034 process
group was terminated.

This check demonstrates the boundary and expected failure mode. It does not
claim that Section9 supports offline model inference; a provider or local
model is still required for successful chat responses.
