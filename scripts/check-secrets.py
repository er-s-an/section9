#!/usr/bin/env python3
"""Check prospective Git files without printing protected secret values."""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
secrets = []
for file in [ROOT / ".env", ROOT / "infra/.env"]:
    for line in file.read_text().splitlines() if file.exists() else []:
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        name, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        if any(word in name.upper() for word in ["KEY", "PASSWORD", "SECRET", "TOKEN"]) and len(value) >= 12:
            secrets.append(value.encode())
names = subprocess.check_output(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=ROOT).split(b"\0")
findings = []
checked = 0
for name in names:
    if not name:
        continue
    file = ROOT / name.decode()
    if not file.is_file():
        continue
    data = file.read_bytes()
    checked += 1
    if any(value in data for value in secrets):
        findings.append(str(file.relative_to(ROOT)))
if findings:
    print("FAIL: protected value found in: " + ", ".join(findings))
    sys.exit(1)
print(f"PASS: {checked} prospective repository files checked; no configured secrets found")
