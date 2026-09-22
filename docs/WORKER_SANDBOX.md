# Worker sandbox command

` s9.sandbox.worker_command(python, root, agent_port, worker_args) ` builds a
macOS Seatbelt command for a worker process. The passed interpreter string is
kept unchanged, so `.venv/bin/python` continues to resolve its normal
site-packages. The profile separately permits the interpreter's resolved
realpath.

The profile permits Python runtime/stdlib files, the virtualenv, and the worker
module plus package initializer. It denies project `.env`, `CLAIMS.md`, data,
logs, memory, infra secrets, root config/core/api modules, all writes, and
command execution. Outbound TCP is restricted to the caller-provided localhost
port (normally `9021`). It does not grant external network access or inbound
listening.

The function raises on non-macOS instead of silently returning an unsandboxed
command. Use the returned list with `subprocess` or the process supervisor;
the profile is inline and does not create a persistent system setting.

Component tests use the host's real `/usr/bin/sandbox-exec` when available and
verify that a worker imports its runtime dependencies, fails without
`S9_AGENT_TOKEN`, and cannot import `s9.config`. Root should run the real
worker against the dedicated localhost `9021` agent API before release.
