#!/usr/bin/env python3
"""Download a Section9 pair's read-only evidence export."""
from __future__ import annotations

import argparse
import pathlib
import urllib.parse
import urllib.request


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:9019')
    parser.add_argument('--pair-id', required=True)
    parser.add_argument('--output', type=pathlib.Path)
    args = parser.parse_args()
    name = f'{args.pair_id}-evidence.zip'
    output = args.output or pathlib.Path(name)
    url = f"{args.base_url.rstrip('/')}/api/pairs/{urllib.parse.quote(args.pair_id, safe='')}/export.zip"
    request = urllib.request.Request(url, headers={'Accept': 'application/zip'})
    with urllib.request.urlopen(request, timeout=30) as response:
        output.write_bytes(response.read())
    print(output)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
