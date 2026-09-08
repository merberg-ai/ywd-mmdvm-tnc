#!/usr/bin/env python3
"""Build-only deterministic 0C-P2 AX25R4 RSSI firmware candidate.

This reconstructs the exact frozen AX25R3 engineering lineage from byte-pinned
files vendored inside YWD-1278, applies the one pinned AX25R4 RSSI telemetry
transform, applies product branding, and builds twice for byte-for-byte
reproducibility.

The original YWD-MMDVM commits remain manifest provenance only. No YWD-MMDVM
checkout, local sibling repository, or YWD-MMDVM network fetch is required.

It never opens a modem device, accesses GPIO, flashes firmware, or transmits RF.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "firmware" / "tooling" / "packet-rssi-build-manifest.json"
BRANDER = ROOT / "firmware" / "tooling" / "apply_packet_rssi_branding.py"
INSPECTOR = ROOT / "firmware" / "tooling" / "inspect_artifact.py"
MATERIALIZER = ROOT / "firmware" / "tooling" / "materialize_vendored_engineering.py"


def run(args: list[str], *, cwd: Path | None = None, capture: bool = False) -> str:
    if capture:
        return subprocess.check_output(args, cwd=cwd, text=True).strip()
    subprocess.check_call(args, cwd=cwd)
    return ""


def git(repo: Path, *args: str, capture: bool = True) -> str:
    return run(["git", "-C", str(repo), *args], capture=capture)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def require_tool(name: str) -> None:
    if shutil.which(name) is None:
        raise SystemExit(f"[FAIL] Missing build dependency: {name}")


def fetch_upstream(manifest: dict, seed: Path) -> None:
    upstream = manifest["upstream"]
    run(["git", "init", "-q", str(seed)])
    git(seed, "remote", "add", "origin", upstream["repository"], capture=False)

    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    subprocess.check_call(
        [
            "git",
            "-C",
            str(seed),
            "fetch",
            "--quiet",
            "--no-tags",
            "--depth=1",
            "origin",
            upstream["commit"],
        ],
        env=env,
    )
    git(seed, "checkout", "--quiet", "--detach", upstream["commit"], capture=False)
    git(seed, "submodule", "sync", "--quiet", "--recursive", capture=False)
    git(seed, "submodule", "update", "--init", "--recursive", capture=False)

    if git(seed, "rev-parse", "HEAD") != upstream["commit"]:
        raise RuntimeError("upstream checkout mismatch")
    submodule = seed / "STM32F10X_Lib"
    if git(submodule, "rev-parse", "HEAD") != upstream["submodules"]["STM32F10X_Lib"]:
        raise RuntimeError("STM32F10X_Lib submodule mismatch")

    pinned_files = {
        upstream["config_template"]: upstream["config_template_blob"],
        "version.h": upstream["version_blob"],
        "Makefile": upstream["makefile_blob"],
        upstream["build_script"]: upstream["build_script_blob"],
    }
    for rel, expected_blob in pinned_files.items():
        actual = run(["git", "-C", str(seed), "hash-object", rel], capture=True)
        if actual != expected_blob:
            raise RuntimeError(f"pinned upstream blob mismatch: {rel}")

    makefile = (seed / "Makefile").read_text(encoding="utf-8")
    if "CLK_DEF=8000000" not in makefile:
        raise RuntimeError("pinned upstream STM32 HSE default is not 8 MHz")
    config = (seed / upstream["config_template"]).read_text(encoding="utf-8")
    if "#define ADF7021_14_7456" not in config:
        raise RuntimeError("pinned HAT config does not select the 14.7456 MHz ADF7021 TCXO")
    if "#define SEND_RSSI_DATA" not in config:
        raise RuntimeError("pinned HAT config does not compile the ADF7021 RSSI readback path")

    print("PINNED_UPSTREAM_SOURCE=PASS")
    print("STM32_HSE_HZ=8000000")
    print("ADF7021_TCXO_HZ=14745600")
    print("SEND_RSSI_DATA=ENABLED")
    print("OSC_OVERRIDE=NO")


def build_one(
    *,
    label: str,
    seed: Path,
    work: Path,
    transforms: Path,
    manifest: dict,
    jobs: int,
) -> Path:
    src = work / label
    shutil.copytree(seed, src, symlinks=True)
    upstream = manifest["upstream"]
    build = manifest["build"]

    git(src, "reset", "--quiet", "--hard", upstream["commit"], capture=False)
    git(src, "clean", "-qfdx", capture=False)
    shutil.copy2(src / upstream["config_template"], src / "Config.h")

    for rel in manifest["engineering"]["transform_order"]:
        print(f"    transform: {rel}")
        run([sys.executable, str(transforms / rel), str(src)])

    run([sys.executable, str(BRANDER), str(src), "--manifest", str(MANIFEST)])

    env = dict(os.environ)
    env["SOURCE_DATE_EPOCH"] = git(seed, "show", "-s", "--format=%ct", "HEAD")
    env["TZ"] = "UTC"
    env["LC_ALL"] = "C"
    subprocess.check_call(["make", "-C", str(src), "clean"], env=env)
    # Deliberately never pass OSC=. The STM32 HSE remains the upstream 8 MHz
    # default while Config.h independently selects the 14.7456 MHz ADF7021 TCXO.
    subprocess.check_call(
        ["make", "-C", str(src), f"-j{jobs}", build["make_target"]],
        env=env,
    )

    artifact = src / build["binary_path"]
    if not artifact.is_file():
        raise RuntimeError(f"{label} did not produce {build['binary_path']}")
    run([sys.executable, str(INSPECTOR), str(artifact), "--manifest", str(MANIFEST)])
    return artifact


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=max(1, os.cpu_count() or 1))
    ap.add_argument("--single", action="store_true", help="skip the second reproducibility build")
    ap.add_argument("--keep-work", action="store_true")
    args = ap.parse_args()

    if os.geteuid() == 0:
        raise SystemExit("[FAIL] Firmware builds do not require root; run without sudo")
    if args.jobs < 1:
        raise SystemExit("[FAIL] --jobs must be positive")
    for tool in ("git", "make", "arm-none-eabi-gcc", "arm-none-eabi-g++", "arm-none-eabi-objcopy"):
        require_tool(tool)
    if not MANIFEST.is_file() or not BRANDER.is_file() or not INSPECTOR.is_file() or not MATERIALIZER.is_file():
        raise SystemExit("[FAIL] 0C-P2 build tooling is incomplete")

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if manifest["schema"] != 1 or manifest["phase"] != "0C-P2":
        raise RuntimeError("unexpected RSSI build manifest")
    if manifest["engineering"].get("source") != "vendored":
        raise RuntimeError("0C-P2 engineering source must be vendored in YWD-1278")
    if manifest["build"]["stm32_hse_hz"] != 8_000_000:
        raise RuntimeError("0C-P2 requires STM32 HSE 8 MHz")
    if manifest["rf"]["tcxo_hz"] != 14_745_600:
        raise RuntimeError("0C-P2 requires ADF7021 TCXO 14.7456 MHz")
    if manifest["build"]["osc_override"] is not False:
        raise RuntimeError("0C-P2 must not pass an OSC override")
    safety = manifest["safety"]
    if any(
        (
            safety["hardware_access"],
            safety["flash_enabled"],
            safety["option_bytes_permitted"],
            safety["rf_transmit_possible_during_build"],
        )
    ):
        raise RuntimeError("0C-P2 build manifest violates build-only safety boundary")
    if manifest["telemetry"]["carrier_threshold_selected"] is not False:
        raise RuntimeError("raw RSSI candidate must not preselect a carrier threshold")

    out_dir = ROOT / "firmware" / "out" / manifest["profile_id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="ywd1278-rssi-fwbuild."))
    try:
        transforms = work / "engineering"
        transforms.mkdir()
        run(
            [
                sys.executable,
                str(MATERIALIZER),
                "--manifest",
                str(MANIFEST),
                "--dest",
                str(transforms),
            ]
        )
        seed = work / "upstream"
        fetch_upstream(manifest, seed)

        print("\n=== YWD-1278 0C-P2 AX25R4 RSSI FIRMWARE BUILD ===")
        print(f"PROFILE={manifest['profile_id']}")
        print(f"EXPECTED_IDENTITY={manifest['branding']['expected_identity']}")
        print(f"ENGINEERING_PROVENANCE={manifest['engineering']['repository']}@{manifest['engineering']['commit']}")
        print("ENGINEERING_SOURCE=VENDORED_IN_YWD1278")
        print("ENGINEERING_EXTERNAL_REPO_REQUIRED=NO")
        print("HARDWARE_ACCESS=NO")
        print("FLASH_WRITTEN=NO")
        print("RF_TRANSMITTED=NO")

        a = build_one(
            label="build-a",
            seed=seed,
            work=work,
            transforms=transforms,
            manifest=manifest,
            jobs=args.jobs,
        )
        if not args.single:
            b = build_one(
                label="build-b",
                seed=seed,
                work=work,
                transforms=transforms,
                manifest=manifest,
                jobs=args.jobs,
            )
            if a.read_bytes() != b.read_bytes():
                raise RuntimeError("independent RSSI firmware builds are not byte-identical")
            print("REPRODUCIBLE_BUILDS=PASS")
        else:
            print("REPRODUCIBLE_BUILDS=SKIPPED_BY_OPERATOR")

        series = manifest["branding"]["product_series"]
        version = manifest["branding"]["firmware_version"]
        short = manifest["upstream"]["short_commit"]
        final = out_dir / f"MMDVM_HS_Hat-YWD-1278-{series}-v{version}-{short}-hse8m.bin"
        shutil.copy2(a, final)
        digest = sha256(final)
        size = final.stat().st_size

        metadata = {
            "schema": 1,
            "phase": "0C-P2",
            "profile_id": manifest["profile_id"],
            "artifact": str(final.relative_to(ROOT)),
            "artifact_size_bytes": size,
            "artifact_sha256": digest,
            "expected_identity": manifest["branding"]["expected_identity"],
            "upstream_commit": manifest["upstream"]["commit"],
            "engineering_source": "vendored",
            "engineering_baseline_commit": manifest["engineering"]["baseline_qualified_commit"],
            "engineering_rssi_commit": manifest["engineering"]["commit"],
            "stm32_hse_hz": 8_000_000,
            "adf7021_tcxo_hz": 14_745_600,
            "osc_override": False,
            "rssi_subcommand": 5,
            "carrier_threshold_selected": False,
            "hardware_access": False,
            "flash_written": False,
            "rf_transmitted": False,
            "option_bytes_written": False,
        }
        (out_dir / "build-metadata.json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

        print("YWD1278_0C_P2_RSSI_FIRMWARE_BUILD=PASS")
        print(f"ARTIFACT={final}")
        print(f"ARTIFACT_SIZE_BYTES={size}")
        print(f"ARTIFACT_SHA256={digest}")
        print("RSSI_SUBCOMMAND=0x05")
        print("RSSI_THRESHOLD_SELECTED=NO")
        print("ENGINEERING_EXTERNAL_REPO_REQUIRED=NO")
        print("HARDWARE_ACCESS=NO")
        print("FLASH_WRITTEN=NO")
        print("RF_TRANSMITTED=NO")
        print("OPTION_BYTES_WRITTEN=NO")
        return 0
    finally:
        if args.keep_work:
            print(f"BUILD_WORK_RETAINED={work}")
        else:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
