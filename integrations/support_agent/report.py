"""Offline manifest report: ``python -m integrations.support_agent.report``."""

import json

from .manifest import get_manifest


def main() -> int:
    print(json.dumps(get_manifest(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
