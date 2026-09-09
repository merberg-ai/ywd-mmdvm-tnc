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
profile_path = root / "firmware/product-ax25r4.json"
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
toolchain = json.loads(toolchain_path.read_text(encoding="utf-8"))
profile = json.loads(profile_path.read_text(encoding="utf-8"))

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

# Preserve the exact FWM1 tooling blobs that define the qualified build.
expected_tooling = {
    "firmware/build-qualified-inrepo.py": "5abf2db22e462be844207eb776fed9bae242dcb3",
    "firmware/tooling/packet-rssi-build-manifest.json": "c74f13fe0ae3ee786833f0f7a737111829027301",
    "firmware/tooling/qualified-toolchain.json": "56d265cd2523dcdb7fd5f3634142e8c6407bef73",
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
with tempfile.TemporaryDirectory(prefix="ywd-tnc-fwm2-") as td:
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

# FWM2 production firmware-build cutover: build.sh must consume only the
# in-repo qualified builder/tooling/engineering inputs. The YWD-1278 submodule
# remains available to the modem runtime and flash-support path in this phase,
# but must no longer be a firmware build input.
production_build = (root / "firmware/build.sh").read_text(encoding="utf-8")
assert 'BUILDER="$ROOT/firmware/build-qualified-inrepo.py"' in production_build
assert 'TOOLCHAIN="$ROOT/firmware/tooling/qualified-toolchain.json"' in production_build
assert 'firmware/build-qualified-inrepo.py' in production_build
assert 'firmware/tooling' in production_build
assert 'firmware/vendor' in production_build
assert 'sudo -H -u "$build_user" -- python3 "$build_root/firmware/build-qualified-inrepo.py"' in production_build
assert 'FIRMWARE_BUILD_SOURCE=IN_REPO' in production_build
assert 'YWD1278_FIRMWARE_BUILDER_INVOKED=NO' in production_build
assert 'build-packet-rssi-ywd1278.py' not in production_build
assert 'CORE="$ROOT/vendor/ywd-1278"' not in production_build
assert 'archive --format=tar HEAD' not in production_build

assert profile["vendor_build_script"] == "firmware/build-qualified-inrepo.py"
assert profile["artifact_relative_path"].startswith("firmware/out/0c-p2-rssi-ax25r4-")
assert profile["artifact_size_bytes"] == 59892
assert profile["artifact_sha256"] == toolchain["qualified_artifact"]["sha256"]
assert profile["firmware_engineering_manifest"] == "firmware/tooling/packet-rssi-build-manifest.json"
assert profile["qualified_toolchain_manifest"] == "firmware/tooling/qualified-toolchain.json"

# Runtime composition and HAT-support location are outside FWM2's firmware-
# build qualification. Later isolated phases may migrate those paths without
# changing the exact FWM2 builder/tooling/engineering inputs asserted above.

print("FWM2_ENGINEERING_BLOBS_EXACT=PASS")
print("FWM2_TOOLING_BLOBS_EXACT=PASS")
print("FWM2_BUILDER_BLOB_EXACT=PASS")
print("FWM2_QUALIFIED_TOOLCHAIN_PROVENANCE=PASS")
print("FWM2_MATERIALIZER=PASS")
print("FWM2_PRODUCTION_FIRMWARE_BUILD_SOURCE=IN_REPO")
print("FWM2_YWD1278_FIRMWARE_BUILDER_REACHABLE_FROM_BUILD_SH=NO")
print("FWM2_RUNTIME_COMPOSITION_OUT_OF_SCOPE=YES")
print("FWM2_FIRMWARE_BUILD_CONTRACT_PRESERVED=PASS")
print("INSTALLER_FLASH_BEHAVIOR_CHANGED=NO")
print("HARDWARE_ACCESSED=NO")
print("FLASH_WRITTEN=NO")
print("RF_TRANSMITTED=NO")
PY
