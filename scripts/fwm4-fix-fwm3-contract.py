#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
p = ROOT / "scripts/check-runtime-inrepo-migration.sh"
data = p.read_text(encoding="utf-8")
old = '''pyproject=(root/"pyproject.toml").read_text(encoding="utf-8")
assert 'version = "0.1.0a11"' in pyproject
assert 'include = ["ywdtnc*", "ywd1278*"]' in pyproject
assert '__version__ = "0.1.0a11"' in (root/"src/ywdtnc/__init__.py").read_text(encoding="utf-8")
'''
new = '''pyproject=(root/"pyproject.toml").read_text(encoding="utf-8")
import tomllib
project_version=tomllib.loads(pyproject)["project"]["version"]
assert 'include = ["ywdtnc*", "ywd1278*"]' in pyproject
assert f'__version__ = "{project_version}"' in (root/"src/ywdtnc/__init__.py").read_text(encoding="utf-8")
'''
if old in data:
    data = data.replace(old, new, 1)
elif new not in data:
    raise SystemExit("FWM3 version-contract anchor not found")
p.write_text(data, encoding="utf-8")
print("FWM3_VERSION_CONTRACT_FUTURE_SAFE=PASS")
