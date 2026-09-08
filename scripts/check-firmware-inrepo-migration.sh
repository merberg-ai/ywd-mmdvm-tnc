#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

python3 - "$ROOT" <<'PY'
from pathlib import Path
import hashlib
import json
import subprocess
import sys
import tempfile

root = Path(sys.argv[1])
manifest_path = root / "firmware/tooling/packet-rssi-build-manifest.json"
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

assert manifest["schema"] == 1
assert manifest["phase"] == "0C-P2"
assert manifest["upstream"]["commit"] == "7ff74ed1ba663a282edcbbb5e0ec3d7132e6f2f5"
assert manifest["upstream"]["submodules"]["STM32F10X_Lib"] == "1debc23063f3942608e2bd62d04d5e1249c47fa3"
assert manifest["engineering"]["commit"] == "69309644da839522102e393e66093378544869ea"
assert manifest["branding"]["expected_identity"] == (
    "MMDVM_HS_Hat-YWD-1278-AX25R4-v0.1.0-alpha1 14.7456MHz "
    "ADF7021 FW based on CA6JAU GitID #7ff74ed"
)

# Preserve the exact YWD-1278 tooling blobs that define the qualified build.
expected_tooling = {
    "firmware/tooling/packet-rssi-build-manifest.json": "c74f13fe0ae3ee786833f0f7a737111829027301",
    "firmware/tooling/apply_packet_rssi_branding.py": "7bb4c158d3c63d49624a05c0339fbb8c43c401e7",
    "firmware/tooling/inspect_artifact.py": "6808d5a28fb033a6f916a014a0db90aad5ccc589",
    "firmware/tooling/materialize_vendored_engineering.py": "7b05b395600c1d59c46d5a324488f337813c1542",
}
for rel, expected in expected_tooling.items():
    actual = subprocess.check_output(["git", "hash-object", str(root / rel)], text=True).strip()
    assert actual == expected, (rel, expected, actual)

vendor_root = root / manifest["engineering"]["vendored_root"]
assert vendor_root.is_dir()
for rel, expected in manifest["engineering"]["files"].items():
    path = vendor_root / rel
    assert path.is_file(), rel
    actual = subprocess.check_output(["git", "hash-object", str(path)], text=True).strip()
    assert actual == expected, (rel, expected, actual)

# Exercise the copied materializer itself: it must re-hash all 13 engineering
# inputs and materialize only verified bytes into a clean destination.
with tempfile.TemporaryDirectory(prefix="ywd-tnc-fwm1-") as td:
    subprocess.check_call([
        sys.executable,
        str(root / "firmware/tooling/materialize_vendored_engineering.py"),
        "--manifest", str(manifest_path),
        "--dest", td,
    ])

builder = (root / "firmware/build-qualified-inrepo.py").read_text(encoding="utf-8")
assert 'ROOT / "firmware" / "tooling" / "packet-rssi-build-manifest.json"' in builder
assert 'ROOT / "firmware" / "tooling" / "materialize_vendored_engineering.py"' in builder
assert 'git(seed, "submodule", "update", "--init", "--recursive"' in builder
assert 'if os.geteuid() == 0' in builder
assert 'independent RSSI firmware builds are not byte-identical' in builder

# FWM1 is parallel-only: the physically-qualified production installer/build
# path must still point at the pinned YWD-1278 submodule until equivalence is
# proven and a later cutover phase is explicitly authorized.
production_build = (root / "firmware/build.sh").read_text(encoding="utf-8")
assert 'CORE="$ROOT/vendor/ywd-1278"' in production_build
assert 'build-packet-rssi-ywd1278.py' in production_build

print("FWM1_ENGINEERING_BLOBS_EXACT=PASS")
print("FWM1_TOOLING_BLOBS_EXACT=PASS")
print("FWM1_MATERIALIZER=PASS")
print("FWM1_PARALLEL_BUILDER_PRESENT=PASS")
print("PRODUCTION_FIRMWARE_BUILD_PATH_CHANGED=NO")
print("INSTALLER_FIRMWARE_PATH_CHANGED=NO")
print("HARDWARE_ACCESSED=NO")
print("FLASH_WRITTEN=NO")
print("RF_TRANSMITTED=NO")
PY
