#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

python3 - "$ROOT" <<'PY'
from pathlib import Path
import json
import subprocess
import sys
import tempfile

root = Path(sys.argv[1])
manifest_path = root / "firmware/tooling/packet-rssi-build-manifest.json"
toolchain_path = root / "firmware/tooling/qualified-toolchain.json"
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
toolchain = json.loads(toolchain_path.read_text(encoding="utf-8"))

assert manifest["schema"] == 1
assert manifest["phase"] == "0C-P2"
assert manifest["upstream"]["commit"] == "7ff74ed1ba663a282edcbbb5e0ec3d7132e6f2f5"
assert manifest["upstream"]["submodules"]["STM32F10X_Lib"] == "1debc23063f3942608e2bd62d04d5e1249c47fa3"
assert manifest["engineering"]["commit"] == "69309644da839522102e393e66093378544869ea"
assert manifest["branding"]["expected_identity"] == (
    "MMDVM_HS_Hat-YWD-1278-AX25R4-v0.1.0-alpha1 14.7456MHz "
    "ADF7021 FW based on CA6JAU GitID #7ff74ed"
)

assert toolchain["schema"] == 1
assert toolchain["phase"] == "FWM1"
assert toolchain["build_environment"]["distribution"] == "Debian trixie"
assert toolchain["build_environment"]["container_image"] == "debian:trixie-slim"
assert toolchain["build_environment"]["container_digest"] == "sha256:d7e12182ce18b85b93007c1dedf31f2d29e01ccf3182cc4017c709b6259bc132"
assert toolchain["build_environment"]["arm_gcc_full_version"] == "14.2.1"
assert toolchain["build_environment"]["packages"] == {
    "gcc-arm-none-eabi": "15:14.2.rel1-1",
    "binutils-arm-none-eabi": "2.44-3+23+b1",
    "libnewlib-arm-none-eabi": "4.5.0.20241231-1",
    "libnewlib-dev": "4.5.0.20241231-1",
    "libstdc++-arm-none-eabi-dev": "15:14.2.rel1-1+29",
    "libstdc++-arm-none-eabi-newlib": "15:14.2.rel1-1+29",
    "make": "4.4.1-2",
    "git": "1:2.47.3-0+deb13u1",
}
assert toolchain["qualified_artifact"]["size_bytes"] == 59892
assert toolchain["qualified_artifact"]["sha256"] == "b06fcbf0baa36e865198091cee27c66e1624ef08117ee685253a7a5613c7c616"
assert toolchain["qualified_artifact"]["identity"] == manifest["branding"]["expected_identity"]
assert toolchain["source_provenance"]["ywd_1278_core_commit"] == "c28c46c3478d7931af611923c92cd8f692a00858"
assert toolchain["source_provenance"]["qualified_builder_blob"] == "5abf2db22e462be844207eb776fed9bae242dcb3"
assert toolchain["source_provenance"]["upstream_mmdvm_hs_commit"] == manifest["upstream"]["commit"]
assert toolchain["source_provenance"]["stm32f10x_lib_commit"] == manifest["upstream"]["submodules"]["STM32F10X_Lib"]
assert toolchain["source_provenance"]["engineering_provenance_commit"] == manifest["engineering"]["commit"]
assert toolchain["qualification_context"]["reference_vs_inrepo_byte_identical"] is True
assert toolchain["safety"] == {"hardware_access": False, "flash_written": False, "rf_transmitted": False}

# Preserve the exact YWD-1278 tooling blobs that define the qualified build.
expected_tooling = {
    "firmware/build-qualified-inrepo.py": "5abf2db22e462be844207eb776fed9bae242dcb3",
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
print("FWM1_BUILDER_BLOB_EXACT=PASS")
print("FWM1_QUALIFIED_TOOLCHAIN_PROVENANCE=PASS")
print("FWM1_MATERIALIZER=PASS")
print("FWM1_PARALLEL_BUILDER_PRESENT=PASS")
print("PRODUCTION_FIRMWARE_BUILD_PATH_CHANGED=NO")
print("INSTALLER_FIRMWARE_PATH_CHANGED=NO")
print("HARDWARE_ACCESSED=NO")
print("FLASH_WRITTEN=NO")
print("RF_TRANSMITTED=NO")
PY
