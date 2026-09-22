#!/usr/bin/env python3
"""Acquire pinned upstream character sheets locally, without redistributing them."""
from pathlib import Path
from hashlib import sha256
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
COMMIT = "f29c107e9728a72f2635f10b4e8203b29b37221d"
HASHES = [
    "66eda7c39f233f7e1fd74696f4737a3e1ec51de0a63575f4616fb1646c74b254",
    "43ab6870067f634320a7090210de948b456ef231b50dca37e535c1144873ed37",
    "5f2f627a8f654987f952f32bb4e5b283555a06641f1e57faa206d198cd046825",
    "0f14865c3e1f89efd7172b1a6fef7146bae5374d94a8fb6e45ce10a59dbedd0e",
]


def main():
    directory = ROOT / "frontend/public/vendor/star-office"
    directory.mkdir(parents=True, exist_ok=True)
    for index, expected in enumerate(HASHES, 1):
        name = f"guest_anim_{index}.webp"
        destination = directory / name
        if destination.exists() and sha256(destination.read_bytes()).hexdigest() == expected:
            continue
        url = f"https://raw.githubusercontent.com/ringhyacinth/Star-Office-UI/{COMMIT}/frontend/{name}"
        with urlopen(url, timeout=30) as response:
            data = response.read(1024 * 1024)
        if sha256(data).hexdigest() != expected:
            raise RuntimeError(f"Asset checksum mismatch: {name}")
        destination.write_bytes(data)
    print("Office character assets: 4 pinned local files verified")


if __name__ == "__main__":
    main()
